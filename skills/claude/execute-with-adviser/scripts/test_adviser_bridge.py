#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic tests for adviser_bridge.py; no external adviser is invoked."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import adviser_bridge as bridge  # noqa: E402


def valid_request(**overrides):
    request = {
        "protocol": bridge.PROTOCOL_VERSION,
        "id": "request-1",
        "context_id": "task-1",
        "state": "submitted",
        "trigger": "ambiguity",
        "trigger_event": {
            "event": "advice-triggered",
            "event_id": "event-1",
            "trigger": "ambiguity",
            "trigger_source": "automatic",
            "condition": "Two consequential interpretations remain.",
            "blocking": True,
            "evidence_ready": False,
        },
        "review_scope": "summary-only",
        "goal": "Choose the safe interpretation and preserve the acceptance criteria.",
        "question": "Should the executor use interpretation A or B?",
        "evidence": [],
        "attempts": [],
        "constraints": ["Do not modify external state."],
        "executor_position": "A is reversible; B is not.",
        "fallback": "Use A and record the assumption.",
        "data_classification": "public",
        "consult_index": 1,
        "consult_limit": 3,
    }
    request.update(overrides)
    return request


def valid_response(request):
    return {
        "protocol": bridge.PROTOCOL_VERSION,
        "id": request["id"],
        "context_id": request["context_id"],
        "review_scope": request["review_scope"],
        "evidence_checked": (
            [request["evidence"][0]["uri"]]
            if request["review_scope"] == "evidence-read" and request["evidence"]
            else []
        ),
        "state": "completed",
        "decision": "proceed",
        "recommendation": "Use interpretation A.",
        "reason": "It is reversible and satisfies the stated constraints.",
        "change_evidence": "Evidence that B is required by the acceptance criteria.",
        "executor_next_step": "Apply A and verify the output.",
    }


class SequenceBackend:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.transport_attempts = []
        self.retry_reasons = []

    def invoke(self, request, *, transport_attempt=1, retry_reason=None):
        self.transport_attempts.append(transport_attempt)
        self.retry_reasons.append(retry_reason)
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RequestValidationTests(unittest.TestCase):
    def test_valid_request(self):
        self.assertEqual(bridge.validate_request(valid_request())["trigger"], "ambiguity")

    def test_unknown_field_is_rejected(self):
        request = valid_request(unexpected=True)
        with self.assertRaisesRegex(bridge.ProtocolError, "unknown field"):
            bridge.validate_request(request)

    def test_failure_requires_two_attempts(self):
        event = valid_request()["trigger_event"] | {"trigger": "failure"}
        request = valid_request(trigger="failure", trigger_event=event, attempts=[])
        with self.assertRaisesRegex(bridge.ProtocolError, "at least two attempts"):
            bridge.validate_request(request)

    def test_critical_fact_requires_evidence(self):
        event = valid_request()["trigger_event"] | {
            "trigger": "critical-fact",
            "evidence_ready": True,
        }
        request = valid_request(trigger="critical-fact", trigger_event=event, evidence=[])
        with self.assertRaisesRegex(bridge.ProtocolError, "requires evidence"):
            bridge.validate_request(request)

    def test_consult_limit_is_enforced(self):
        request = valid_request(consult_index=3, consult_limit=2)
        with self.assertRaisesRegex(bridge.ProtocolError, "exceeds consult_limit"):
            bridge.validate_request(request)

    def test_three_consultation_rounds_are_allowed(self):
        request = valid_request(consult_index=3, consult_limit=3)
        self.assertIs(bridge.validate_request(request), request)

    def test_trigger_event_must_match_request_trigger(self):
        event = valid_request()["trigger_event"] | {"trigger": "failure"}
        request = valid_request(trigger_event=event)
        with self.assertRaisesRegex(bridge.ProtocolError, "must match"):
            bridge.validate_request(request)

    def test_pilot_requires_exactly_one_consultation(self):
        event = valid_request()["trigger_event"] | {"trigger_source": "pilot"}
        request = valid_request(trigger_event=event, consult_limit=2)
        with self.assertRaisesRegex(bridge.ProtocolError, "pilot trigger"):
            bridge.validate_request(request)

    def test_result_review_requires_ready_evidence(self):
        event = valid_request()["trigger_event"] | {
            "trigger": "result-review",
            "evidence_ready": False,
        }
        request = valid_request(
            trigger="result-review",
            trigger_event=event,
            evidence=[{"uri": "/tmp/result.txt", "locator": "final result"}],
        )
        with self.assertRaisesRegex(bridge.ProtocolError, "evidence_ready=true"):
            bridge.validate_request(request)

    def test_result_review_requires_evidence(self):
        event = valid_request()["trigger_event"] | {
            "trigger": "result-review",
            "evidence_ready": True,
        }
        request = valid_request(
            trigger="result-review", trigger_event=event, evidence=[]
        )
        with self.assertRaisesRegex(bridge.ProtocolError, "requires evidence"):
            bridge.validate_request(request)


