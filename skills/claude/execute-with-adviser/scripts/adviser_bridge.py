#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Thin, stateless executor-to-adviser JSON bridge."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Any, Callable, Protocol


PROTOCOL_VERSION = "executor-adviser/0.2"
TRIGGERS = {"critical-fact", "failure", "ambiguity", "result-review"}
TRIGGER_SOURCES = {"automatic", "user", "pilot"}
REVIEW_SCOPES = {"summary-only", "evidence-read"}
REQUEST_STATES = {"submitted"}
RESPONSE_STATES = {"completed", "input-required", "rejected", "failed", "timeout"}
DECISIONS = {"proceed", "change", "stop", "need-evidence"}
CLASSIFICATIONS = {"public", "private", "restricted"}
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
MAX_CONSULT_ROUNDS = 3
EventSink = Callable[[dict[str, Any]], None]

SKILL_DIR = Path(__file__).resolve().parents[1]
RESPONSE_SCHEMA_PATH = SKILL_DIR / "references" / "response.schema.json"
DEFAULT_BACKEND = "codex"
DEFAULT_CODEX_MODEL = "gpt-6-sol"
DEFAULT_CODEX_EFFORT = "xhigh"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_EFFORT = "xhigh"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_CLAUDE_HEALTH_CHECK_TIMEOUT_SECONDS = 15.0
EVIDENCE_READ_CLAUDE_TOOLS = ("Read",)


class ProtocolError(ValueError):
    """The request or response violates the protocol contract."""


class BackendError(RuntimeError):
    """The adviser process failed."""


class BackendTimeout(BackendError):
    """The adviser process exceeded its deadline."""


class MalformedResponse(BackendError):
    """The adviser returned data that cannot be decoded as a response."""


class AdviserBackend(Protocol):
    def invoke(
        self,
        request: dict[str, Any],
        *,
        transport_attempt: int = 1,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        """Return one unvalidated adviser response."""

        ...


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _require_string(
    obj: dict[str, Any], key: str, *, max_length: int, allow_empty: bool = False
) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"{key} must be a string")
    if not allow_empty and not value.strip():
        raise ProtocolError(f"{key} must not be empty")
    if len(value) > max_length:
        raise ProtocolError(f"{key} exceeds {max_length} characters")
    return value