class ResponseTests(unittest.TestCase):
    def test_structured_output_wrapper_is_extracted(self):
        request = valid_request()
        wrapped = json.dumps({"type": "result", "structured_output": valid_response(request)})
        result = bridge.extract_structured_response(wrapped.encode(), max_output_bytes=10000)
        self.assertEqual(result["id"], request["id"])

    def test_response_identity_is_enforced(self):
        request = valid_request()
        response = valid_response(request)
        response["id"] = "another-request"
        with self.assertRaisesRegex(bridge.ProtocolError, "does not match"):
            bridge.validate_response(response, request)

    def test_need_evidence_pair_is_enforced(self):
        request = valid_request()
        response = valid_response(request)
        response["decision"] = "need-evidence"
        with self.assertRaisesRegex(bridge.ProtocolError, "must appear together"):
            bridge.validate_response(response, request)

    def test_summary_only_cannot_claim_opened_evidence(self):
        request = valid_request(evidence=[{"uri": "/tmp/a", "locator": "claim"}])
        response = valid_response(request)
        response["evidence_checked"] = ["/tmp/a"]
        with self.assertRaisesRegex(bridge.ProtocolError, "summary-only"):
            bridge.validate_response(response, request)

    def test_completed_evidence_read_must_name_requested_evidence(self):
        request = valid_request(
            review_scope="evidence-read",
            evidence=[{"uri": "/tmp/a", "locator": "claim"}],
        )
        response = valid_response(request)
        response["evidence_checked"] = []
        with self.assertRaisesRegex(bridge.ProtocolError, "must list"):
            bridge.validate_response(response, request)


class RetryAndPolicyTests(unittest.TestCase):
    def test_malformed_response_is_retried_once(self):
        request = valid_request()
        backend = SequenceBackend(
            [bridge.MalformedResponse("bad JSON"), valid_response(request)]
        )
        response = bridge.consult_with_retry(
            request, backend, retries=1, allow_restricted=False
        )
        self.assertEqual(response["state"], "completed")
        self.assertEqual(backend.calls, 2)

    def test_validation_failure_feeds_reason_into_retry(self):
        request = valid_request()
        thin_response = valid_response(request) | {"change_evidence": ""}
        backend = SequenceBackend([thin_response, valid_response(request)])
        response = bridge.consult_with_retry(
            request, backend, retries=1, allow_restricted=False
        )
        self.assertEqual(response["state"], "completed")
        self.assertEqual(backend.calls, 2)
        self.assertEqual(backend.retry_reasons[0], None)
        self.assertIn("change_evidence", backend.retry_reasons[1])

    def test_timeout_does_not_leak_into_retry_reason(self):
        request = valid_request()
        backend = SequenceBackend(
            [bridge.BackendTimeout("slow"), bridge.BackendTimeout("slow again")]
        )
        bridge.consult_with_retry(request, backend, retries=1, allow_restricted=False)
        self.assertEqual(backend.retry_reasons, [None, None])

    def test_timeout_stops_after_retry(self):
        request = valid_request()
        backend = SequenceBackend(
            [bridge.BackendTimeout("slow"), bridge.BackendTimeout("slow again")]
        )
        response = bridge.consult_with_retry(
            request, backend, retries=1, allow_restricted=False
        )
        self.assertEqual(response["state"], "timeout")
        self.assertEqual(response["decision"], "stop")
        self.assertEqual(backend.calls, 2)

    def test_restricted_request_is_rejected_without_call(self):
        request = valid_request(data_classification="restricted")
        backend = SequenceBackend([valid_response(request)])
        response = bridge.consult_with_retry(
            request, backend, retries=1, allow_restricted=False
        )
        self.assertEqual(response["state"], "rejected")
        self.assertEqual(backend.calls, 0)

    def test_transport_attempts_do_not_change_consult_index(self):
        request = valid_request()
        backend = SequenceBackend(
            [bridge.MalformedResponse("bad JSON"), valid_response(request)]
        )
        events = []
        response = bridge.consult_with_retry(
            request,
            backend,
            retries=1,
            allow_restricted=False,
            backend_name="native",
            event_sink=events.append,
        )
        self.assertEqual(response["state"], "completed")
        attempts = [event for event in events if event["event"] == "transport-attempt"]
        self.assertEqual({event["transport_attempt"] for event in attempts}, {1, 2})
        self.assertEqual({event["consult_index"] for event in events}, {1})
        self.assertEqual(backend.transport_attempts, [1, 2])
        self.assertEqual(events[0]["event"], "advice-triggered")

    def test_claude_failure_always_falls_back_to_codex_same_round(self):
        request = valid_request()
        primary = SequenceBackend(
            [bridge.BackendError("Claude exited 1"), bridge.BackendError("Claude exited 1 again")]
        )
        fallback = SequenceBackend([valid_response(request)])
        events = []

        response = bridge.consult_with_backend_fallback(
            request,
            primary,
            lambda: fallback,
            retries=1,
            allow_restricted=False,
            primary_name="claude",
            fallback_name="codex",
            event_sink=events.append,
        )

        self.assertEqual(response["state"], "completed")
        self.assertEqual(primary.calls, 2)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(fallback.transport_attempts, [1])
        self.assertEqual(
            sum(event["event"] == "advice-triggered" for event in events), 1
        )
        self.assertEqual(
            [event["state"] for event in events if event["event"] == "backend-failover"],
            ["started", "completed"],
        )
        self.assertEqual({event["consult_index"] for event in events}, {1})

    def test_valid_claude_response_does_not_call_codex(self):
        request = valid_request()
        primary = SequenceBackend([valid_response(request)])
        fallback_calls = []
        events = []

        response = bridge.consult_with_backend_fallback(
            request,
            primary,
            lambda: fallback_calls.append(True),
            retries=1,
            allow_restricted=False,
            event_sink=events.append,
        )

        self.assertEqual(response["state"], "completed")
        self.assertEqual(fallback_calls, [])
        self.assertNotIn("backend-failover", {event["event"] for event in events})

    def test_claude_fallback_failure_remains_fail_closed_after_codex_failure(self):
        request = valid_request()
        primary = SequenceBackend(
            [bridge.BackendError("Codex failed"), bridge.BackendError("Codex failed again")]
        )
        fallback = SequenceBackend(
            [bridge.BackendTimeout("Claude slow"), bridge.BackendTimeout("Claude slow again")]
        )
        events = []

        response = bridge.consult_with_backend_fallback(
            request,
            primary,
            lambda: fallback,
            retries=1,
            allow_restricted=False,
            event_sink=events.append,
        )

        self.assertEqual(response["state"], "timeout")
        self.assertEqual(response["decision"], "stop")
        self.assertIn("codex failed", response["reason"])
        self.assertIn("claude timeout", response["reason"])
        self.assertEqual(
            [event["state"] for event in events if event["event"] == "backend-failover"],
            ["started", "failed"],
        )


class PromptContentTests(unittest.TestCase):
    def test_prompt_explains_change_evidence(self):
        prompt = bridge.build_adviser_prompt(valid_request())
        self.assertIn("change_evidence must contain real content", prompt)

    def test_retry_reason_is_absent_by_default(self):
        prompt = bridge.build_adviser_prompt(valid_request())
        self.assertNotIn("PREVIOUS_ATTEMPT_REJECTED", prompt)

    def test_retry_reason_is_prepended_when_given(self):
        prompt = bridge.build_adviser_prompt(
            valid_request(), retry_reason="change_evidence must not be empty"
        )
        self.assertIn("PREVIOUS_ATTEMPT_REJECTED", prompt)
        self.assertIn("change_evidence must not be empty", prompt)
        self.assertLess(
            prompt.index("PREVIOUS_ATTEMPT_REJECTED"), prompt.index("REQUEST_JSON")
        )