def _reject_unknown(obj: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ProtocolError(f"{label} has unknown field(s): {', '.join(unknown)}")


def _validate_string_list(
    value: Any, label: str, *, max_items: int, max_length: int
) -> list[str]:
    if not isinstance(value, list):
        raise ProtocolError(f"{label} must be an array")
    if len(value) > max_items:
        raise ProtocolError(f"{label} exceeds {max_items} items")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ProtocolError(f"{label}[{index}] must be a non-empty string")
        if len(item) > max_length:
            raise ProtocolError(f"{label}[{index}] exceeds {max_length} characters")
    return value


def validate_request(payload: Any) -> dict[str, Any]:
    request = _require_object(payload, "request")
    allowed = {
        "protocol",
        "id",
        "context_id",
        "state",
        "trigger",
        "trigger_event",
        "review_scope",
        "goal",
        "question",
        "evidence",
        "attempts",
        "constraints",
        "executor_position",
        "fallback",
        "data_classification",
        "consult_index",
        "consult_limit",
    }
    _reject_unknown(request, allowed, "request")
    missing = sorted(allowed - set(request))
    if missing:
        raise ProtocolError(f"request is missing field(s): {', '.join(missing)}")

    if request["protocol"] != PROTOCOL_VERSION:
        raise ProtocolError(f"protocol must be {PROTOCOL_VERSION}")
    _require_string(request, "id", max_length=128)
    _require_string(request, "context_id", max_length=128)
    if request["state"] not in REQUEST_STATES:
        raise ProtocolError("state must be submitted")
    if request["trigger"] not in TRIGGERS:
        raise ProtocolError(f"unsupported trigger: {request['trigger']!r}")
    trigger_event = _require_object(request["trigger_event"], "trigger_event")
    trigger_fields = {
        "event",
        "event_id",
        "trigger",
        "trigger_source",
        "condition",
        "blocking",
        "evidence_ready",
    }
    _reject_unknown(trigger_event, trigger_fields, "trigger_event")
    missing_trigger = sorted(trigger_fields - set(trigger_event))
    if missing_trigger:
        raise ProtocolError(
            f"trigger_event is missing field(s): {', '.join(missing_trigger)}"
        )
    if trigger_event["event"] != "advice-triggered":
        raise ProtocolError("trigger_event.event must be advice-triggered")
    _require_string(trigger_event, "event_id", max_length=128)
    if trigger_event["trigger"] != request["trigger"]:
        raise ProtocolError("trigger_event.trigger must match request trigger")
    if trigger_event["trigger_source"] not in TRIGGER_SOURCES:
        raise ProtocolError(
            f"unsupported trigger_source: {trigger_event['trigger_source']!r}"
        )
    _require_string(trigger_event, "condition", max_length=2000)
    for key in ("blocking", "evidence_ready"):
        if not isinstance(trigger_event[key], bool):
            raise ProtocolError(f"trigger_event.{key} must be a boolean")
    if request["review_scope"] not in REVIEW_SCOPES:
        raise ProtocolError(f"unsupported review_scope: {request['review_scope']!r}")
    _require_string(request, "goal", max_length=4000)
    _require_string(request, "question", max_length=4000)
    _require_string(request, "executor_position", max_length=4000)
    _require_string(request, "fallback", max_length=2000)

    classification = request["data_classification"]
    if classification not in CLASSIFICATIONS:
        raise ProtocolError(f"unsupported data_classification: {classification!r}")

    index = request["consult_index"]
    limit = request["consult_limit"]
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or not 1 <= index <= MAX_CONSULT_ROUNDS
    ):
        raise ProtocolError(
            f"consult_index must be an integer from 1 to {MAX_CONSULT_ROUNDS}"
        )
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_CONSULT_ROUNDS
    ):
        raise ProtocolError(
            f"consult_limit must be an integer from 1 to {MAX_CONSULT_ROUNDS}"
        )
    if index > limit:
        raise ProtocolError("consult_index exceeds consult_limit")
    if trigger_event["trigger_source"] == "pilot" and (index != 1 or limit != 1):
        raise ProtocolError("pilot trigger requires consult_index=1 and consult_limit=1")

    evidence = request["evidence"]
    if not isinstance(evidence, list):
        raise ProtocolError("evidence must be an array")
    if len(evidence) > 24:
        raise ProtocolError("evidence exceeds 24 items")
    for index, raw_item in enumerate(evidence):
        item = _require_object(raw_item, f"evidence[{index}]")
        _reject_unknown(item, {"uri", "locator", "excerpt", "sha256"}, f"evidence[{index}]")
        if "uri" not in item or "locator" not in item:
            raise ProtocolError(f"evidence[{index}] requires uri and locator")
        _require_string(item, "uri", max_length=2000)
        _require_string(item, "locator", max_length=500)
        if "excerpt" in item:
            _require_string(item, "excerpt", max_length=6000, allow_empty=True)
        if "sha256" in item:
            digest = _require_string(item, "sha256", max_length=64)
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ProtocolError(f"evidence[{index}].sha256 must contain 64 hex characters")

    attempts = request["attempts"]
    if not isinstance(attempts, list):
        raise ProtocolError("attempts must be an array")
    if len(attempts) > 4:
        raise ProtocolError("attempts exceeds 4 items")
    for index, raw_item in enumerate(attempts):
        item = _require_object(raw_item, f"attempts[{index}]")
        _reject_unknown(item, {"method", "result", "raw_error", "ruled_out"}, f"attempts[{index}]")
        required = {"method", "result", "ruled_out"}
        missing_attempt = sorted(required - set(item))
        if missing_attempt:
            raise ProtocolError(
                f"attempts[{index}] is missing field(s): {', '.join(missing_attempt)}"
            )
        _require_string(item, "method", max_length=2000)
        _require_string(item, "result", max_length=4000)
        if "raw_error" in item:
            _require_string(item, "raw_error", max_length=8000, allow_empty=True)
        _validate_string_list(
            item["ruled_out"], f"attempts[{index}].ruled_out", max_items=16, max_length=1000
        )
    if request["trigger"] == "failure" and len(attempts) < 2:
        raise ProtocolError("failure trigger requires at least two attempts")
    if request["trigger"] in {"critical-fact", "result-review"} and not evidence:
        raise ProtocolError(f"{request['trigger']} trigger requires evidence")
    if request["trigger"] in {"critical-fact", "result-review"} and not trigger_event[
        "evidence_ready"
    ]:
        raise ProtocolError(f"{request['trigger']} trigger requires evidence_ready=true")

    _validate_string_list(request["constraints"], "constraints", max_items=24, max_length=1000)
    return request