class ClaudeCommandTests(unittest.TestCase):
    def test_command_is_fixed_argv_and_schema_constrained(self):
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent="adviser",
            model=None,
            effort="high",
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        request = valid_request()
        command = backend.build_command(request)
        self.assertEqual(command[0], "/fixed/claude")
        self.assertIn("--json-schema", command)
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(command[command.index("--effort") + 1], "high")
        self.assertNotIn("shell=True", command)
        schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$schema", schema)
        self.assertNotIn("$id", schema)
        self.assertEqual(schema["properties"]["id"]["const"], request["id"])
        self.assertEqual(
            schema["properties"]["context_id"]["const"], request["context_id"]
        )
        self.assertEqual(
            schema["properties"]["review_scope"]["const"], request["review_scope"]
        )

    def test_second_transport_attempt_reuses_selected_effort(self):
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=None,
            model="opus",
            effort="low",
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        request = valid_request()
        first = backend.build_command(request, transport_attempt=1)
        second = backend.build_command(request, transport_attempt=2)
        self.assertEqual(first[first.index("--effort") + 1], "low")
        self.assertEqual(second[second.index("--effort") + 1], "low")
        self.assertEqual(second[second.index("--model") + 1], "opus")

    def test_http_evidence_enables_only_bounded_read_and_web_fetch(self):
        event = valid_request()["trigger_event"] | {
            "trigger": "result-review",
            "evidence_ready": True,
        }
        request = valid_request(
            trigger="result-review",
            trigger_event=event,
            review_scope="evidence-read",
            evidence=[{"uri": "https://example.com/evidence", "locator": "claim"}],
        )
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=None,
            model="opus",
            effort="low",
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        command = backend.build_command(request)
        self.assertEqual(
            command[command.index("--tools") + 1], "Read,WebFetch"
        )
        self.assertIn("use WebFetch only for the exact listed URLs", bridge.build_adviser_prompt(request))
        self.assertIn("never recursively search", bridge.build_adviser_prompt(request))

    def test_summary_only_does_not_enable_web_fetch(self):
        request = valid_request(
            evidence=[{"uri": "https://example.com/evidence", "locator": "claim"}]
        )
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=None,
            model="opus",
            effort="low",
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        command = backend.build_command(request)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("This consultation has no tools", bridge.build_adviser_prompt(request))

    def test_local_evidence_read_exposes_read_only_tool_without_discovery(self):
        event = valid_request()["trigger_event"] | {
            "trigger": "critical-fact",
            "evidence_ready": True,
        }
        request = valid_request(
            trigger="critical-fact",
            trigger_event=event,
            review_scope="evidence-read",
            evidence=[{"uri": "/tmp/evidence.txt", "locator": "lines 1-5"}],
        )
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=None,
            model="opus",
            effort="low",
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        command = backend.build_command(request)
        self.assertEqual(command[command.index("--tools") + 1], "Read")
        self.assertNotIn("Grep", command[command.index("--tools") + 1])
        self.assertNotIn("Glob", command[command.index("--tools") + 1])

    def test_health_check_command_is_low_effort_and_tool_free(self):
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=None,
            model="opus",
            effort="xhigh",
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
            health_check=True,
        )
        command = backend.build_health_check_command()
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--effort") + 1], "low")
        self.assertFalse(backend._health_checked)

    def test_default_claude_model_and_effort(self):
        args = bridge.build_parser().parse_args([])
        self.assertIsNone(args.model)
        self.assertIsNone(args.effort)
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=None,
            model=args.model,
            effort=args.effort,
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        command = backend.build_command(valid_request())
        self.assertEqual(
            command[command.index("--model") + 1], bridge.DEFAULT_CLAUDE_MODEL
        )
        self.assertEqual(
            command[command.index("--effort") + 1], bridge.DEFAULT_CLAUDE_EFFORT
        )

    def test_default_claude_agent_is_omitted(self):
        args = bridge.build_parser().parse_args([])
        self.assertIsNone(args.claude_agent)
        backend = bridge.ClaudeBackend(
            executable="/fixed/claude",
            agent=args.claude_agent,
            model=None,
            effort=args.effort,
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        self.assertNotIn("--agent", backend.build_command(valid_request()))

    def test_default_timeout_and_health_check_are_explicit(self):
        args = bridge.build_parser().parse_args([])
        self.assertEqual(args.timeout, 300.0)
        self.assertEqual(args.timeout, bridge.DEFAULT_TIMEOUT_SECONDS)
        self.assertFalse(args.claude_health_check)


class CodexCommandTests(unittest.TestCase):
    def test_default_codex_adviser_command_uses_sol_xhigh_and_read_only(self):
        backend = bridge.CodexBackend(
            executable="/fixed/codex",
            model=None,
            effort=None,
            cwd=Path("/tmp"),
            timeout_seconds=30,
            max_output_bytes=10000,
        )
        request = valid_request()
        command = backend.build_command(
            request,
            Path("/tmp/adviser-response-schema.json"),
            Path("/tmp/adviser-last-message.json"),
        )
        self.assertEqual(command[:2], ["/fixed/codex", "exec"])
        self.assertEqual(command[command.index("--model") + 1], "gpt-6-sol")
        self.assertEqual(
            command[command.index("-c") + 1], 'model_reasoning_effort="xhigh"'
        )
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", command)
        self.assertIn("--output-last-message", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertEqual(command[-1], "-")
        schema = bridge.response_schema_for_request(request, codex_compatible=True)
        serialized = json.dumps(schema)
        self.assertNotIn('"const"', serialized)
        self.assertNotIn('"uniqueItems"', serialized)
        self.assertEqual(schema["properties"]["id"]["enum"], [request["id"]])

    def test_default_backend_is_codex(self):
        args = bridge.build_parser().parse_args([])
        self.assertEqual(args.backend, "codex")


class CodexProviderPinTests(unittest.TestCase):
    def test_adviser_codex_command_pins_the_openai_provider(self):
        # The default Codex provider may be a local oMLX; the adviser must stay on OpenAI.
        backend = bridge.CodexBackend(
            executable="codex", model=None, effort=None, cwd=Path("."),
            timeout_seconds=5, max_output_bytes=4096,
        )
        command = backend.build_command(valid_request(), Path("s.json"), Path("o.json"))
        pairs = [command[i + 1] for i, c in enumerate(command) if c == "-c"]
        self.assertIn('model_provider="openai"', pairs)


class CliTests(unittest.TestCase):
    def test_claude_health_check_flag_reaches_backend(self):
        request = valid_request()
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            output = io.StringIO()
            with (
                patch.object(bridge.shutil, "which", return_value="/fixed/claude"),
                patch.object(bridge, "ClaudeBackend") as claude_backend,
                patch.object(
                    bridge,
                    "consult_with_backend_fallback",
                    return_value=valid_response(request),
                ),
                redirect_stdout(output),
            ):
                result = bridge.main(
                    [
                        "--backend",
                        "claude",
                        "--request",
                        str(request_path),
                        "--claude-health-check",
                    ]
                )
        self.assertEqual(result, 0)
        self.assertTrue(claude_backend.call_args.kwargs["health_check"])

    def test_default_backend_routes_through_claude_fallback(self):
        request = valid_request()
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            output = io.StringIO()
            with (
                patch.object(bridge.shutil, "which", return_value="/fixed/codex"),
                patch.object(bridge, "CodexBackend"),
                patch.object(
                    bridge,
                    "consult_with_backend_fallback",
                    return_value=valid_response(request),
                ) as fallback_call,
                redirect_stdout(output),
            ):
                result = bridge.main(["--request", str(request_path)])
        self.assertEqual(result, 0)
        self.assertEqual(fallback_call.call_args.kwargs["primary_name"], "codex")
        self.assertEqual(fallback_call.call_args.kwargs["fallback_name"], "claude")

    def test_fake_backend_end_to_end(self):
        request = valid_request()
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        result = subprocess.run(
            [uv, "run", "--script", str(SCRIPT_DIR / "adviser_bridge.py"), "--backend", "fake"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["id"], request["id"])
        self.assertEqual(response["state"], "rejected")
        self.assertEqual(response["decision"], "stop")

    def test_invalid_json_returns_usage_error(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        result = subprocess.run(
            [uv, "run", "--script", str(SCRIPT_DIR / "adviser_bridge.py")],
            input="not-json",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid request", result.stderr)

    def test_native_prompt_and_response_use_same_contract(self):
        request = valid_request()
        response = valid_response(request)
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            response_path = root / "response.json"
            event_path = root / "events.jsonl"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            response_path.write_text(json.dumps(response), encoding="utf-8")

            prepared = subprocess.run(
                [
                    uv,
                    "run",
                    "--script",
                    str(SCRIPT_DIR / "adviser_bridge.py"),
                    "--backend",
                    "native",
                    "--request",
                    str(request_path),
                    "--event-log",
                    str(event_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertIn("REQUEST_JSON", prepared.stdout)
            self.assertIn("RESPONSE_JSON_SCHEMA", prepared.stdout)

            validated = subprocess.run(
                [
                    uv,
                    "run",
                    "--script",
                    str(SCRIPT_DIR / "adviser_bridge.py"),
                    "--backend",
                    "native",
                    "--request",
                    str(request_path),
                    "--response",
                    str(response_path),
                    "--event-log",
                    str(event_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(json.loads(validated.stdout)["id"], request["id"])
            events = [json.loads(line) for line in event_path.read_text().splitlines()]
            self.assertEqual(events[0]["event"], "advice-triggered")
            self.assertIn("native-handoff-prepared", {event["event"] for event in events})
            self.assertIn("response-validated", {event["event"] for event in events})


if __name__ == "__main__":
    unittest.main()