def validate_response(payload: Any, request: dict[str, Any]) -> dict[str, Any]:
    response = _require_object(payload, "response")
    allowed = {
        "protocol",
        "id",
        "context_id",
        "review_scope",
        "evidence_checked",
        "state",
        "decision",
        "recommendation",
        "reason",
        "change_evidence",
        "executor_next_step",
    }
    _reject_unknown(response, allowed, "response")
    missing = sorted(allowed - set(response))
    if missing:
        raise ProtocolError(f"response is missing field(s): {', '.join(missing)}")
    if response["protocol"] != PROTOCOL_VERSION:
        raise ProtocolError(f"response protocol must be {PROTOCOL_VERSION}")
    if response["id"] != request["id"]:
        raise ProtocolError("response id does not match request id")
    if response["context_id"] != request["context_id"]:
        raise ProtocolError("response context_id does not match request context_id")
    if response["review_scope"] != request["review_scope"]:
        raise ProtocolError("response review_scope does not match request review_scope")
    evidence_checked = response["evidence_checked"]
    if not isinstance(evidence_checked, list):
        raise ProtocolError("evidence_checked must be an array")
    if len(evidence_checked) > 24:
        raise ProtocolError("evidence_checked exceeds 24 items")
    if len(evidence_checked) != len(set(evidence_checked)):
        raise ProtocolError("evidence_checked must not contain duplicates")
    allowed_evidence = {item["uri"] for item in request["evidence"]}
    for index, uri in enumerate(evidence_checked):
        if not isinstance(uri, str) or not uri.strip():
            raise ProtocolError(f"evidence_checked[{index}] must be a non-empty string")
        if len(uri) > 2000:
            raise ProtocolError(f"evidence_checked[{index}] exceeds 2000 characters")
        if uri not in allowed_evidence:
            raise ProtocolError(f"evidence_checked[{index}] is not listed in request evidence")
    if response["state"] not in RESPONSE_STATES:
        raise ProtocolError(f"unsupported response state: {response['state']!r}")
    if response["decision"] not in DECISIONS:
        raise ProtocolError(f"unsupported decision: {response['decision']!r}")
    _require_string(response, "recommendation", max_length=4000)
    _require_string(response, "reason", max_length=4000)
    _require_string(response, "change_evidence", max_length=3000)
    _require_string(response, "executor_next_step", max_length=3000)

    state = response["state"]
    decision = response["decision"]
    if (state == "input-required") != (decision == "need-evidence"):
        raise ProtocolError("input-required and need-evidence must appear together")
    if state in {"rejected", "failed", "timeout"} and decision != "stop":
        raise ProtocolError(f"{state} response must use stop decision")
    if request["review_scope"] == "summary-only" and evidence_checked:
        raise ProtocolError("summary-only response must use an empty evidence_checked array")
    if (
        request["review_scope"] == "evidence-read"
        and state == "completed"
        and not evidence_checked
    ):
        raise ProtocolError("completed evidence-read response must list evidence_checked")
    return response


def load_response_schema() -> dict[str, Any]:
    try:
        return json.loads(RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackendError(f"cannot load response schema: {exc}") from exc


CODEX_UNSUPPORTED_SCHEMA_KEYS = {
    "$id",
    "$schema",
    "title",
    "maxItems",
    "maxLength",
    "minLength",
    "uniqueItems",
}


def _codex_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_codex_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    converted: dict[str, Any] = {}
    for key, item in value.items():
        if key in CODEX_UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "const":
            converted["enum"] = [_codex_schema(item)]
        else:
            converted[key] = _codex_schema(item)
    return converted


def response_schema_for_request(
    request: dict[str, Any], *, claude_compatible: bool = False, codex_compatible: bool = False
) -> dict[str, Any]:
    if claude_compatible and codex_compatible:
        raise ProtocolError("schema compatibility modes are mutually exclusive")
    schema = load_response_schema()
    if claude_compatible:
        schema.pop("$schema", None)
        schema.pop("$id", None)
    if codex_compatible:
        schema = _codex_schema(schema)
    def fixed_string(value: str) -> dict[str, Any]:
        return {"type": "string", "enum" if codex_compatible else "const": [value] if codex_compatible else value}

    schema["properties"]["id"] = fixed_string(request["id"])
    schema["properties"]["context_id"] = {
        **fixed_string(request["context_id"]),
    }
    schema["properties"]["review_scope"] = {
        **fixed_string(request["review_scope"]),
    }
    evidence_uris = [item["uri"] for item in request["evidence"]]
    if evidence_uris:
        schema["properties"]["evidence_checked"]["items"] = {
            "type": "string",
            "enum": evidence_uris,
        }
    else:
        if codex_compatible:
            schema["properties"]["evidence_checked"]["items"] = {
                "type": "string",
                "enum": [],
            }
        else:
            schema["properties"]["evidence_checked"]["maxItems"] = 0
    return schema


def _has_http_evidence(request: dict[str, Any]) -> bool:
    return any(
        item["uri"].startswith(("http://", "https://"))
        for item in request["evidence"]
    )


def claude_tools_for_request(request: dict[str, Any]) -> str:
    # Summary-only is a hard no-tool mode. Prompt wording alone is not enough:
    # a model with Read/Grep/Glob can still spend the whole deadline exploring
    # the workspace before returning the required JSON response.
    if request["review_scope"] == "summary-only":
        return ""
    tools = list(EVIDENCE_READ_CLAUDE_TOOLS)
    if _has_http_evidence(request):
        tools.append("WebFetch")
    return ",".join(tools)


def build_adviser_prompt(
    request: dict[str, Any],
    *,
    include_schema: bool = False,
    retry_reason: str | None = None,
) -> str:
    envelope = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if request["review_scope"] == "summary-only":
        scope_instruction = (
            "Use only the supplied excerpts. This consultation has no tools; do not open "
            "evidence URIs. Return an empty evidence_checked array."
        )
    else:
        scope_instruction = (
            "You may read only the evidence URIs and locators listed in the request. Do not "
            "browse the wider workspace. Use only the smallest bounded read needed around "
            "each locator: never recursively search, use broad Grep/Glob discovery, or read "
            "an entire large/binary artifact. If the listed evidence cannot be checked in a "
            "bounded read, return state=input-required and decision=need-evidence. List "
            "every evidence URI actually opened in evidence_checked."
        )
        if _has_http_evidence(request):
            scope_instruction += (
                " For HTTP(S) evidence, use WebFetch only for the exact listed URLs; do not "
                "follow unrelated links or browse elsewhere."
            )
    prompt = (
        "Act only as a consult-only adviser. Do not execute the task, edit files, build, "
        "or reproduce the executor's work. Answer the single decision question from the "
        "evidence supplied. Prefer objective evidence over model opinion. If evidence is "
        "insufficient, return state=input-required and decision=need-evidence with the exact "
        "missing evidence. change_evidence must contain real content: state what evidence, if "
        "it existed, would change your recommendation; if none realistically would, say that "
        "explicitly instead of leaving the field empty or generic. Return only the "
        "schema-constrained response and preserve id, "
        f"context_id, protocol, and review_scope. {scope_instruction}\n\nREQUEST_JSON\n"
        + envelope
    )
    if include_schema:
        schema = json.dumps(
            response_schema_for_request(request),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt += "\n\nRESPONSE_JSON_SCHEMA\n" + schema
    if retry_reason:
        prompt = (
            f"PREVIOUS_ATTEMPT_REJECTED: {retry_reason}\n"
            "Fix exactly that and resend a complete, valid response.\n\n" + prompt
        )
    return prompt


def extract_structured_response(raw: bytes, *, max_output_bytes: int) -> dict[str, Any]:
    if len(raw) > max_output_bytes:
        raise MalformedResponse(f"adviser output exceeds {max_output_bytes} bytes")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedResponse("adviser output is not UTF-8") from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise MalformedResponse(f"adviser output is not JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise MalformedResponse("adviser output root is not an object")

    candidates: list[Any] = [payload]
    if "structured_output" in payload:
        candidates.insert(0, payload["structured_output"])
    if "result" in payload:
        result = payload["result"]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        candidates.append(result)
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("protocol") == PROTOCOL_VERSION:
            return candidate
    raise MalformedResponse("adviser output contains no protocol response object")


class FakeBackend:
    """Deterministic plumbing backend; it is not an adviser-quality test."""

    def invoke(
        self,
        request: dict[str, Any],
        *,
        transport_attempt: int = 1,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "id": request["id"],
            "context_id": request["context_id"],
            "review_scope": request["review_scope"],
            "evidence_checked": [],
            "state": "rejected",
            "decision": "stop",
            "recommendation": "Do not treat the fake backend as adviser approval.",
            "reason": "The fake backend validates protocol plumbing only; no adviser was called.",
            "change_evidence": "A real adviser response or contradictory objective evidence.",
            "executor_next_step": "Run a real adviser backend or stop the consultation path.",
        }


class ClaudeBackend:
    def __init__(
        self,
        *,
        executable: str,
        agent: str | None,
        model: str | None,
        effort: str | None,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
        health_check: bool = False,
    ) -> None:
        self.executable = executable
        self.agent = agent
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.effort = effort or DEFAULT_CLAUDE_EFFORT
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self._health_checked = not health_check

    def build_command(
        self, request: dict[str, Any], *, transport_attempt: int = 1
    ) -> list[str]:
        response_schema = response_schema_for_request(request, claude_compatible=True)
        schema = json.dumps(response_schema, separators=(",", ":"))
        command = [
            self.executable,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            schema,
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--tools",
            claude_tools_for_request(request),
        ]
        if self.agent:
            command.extend(["--agent", self.agent])
        if self.model:
            command.extend(["--model", self.model])
        command.extend(["--effort", self.effort])
        return command

    def build_health_check_command(self) -> list[str]:
        return [
            self.executable,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--model",
            self.model,
            "--effort",
            "low",
        ]

    def _run_process(
        self,
        command: list[str],
        prompt: bytes,
        *,
        timeout_seconds: float,
        timeout_label: str,
    ) -> tuple[int, bytes, bytes]:
        process = subprocess.Popen(
            command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.communicate()
            partial_stdout = len(exc.output or b"")
            partial_stderr = len(exc.stderr or b"")
            raise BackendTimeout(
                f"{timeout_label} exceeded {timeout_seconds:g} seconds; "
                f"partial stdout={partial_stdout} bytes, stderr={partial_stderr} bytes"
            ) from exc
        return process.returncode, stdout, stderr

    def health_check(self) -> None:
        returncode, stdout, stderr = self._run_process(
            self.build_health_check_command(),
            b"Reply with exactly OK.",
            timeout_seconds=min(
                self.timeout_seconds, DEFAULT_CLAUDE_HEALTH_CHECK_TIMEOUT_SECONDS
            ),
            timeout_label="Claude adviser health check",
        )
        if returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:1000]
            suffix = f": {detail}" if detail else ""
            raise BackendError(f"Claude adviser health check exited {returncode}{suffix}")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendError("Claude adviser health check returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("is_error"):
            detail = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(detail, str) or not detail.strip():
                detail = "Claude adviser health check reported an API error"
            raise BackendError(f"Claude adviser health check failed: {detail[:1000]}")

    def invoke(
        self,
        request: dict[str, Any],
        *,
        transport_attempt: int = 1,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        if not self._health_checked:
            self.health_check()
            self._health_checked = True
        prompt = build_adviser_prompt(request, retry_reason=retry_reason).encode("utf-8")
        command = self.build_command(request, transport_attempt=transport_attempt)
        returncode, stdout, stderr = self._run_process(
            command,
            prompt,
            timeout_seconds=self.timeout_seconds,
            timeout_label="Claude adviser",
        )
        if returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:1000]
            suffix = f": {detail}" if detail else ""
            raise BackendError(f"Claude adviser exited {returncode}{suffix}")
        return extract_structured_response(stdout, max_output_bytes=self.max_output_bytes)


class CodexBackend:
    """Run the adviser through a read-only, ephemeral Codex agent."""

    def __init__(
        self,
        *,
        executable: str,
        model: str | None,
        effort: str | None,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
        health_check: bool = False,
    ) -> None:
        # The shared factory passes this Claude-specific option to every
        # backend. Codex has no separate health-check phase, but accepting the
        # option keeps backend construction compatible.
        del health_check
        self.executable = executable
        self.model = model or DEFAULT_CODEX_MODEL
        self.effort = effort or DEFAULT_CODEX_EFFORT
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def build_command(
        self, request: dict[str, Any], schema_path: Path, output_path: Path
    ) -> list[str]:
        return [
            self.executable,
            "exec",
            "--model",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.effort}"',
            "-c",
            'model_provider="openai"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-C",
            str(self.cwd),
            "-",
        ]

    def invoke(
        self,
        request: dict[str, Any],
        *,
        transport_attempt: int = 1,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        prompt = build_adviser_prompt(request, retry_reason=retry_reason).encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="executor-adviser-codex-") as temp_dir:
            schema_path = Path(temp_dir) / "response-schema.json"
            output_path = Path(temp_dir) / "last-message.json"
            schema_path.write_text(
                json.dumps(
                    response_schema_for_request(request, codex_compatible=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            command = self.build_command(request, schema_path, output_path)
            process = subprocess.Popen(
                command,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(
                    input=prompt, timeout=self.timeout_seconds
                )
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                process.communicate()
                raise BackendTimeout(
                    f"Codex adviser exceeded {self.timeout_seconds:g} seconds"
                ) from exc
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[:1000]
                suffix = f": {detail}" if detail else ""
                raise BackendError(f"Codex adviser exited {process.returncode}{suffix}")
            raw = output_path.read_bytes() if output_path.is_file() else stdout
            return extract_structured_response(raw, max_output_bytes=self.max_output_bytes)


def _failure_response(
    request: dict[str, Any], *, state: str, reason: str
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "id": request["id"],
        "context_id": request["context_id"],
        "review_scope": request["review_scope"],
        "evidence_checked": [],
        "state": state,
        "decision": "stop",
        "recommendation": "Do not treat unavailable adviser input as approval.",
        "reason": reason[:4000],
        "change_evidence": "A valid adviser response tied to this request and context.",
        "executor_next_step": (
            "Use the declared fallback only if it is reversible and low-risk; otherwise ask the user. "
            f"Declared fallback: {request['fallback']}"
        )[:3000],
    }


def consult_with_retry(
    request_payload: Any,
    backend: AdviserBackend,
    *,
    retries: int,
    allow_restricted: bool,
    backend_name: str = "unknown",
    event_sink: EventSink | None = None,
    emit_trigger: bool = True,
) -> dict[str, Any]:
    request = validate_request(request_payload)
    if emit_trigger:
        _emit_event(
            event_sink,
            request,
            request["trigger_event"]["event"],
            event_id=request["trigger_event"]["event_id"],
            trigger=request["trigger"],
            trigger_source=request["trigger_event"]["trigger_source"],
            condition=request["trigger_event"]["condition"],
            blocking=request["trigger_event"]["blocking"],
            evidence_ready=request["trigger_event"]["evidence_ready"],
        )
    if request["data_classification"] == "restricted" and not allow_restricted:
        _emit_event(event_sink, request, "consultation-rejected", reason="restricted-data")
        return _failure_response(
            request,
            state="rejected",
            reason="Restricted data requires explicit --allow-restricted authorization.",
        )

    last_error: BackendError | ProtocolError | None = None
    for attempt_number in range(retries + 1):
        transport_attempt = attempt_number + 1
        _emit_event(
            event_sink,
            request,
            "transport-attempt",
            backend=backend_name,
            transport_attempt=transport_attempt,
            state="started",
        )
        retry_reason = (
            " ".join(str(last_error).split())[:400]
            if isinstance(last_error, (ProtocolError, MalformedResponse))
            else None
        )
        try:
            response = backend.invoke(
                request, transport_attempt=transport_attempt, retry_reason=retry_reason
            )
            validated = validate_response(response, request)
            _emit_event(
                event_sink,
                request,
                "transport-attempt",
                backend=backend_name,
                transport_attempt=transport_attempt,
                state="completed",
            )
            _emit_event(event_sink, request, "response-validated", backend=backend_name)
            return validated
        except (BackendError, ProtocolError) as exc:
            last_error = exc
            _emit_event(
                event_sink,
                request,
                "transport-attempt",
                backend=backend_name,
                transport_attempt=transport_attempt,
                state="failed",
                detail=str(exc)[:1000],
            )
            if attempt_number < retries:
                print(
                    f"adviser bridge: attempt {attempt_number + 1} failed; retrying: {exc}",
                    file=sys.stderr,
                )
    assert last_error is not None
    state = "timeout" if isinstance(last_error, BackendTimeout) else "failed"
    return _failure_response(request, state=state, reason=str(last_error))


def consult_with_backend_fallback(
    request_payload: Any,
    primary_backend: AdviserBackend,
    fallback_factory: Callable[[], AdviserBackend] | None,
    *,
    retries: int,
    allow_restricted: bool,
    primary_name: str = "codex",
    fallback_name: str = "claude",
    event_sink: EventSink | None = None,
) -> dict[str, Any]:
    """Run the primary backend first, then always try the fallback after a transport failure.

    The fallback is a transport/backend failover, not a new semantic round:
    the request id, context id, and consult index remain unchanged. A valid
    primary response is authoritative for this round; the fallback is not
    called just to obtain a preferred second opinion.
    """
    request = validate_request(request_payload)
    primary = consult_with_retry(
        request,
        primary_backend,
        retries=retries,
        allow_restricted=allow_restricted,
        backend_name=primary_name,
        event_sink=event_sink,
        emit_trigger=True,
    )
    if primary["state"] not in {"failed", "timeout"}:
        return primary

    primary_reason = str(primary.get("reason", "primary adviser unavailable"))
    _emit_event(
        event_sink,
        request,
        "backend-failover",
        from_backend=primary_name,
        to_backend=fallback_name,
        state="started",
        primary_state=primary["state"],
        detail=primary_reason[:1000],
    )
    if fallback_factory is None:
        _emit_event(
            event_sink,
            request,
            "backend-failover",
            from_backend=primary_name,
            to_backend=fallback_name,
            state="unavailable",
            detail="No fallback backend factory was configured.",
        )
        return primary

    try:
        fallback_backend = fallback_factory()
    except (BackendError, ProtocolError) as exc:
        detail = f"{fallback_name} backend unavailable: {exc}"
        primary_state = primary["state"]
        _emit_event(
            event_sink,
            request,
            "backend-failover",
            from_backend=primary_name,
            to_backend=fallback_name,
            state="unavailable",
            detail=detail[:1000],
        )
        return _failure_response(
            request,
            state=primary_state,
            reason=f"{primary_name} {primary_state}: {primary_reason}; {detail}",
        )

    fallback = consult_with_retry(
        request,
        fallback_backend,
        retries=retries,
        allow_restricted=allow_restricted,
        backend_name=fallback_name,
        event_sink=event_sink,
        emit_trigger=False,
    )
    if fallback["state"] in {"failed", "timeout"}:
        primary_state = primary["state"]
        fallback_state = fallback["state"]
        detail = (
            f"{primary_name} {primary_state}: {primary_reason}; "
            f"{fallback_name} {fallback_state}: {fallback.get('reason', '')}"
        )
        _emit_event(
            event_sink,
            request,
            "backend-failover",
            from_backend=primary_name,
            to_backend=fallback_name,
            state="failed",
            detail=detail[:1000],
        )
        return _failure_response(request, state=fallback["state"], reason=detail)

    _emit_event(
        event_sink,
        request,
        "backend-failover",
        from_backend=primary_name,
        to_backend=fallback_name,
        state="completed",
        fallback_state=fallback["state"],
    )
    return fallback


def _emit_event(
    sink: EventSink | None,
    request: dict[str, Any],
    event: str,
    **fields: Any,
) -> None:
    if sink is None:
        return
    sink(
        {
            "protocol": PROTOCOL_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "request_id": request["id"],
            "context_id": request["context_id"],
            "consult_index": request["consult_index"],
            "consult_limit": request["consult_limit"],
            "data_classification": request["data_classification"],
            **fields,
        }
    )


def _event_file_sink(path: Path | None) -> EventSink | None:
    if path is None:
        return None
    if not path.parent.is_dir():
        raise ProtocolError(f"event log parent is not a directory: {path.parent}")

    def emit(record: dict[str, Any]) -> None:
        try:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as exc:
            raise ProtocolError(f"cannot write event log: {exc}") from exc

    return emit


def _read_request(path: str) -> Any:
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ProtocolError(f"cannot read request: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"request is not JSON: {exc.msg}") from exc


def _read_response_bytes(path: str) -> bytes:
    try:
        if path == "-":
            return sys.stdin.buffer.read()
        return Path(path).read_bytes()
    except OSError as exc:
        raise ProtocolError(f"cannot read response: {exc}") from exc


class NativeResponseBackend:
    def __init__(self, raw: bytes, *, max_output_bytes: int) -> None:
        self.raw = raw
        self.max_output_bytes = max_output_bytes

    def invoke(
        self,
        request: dict[str, Any],
        *,
        transport_attempt: int = 1,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        # Replays fixed bytes captured earlier; retry_reason has nothing to act on here.
        return extract_structured_response(self.raw, max_output_bytes=self.max_output_bytes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("fake", "codex", "claude", "native"),
        default=DEFAULT_BACKEND,
    )
    parser.add_argument("--request", default="-", help="JSON request path; default: stdin")
    parser.add_argument(
        "--response",
        help="Native adviser JSON response path; with --backend native, omission emits the exact prompt",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"adviser timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument("--retries", type=int, choices=(0, 1), default=1)
    parser.add_argument("--max-output-bytes", type=int, default=131072)
    parser.add_argument(
        "--claude-agent",
        default=None,
        help=(
            "Optional named Claude agent; omitted by default so the bridge's "
            "protocol schema controls the response"
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Adviser model override; Codex default: {DEFAULT_CODEX_MODEL}; "
            f"Claude default: {DEFAULT_CLAUDE_MODEL}"
        ),
    )
    parser.add_argument(
        "--effort",
        choices=tuple(sorted(EFFORT_LEVELS)),
        default=None,
        help=(
            f"Adviser reasoning effort override; Codex default: {DEFAULT_CODEX_EFFORT}; "
            f"Claude default: {DEFAULT_CLAUDE_EFFORT}; retries reuse the selected effort"
        ),
    )
    parser.add_argument(
        "--claude-health-check",
        action="store_true",
        help=(
            "Run a low-effort Claude auth/network preflight before the first review "
            f"(timeout capped at {DEFAULT_CLAUDE_HEALTH_CHECK_TIMEOUT_SECONDS:g}s)"
        ),
    )
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--allow-restricted", action="store_true")
    parser.add_argument("--event-log", type=Path, help="append protocol events as JSONL")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--retry-reason",
        default=None,
        help=(
            "With --backend native: prepend a correction note to the emitted prompt, "
            "for regenerating a prompt after a prior native response failed validation"
        ),
    )
    return parser


def _build_codex_fallback(
    *, cwd: Path, timeout_seconds: float, max_output_bytes: int
) -> AdviserBackend:
    executable = shutil.which("codex")
    if executable is None:
        raise BackendError("codex executable was not found on PATH")
    return CodexBackend(
        executable=executable,
        model=DEFAULT_CODEX_MODEL,
        effort=DEFAULT_CODEX_EFFORT,
        cwd=cwd.resolve(),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def _build_claude_fallback(
    *, cwd: Path, timeout_seconds: float, max_output_bytes: int
) -> AdviserBackend:
    executable = shutil.which("claude")
    if executable is None:
        raise BackendError("claude executable was not found on PATH")
    return ClaudeBackend(
        executable=executable,
        agent=None,
        model=DEFAULT_CLAUDE_MODEL,
        effort=DEFAULT_CLAUDE_EFFORT,
        cwd=cwd.resolve(),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 < args.timeout <= 600:
        print("adviser bridge: --timeout must be greater than 0 and at most 600", file=sys.stderr)
        return 2
    if not 1024 <= args.max_output_bytes <= 1_048_576:
        print(
            "adviser bridge: --max-output-bytes must be from 1024 to 1048576",
            file=sys.stderr,
        )
        return 2
    if not args.cwd.is_dir():
        print(f"adviser bridge: --cwd is not a directory: {args.cwd}", file=sys.stderr)
        return 2

    try:
        if args.response == "-" and args.request == "-":
            raise ProtocolError("request and response cannot both use stdin")
        if args.response is not None and args.backend != "native":
            raise ProtocolError("--response requires --backend native")
        request = _read_request(args.request)
        event_sink = _event_file_sink(args.event_log)
        if args.backend == "native" and args.response is None:
            validated_request = validate_request(request)
            if (
                validated_request["data_classification"] == "restricted"
                and not args.allow_restricted
            ):
                response = _failure_response(
                    validated_request,
                    state="rejected",
                    reason="Restricted data requires explicit --allow-restricted authorization.",
                )
                print(json.dumps(response, ensure_ascii=False, indent=2 if args.pretty else None))
                return 3
            _emit_event(
                event_sink,
                validated_request,
                validated_request["trigger_event"]["event"],
                event_id=validated_request["trigger_event"]["event_id"],
                trigger=validated_request["trigger"],
                trigger_source=validated_request["trigger_event"]["trigger_source"],
                condition=validated_request["trigger_event"]["condition"],
                blocking=validated_request["trigger_event"]["blocking"],
                evidence_ready=validated_request["trigger_event"]["evidence_ready"],
            )
            _emit_event(event_sink, validated_request, "native-handoff-prepared", backend="native")
            print(
                build_adviser_prompt(
                    validated_request, include_schema=True, retry_reason=args.retry_reason
                )
            )
            return 0
        if args.backend == "fake":
            backend: AdviserBackend = FakeBackend()
            backend_name = "fake"
            retries = args.retries
        elif args.backend == "codex":
            executable = shutil.which("codex")
            if executable is None:
                raise ProtocolError("codex executable was not found on PATH")
            backend = CodexBackend(
                executable=executable,
                model=args.model or DEFAULT_CODEX_MODEL,
                effort=args.effort,
                cwd=args.cwd.resolve(),
                timeout_seconds=args.timeout,
                max_output_bytes=args.max_output_bytes,
                health_check=args.claude_health_check,
            )
            backend_name = "codex"
            retries = args.retries
        elif args.backend == "claude":
            executable = shutil.which("claude")
            if executable is None:
                raise ProtocolError("claude executable was not found on PATH")
            agent = (args.claude_agent or "").strip() or None
            backend = ClaudeBackend(
                executable=executable,
                agent=agent,
                model=args.model,
                effort=args.effort,
                cwd=args.cwd.resolve(),
                timeout_seconds=args.timeout,
                max_output_bytes=args.max_output_bytes,
                health_check=args.claude_health_check,
            )
            backend_name = "claude"
            retries = args.retries
        else:
            assert args.response is not None
            backend = NativeResponseBackend(
                _read_response_bytes(args.response), max_output_bytes=args.max_output_bytes
            )
            backend_name = "native"
            retries = 0
        if args.backend == "codex":
            response = consult_with_backend_fallback(
                request,
                backend,
                lambda: _build_claude_fallback(
                    cwd=args.cwd,
                    timeout_seconds=args.timeout,
                    max_output_bytes=args.max_output_bytes,
                ),
                retries=retries,
                allow_restricted=args.allow_restricted,
                primary_name=backend_name,
                fallback_name="claude",
                event_sink=event_sink,
            )
        elif args.backend == "claude":
            response = consult_with_backend_fallback(
                request,
                backend,
                lambda: _build_codex_fallback(
                    cwd=args.cwd,
                    timeout_seconds=args.timeout,
                    max_output_bytes=args.max_output_bytes,
                ),
                retries=retries,
                allow_restricted=args.allow_restricted,
                primary_name=backend_name,
                fallback_name="codex",
                event_sink=event_sink,
            )
        else:
            response = consult_with_retry(
                request,
                backend,
                retries=retries,
                allow_restricted=args.allow_restricted,
                backend_name=backend_name,
                event_sink=event_sink,
                emit_trigger=args.backend != "native",
            )
    except ProtocolError as exc:
        print(f"adviser bridge: invalid request: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(response, ensure_ascii=False, indent=2 if args.pretty else None))
    state = response["state"]
    if state == "completed":
        return 0
    if state == "input-required":
        return 4
    if state == "rejected":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
