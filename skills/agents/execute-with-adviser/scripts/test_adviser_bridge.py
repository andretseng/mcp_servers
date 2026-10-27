#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic tests for adviser_bridge.py; no external adviser is invoked."""

from __future__ import annotations

import json
import os
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ["ADVISER_LEDGER"] = "off"  # unit tests must not write the real review ledger
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

        response = bridge.consult_with_codex_fallback(
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

        response = bridge.consult_with_codex_fallback(
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

    def test_codex_failure_remains_fail_closed_after_claude_failure(self):
        request = valid_request()
        primary = SequenceBackend(
            [bridge.BackendError("Claude failed"), bridge.BackendError("Claude failed again")]
        )
        fallback = SequenceBackend(
            [bridge.BackendTimeout("Codex slow"), bridge.BackendTimeout("Codex slow again")]
        )
        events = []

        response = bridge.consult_with_codex_fallback(
            request,
            primary,
            lambda: fallback,
            retries=1,
            allow_restricted=False,
            event_sink=events.append,
        )

        self.assertEqual(response["state"], "timeout")
        self.assertEqual(response["decision"], "stop")
        self.assertIn("claude failed", response["reason"])
        self.assertIn("codex timeout", response["reason"])
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

    def test_default_backend_is_claude(self):
        args = bridge.build_parser().parse_args([])
        self.assertEqual(args.backend, "claude")


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


class LedgerTests(unittest.TestCase):
    def run_one(self, directory, backend_name="omlx", **overrides):
        request = bridge.validate_request(valid_request(**overrides))
        ok = OmlxAndChainTests._ok(self, request)

        class Good:
            model = "Qwen3.8-27B-4bit"

            def effort_for(self, req):
                return "low"

            def invoke(self, req, **kwargs):
                return ok

        env = {"ADVISER_LEDGER": "on", "ADVISER_LEDGER_DIR": directory, "ADVISER_EXECUTOR": "pi:test/Ornith"}
        with patch.dict(os.environ, env):
            return bridge.consult_with_retry(
                request, Good(), retries=0, allow_restricted=True, backend_name=backend_name
            )

    def read(self, directory):
        path = Path(directory) / "reviews.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_one_line_per_round_with_model_effort_executor_and_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_one(tmp)
            self.run_one(tmp, consult_index=2, consult_limit=3)
            rows = self.read(tmp)
            self.assertEqual(len(rows), 2)
            row = rows[0]
            self.assertEqual((row["backend"], row["model"], row["effort"]), ("omlx", "Qwen3.8-27B-4bit", "low"))
            self.assertEqual((row["executor"], row["decision"], row["state"]), ("pi:test/Ornith", "proceed", "completed"))
            self.assertEqual(rows[1]["consult_index"], 2)
            self.assertFalse(row["is_test"])
            self.assertEqual(oct((Path(tmp) / "reviews.jsonl").stat().st_mode & 0o777), "0o600")

    def test_test_backends_are_flagged_and_restricted_text_is_not_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_one(tmp, backend_name="scripted")
            self.run_one(tmp, data_classification="restricted")
            rows = self.read(tmp)
            self.assertTrue(rows[0]["is_test"])
            self.assertIsNotNone(rows[0]["recommendation"])
            self.assertIsNone(rows[1]["recommendation"])

    def test_failures_are_logged_and_a_broken_ledger_never_breaks_the_review(self):
        request = bridge.validate_request(valid_request())

        class Boom:
            def invoke(self, req, **kwargs):
                raise bridge.BackendError("down")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ADVISER_LEDGER": "on", "ADVISER_LEDGER_DIR": tmp}):
                response = bridge.consult_with_retry(request, Boom(), retries=1, allow_restricted=False, backend_name="codex")
            self.assertEqual(response["state"], "failed")
            row = self.read(tmp)[0]
            self.assertEqual((row["state"], row["attempts"]), ("failed", 2))
            self.assertIn("down", row["error"])
        with patch.dict(os.environ, {"ADVISER_LEDGER": "on", "ADVISER_LEDGER_DIR": "/proc/forbidden/x"}):
            response = bridge.consult_with_retry(request, Boom(), retries=0, allow_restricted=False, backend_name="codex")
        self.assertEqual(response["state"], "failed")      # still returned normally


class ReviewDisciplineTests(unittest.TestCase):
    def seed(self, directory, **row):
        base = {"request_id": "request-1", "consult_index": 1, "state": "completed", "decision": "need-evidence", "is_test": False}
        with (Path(directory) / "reviews.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**base, **row}) + "\n")

    def conflict(self, directory, **request):
        env = {"ADVISER_LEDGER": "on", "ADVISER_LEDGER_DIR": directory}
        with patch.dict(os.environ, env):
            return bridge.answered_round_conflict(valid_request(**request))

    def test_codex_and_claude_efforts_below_the_floor_are_dropped_with_a_warning(self):
        err = io.StringIO()
        with patch("sys.stderr", err), patch.dict(os.environ, {"ADVISER_ALLOW_LOW_EFFORT": ""}):
            self.assertIsNone(bridge.enforce_effort_floor("codex", "low"))
            self.assertIsNone(bridge.enforce_effort_floor("claude", "high"))
        self.assertIn("--effort low ignored for codex", err.getvalue())
        self.assertEqual(err.getvalue().count("ignored"), 2)

    def test_floor_keeps_xhigh_max_default_and_other_backends(self):
        with patch("sys.stderr", io.StringIO()) as err, patch.dict(os.environ, {"ADVISER_ALLOW_LOW_EFFORT": ""}):
            self.assertEqual(bridge.enforce_effort_floor("codex", "xhigh"), "xhigh")
            self.assertEqual(bridge.enforce_effort_floor("claude", "max"), "max")
            self.assertIsNone(bridge.enforce_effort_floor("codex", None))
            self.assertEqual(bridge.enforce_effort_floor("omlx", "low"), "low")
        self.assertEqual(err.getvalue(), "")

    def test_floor_can_be_lifted_explicitly(self):
        with patch.dict(os.environ, {"ADVISER_ALLOW_LOW_EFFORT": "1"}):
            self.assertEqual(bridge.enforce_effort_floor("codex", "low"), "low")

    def test_an_answered_round_cannot_be_resent_but_failures_and_new_rounds_can(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self.conflict(tmp))                        # no ledger yet
            self.seed(tmp, state="failed", decision=None)
            self.assertIsNone(self.conflict(tmp))                        # backend failure may be retried
            self.seed(tmp, is_test=True)
            self.assertIsNone(self.conflict(tmp))                        # test backends never count
            self.seed(tmp)
            self.assertIn("already answered", self.conflict(tmp))
            self.assertIn("NEW request", self.conflict(tmp))
            self.assertIsNone(self.conflict(tmp, id="request-2"))        # new id
            self.assertIsNone(self.conflict(tmp, consult_index=2, consult_limit=3))  # challenge round

    def test_main_refuses_a_resent_round_before_calling_any_adviser(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.seed(tmp)
            req = Path(tmp) / "req.json"
            req.write_text(json.dumps(valid_request()), encoding="utf-8")
            env = {"ADVISER_LEDGER": "on", "ADVISER_LEDGER_DIR": tmp}
            with patch.dict(os.environ, env), patch("shutil.which", side_effect=AssertionError("adviser was launched")), \
                    patch("sys.stderr", io.StringIO()) as err:
                code = bridge.main(["--request", str(req), "--backend", "codex", "--failover", "claude,omlx"])
            self.assertEqual(code, 3)
            self.assertIn("rejected by policy", err.getvalue())


class ExecutorDetectionTests(unittest.TestCase):
    def make_home(self, tmp, thread, models, provider="omlx"):
        d = Path(tmp) / "sessions" / "2026" / "10" / "12"
        d.mkdir(parents=True)
        lines = [json.dumps({"type": "session_meta", "payload": {"id": thread, "model_provider": provider}})]
        lines += [json.dumps({"type": "turn_context", "payload": {"model": m}}) for m in models]
        (d / f"rollout-2026-10-12T10-00-00-{thread}.jsonl").write_text("\n".join(lines), encoding="utf-8")

    def detect(self, **env):
        with patch.dict(os.environ, env, clear=False):
            return bridge.detect_executor()

    def test_explicit_wins_then_codex_session_model_latest_turn(self):
        thread = "01a130aa-1c96-7232-be84-793c61677d7c"
        with tempfile.TemporaryDirectory() as tmp:
            self.make_home(tmp, thread, ["gpt-6.1-sol", "Ornith-1.5-35B-A3B-MLX-8bit"])
            clear = {"ADVISER_EXECUTOR": ""}
            self.assertEqual(self.detect(ADVISER_EXECUTOR="pi:x/y", CODEX_THREAD_ID=thread, CODEX_HOME=tmp), "pi:x/y")
            os.environ.pop("ADVISER_EXECUTOR", None)
            self.assertEqual(self.detect(CODEX_THREAD_ID=thread, CODEX_HOME=tmp), "codex:omlx/Ornith-1.5-35B-A3B-MLX-8bit")
            self.assertEqual(self.detect(CODEX_THREAD_ID="ffffffff-0000-0000-0000-000000000000", CODEX_HOME=tmp), "codex:ffffffff")

    def test_unknown_outside_pi_and_codex(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADVISER_EXECUTOR", None)
            os.environ.pop("CODEX_THREAD_ID", None)
            self.assertEqual(bridge.detect_executor(), "unknown")


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
                    "consult_with_codex_fallback",
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


class OmlxAndChainTests(unittest.TestCase):
    def _server(self, handler_body):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                Handler.seen.append((self.headers.get("Authorization"), body))
                code, payload = handler_body(body)
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        Handler.seen = []
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server, Handler

    def _backend(self, server, key_dir):
        key = Path(key_dir) / "key"
        key.write_text("secret-test-key\n", encoding="utf-8")
        return bridge.OmlxBackend(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model=None,
            effort=None,
            key_file=key,
            timeout_seconds=5,
            max_output_bytes=131072,
        )

    def _ok(self, request):
        return {
            "protocol": bridge.PROTOCOL_VERSION,
            "id": request["id"],
            "context_id": request["context_id"],
            "review_scope": request["review_scope"],
            "evidence_checked": [],
            "state": "completed",
            "decision": "proceed",
            "recommendation": "Use A.",
            "reason": "A is reversible.",
            "change_evidence": "Evidence that A is irreversible.",
            "executor_next_step": "Apply A.",
        }

    def test_omlx_sends_key_effort_and_schema_and_validates(self):
        request = bridge.validate_request(valid_request())
        server, handler = self._server(
            lambda body: (200, {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self._ok(request))}}]})
        )
        with tempfile.TemporaryDirectory() as tmp:
            response = bridge.consult_with_retry(
                request,
                self._backend(server, tmp),
                retries=0,
                allow_restricted=False,
                backend_name="omlx",
            )
        self.assertEqual(response["state"], "completed")
        auth, body = handler.seen[0]
        self.assertEqual(auth, "Bearer secret-test-key")
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["model"], bridge.DEFAULT_OMLX_MODEL)
        self.assertEqual(body["response_format"]["type"], "json_schema")

    def test_omlx_effort_low_in_round_1_xhigh_later_explicit_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(self._server(lambda body: (200, {}))[0], tmp)
            r1 = bridge.validate_request(valid_request())
            r2 = bridge.validate_request(valid_request(consult_index=2, consult_limit=3))
            self.assertEqual(backend.effort_for(r1), "low")
            self.assertEqual(backend.effort_for(r2), "xhigh")
            backend.explicit_effort = "medium"
            self.assertEqual(backend.effort_for(r2), "medium")

    def test_scripted_backend_fills_protocol_fields_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canned.json"
            path.write_text(json.dumps({
                "decision": "change",
                "recommendation": "Change X.",
                "reason": "Because.",
                "change_evidence": "A counter-example.",
                "executor_next_step": "Apply.",
            }), encoding="utf-8")
            request = bridge.validate_request(valid_request())
            response = bridge.consult_with_retry(
                request, bridge.ScriptedBackend(path), retries=0,
                allow_restricted=False, backend_name="scripted",
            )
        self.assertEqual(response["state"], "completed")
        self.assertEqual(response["decision"], "change")
        self.assertEqual(response["id"], request["id"])

    def test_omlx_low_effort_gets_a_smaller_token_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(self._server(lambda body: (200, {}))[0], tmp)
            r1 = bridge.validate_request(valid_request())
            r2 = bridge.validate_request(valid_request(consult_index=2, consult_limit=3))
            self.assertEqual(backend.max_tokens_for(r1), 6000)
            self.assertEqual(backend.max_tokens_for(r2), 16384)

    def test_omlx_drops_response_format_when_server_rejects_it(self):
        request = bridge.validate_request(valid_request())

        def body_fn(body):
            if "response_format" in body:
                return 400, {"error": "unsupported response_format"}
            return 200, {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self._ok(request))}}]}

        server, handler = self._server(body_fn)
        with tempfile.TemporaryDirectory() as tmp:
            response = self._backend(server, tmp).invoke(request)
        self.assertEqual(response["state"], "completed")
        self.assertEqual(len(handler.seen), 2)

    def test_omlx_truncated_output_is_malformed(self):
        request = bridge.validate_request(valid_request())
        server, _ = self._server(
            lambda body: (200, {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(bridge.MalformedResponse):
                self._backend(server, tmp).invoke(request)

    def test_omlx_rejects_evidence_read_scope(self):
        request = bridge.validate_request(
            valid_request(
                review_scope="evidence-read",
                evidence=[{"uri": "file:///tmp/a.txt", "locator": "lines 1-2"}],
            )
        )
        server, _ = self._server(lambda body: (200, {}))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(bridge.ProtocolError):
                self._backend(server, tmp).invoke(request)

    def test_missing_key_file_is_backend_error(self):
        with self.assertRaises(bridge.BackendError):
            bridge.OmlxBackend(
                base_url="http://127.0.0.1:1/v1",
                model=None,
                effort=None,
                key_file=Path("/nonexistent/omlx-key"),
                timeout_seconds=1,
                max_output_bytes=1024,
            )

    def test_chain_fails_over_and_restricted_only_reaches_local(self):
        class Boom:
            def invoke(self, request, **kwargs):
                raise bridge.BackendError("down")

        ok = self._ok

        class Good:
            def invoke(self, request, **kwargs):
                return ok(request)

        reached = []

        def factory(name, backend):
            def build():
                reached.append(name)
                return backend

            return build

        request = valid_request()
        response = bridge.consult_chain(
            request,
            [("codex", factory("codex", Boom())), ("omlx", factory("omlx", Good()))],
            retries=0,
            allow_restricted=False,
        )
        self.assertEqual(response["state"], "completed")
        self.assertEqual(reached, ["codex", "omlx"])

        reached.clear()
        response = bridge.consult_chain(
            valid_request(data_classification="restricted"),
            [("codex", factory("codex", Good())), ("omlx", factory("omlx", Good()))],
            retries=0,
            allow_restricted=False,
        )
        self.assertEqual(response["state"], "completed")
        self.assertEqual(reached, ["omlx"])

        response = bridge.consult_chain(
            valid_request(data_classification="restricted"),
            [("codex", factory("codex", Good()))],
            retries=0,
            allow_restricted=True,
        )
        self.assertEqual(response["state"], "rejected")


class TolerantJsonTests(unittest.TestCase):
    def good(self):
        return {"protocol": bridge.PROTOCOL_VERSION, "id": "x"}

    def test_object_inside_prose_and_code_fence(self):
        body = json.dumps(self.good())
        for text in (f"Here is my answer:\n{body}\nHope that helps", f"```json\n{body}\n```"):
            out = bridge.extract_structured_response(text.encode(), max_output_bytes=100000)
            self.assertEqual(out["id"], "x")

    def test_skips_unrelated_objects_and_still_rejects_garbage(self):
        text = '{"note": 1} then ' + json.dumps(self.good())
        self.assertEqual(bridge.extract_structured_response(text.encode(), max_output_bytes=100000)["id"], "x")
        with self.assertRaises(bridge.MalformedResponse):
            bridge.extract_structured_response(b"no json here", max_output_bytes=1000)
        with self.assertRaises(bridge.MalformedResponse):
            bridge.extract_structured_response(b'{"note": 1}', max_output_bytes=1000)


class OmlxEvidenceToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "a.syscfg"
        self.file.write_text("\n".join(f"line {n} PA{n}" for n in range(1, 401)), encoding="utf-8")
        self.secret = Path(self.tmp.name) / "secret.txt"
        self.secret.write_text("TOP SECRET", encoding="utf-8")
        self.uri = f"file://{self.file}"

    def request(self):
        return bridge.validate_request(
            valid_request(
                review_scope="evidence-read",
                evidence=[{"uri": self.uri, "locator": "whole file"}],
            )
        )

    def test_tools_are_bounded_and_allowlisted(self):
        tools = bridge.EvidenceTools(self.request())
        out = tools.call("read_lines", {"uri": self.uri, "start": 10, "end": 5000})
        self.assertEqual(out.splitlines()[0], "10: line 10 PA10")
        self.assertEqual(len(out.splitlines()), bridge.OMLX_READ_MAX_LINES)
        hits = tools.call("grep", {"uri": self.uri, "pattern": "pa1"})
        self.assertIn("more matches not shown", hits)
        self.assertEqual(tools.call("grep", {"uri": self.uri, "pattern": "nomatch"}), "(no matches)")
        self.assertTrue(tools.call("read_lines", {"uri": str(self.secret), "start": 1, "end": 1}).startswith("ERROR"))
        self.assertTrue(tools.call("grep", {"uri": "../../etc/passwd", "pattern": "x"}).startswith("ERROR"))
        self.assertTrue(tools.call("grep", {"uri": self.uri, "pattern": "("}).startswith("ERROR"))
        self.assertTrue(tools.call("delete", {}).startswith("ERROR"))
        self.assertEqual(tools.opened, {self.uri})

    def test_symlink_escape_is_rejected(self):
        link = Path(self.tmp.name) / "link.txt"
        link.symlink_to(self.secret)
        request = bridge.validate_request(
            valid_request(
                review_scope="evidence-read",
                evidence=[{"uri": self.uri, "locator": "x"}],
            )
        )
        tools = bridge.EvidenceTools(request)
        self.assertTrue(tools.call("read_lines", {"uri": str(link), "start": 1, "end": 1}).startswith("ERROR"))

    def test_tool_loop_fills_evidence_checked_and_returns_response(self):
        request = self.request()
        ok = OmlxAndChainTests._ok(self, request)
        replies = [
            {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "grep", "arguments": json.dumps({"uri": self.uri, "pattern": "line 7 "})}}
            ]}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ok)}}]},
        ]
        seen = []

        def handler(body):
            seen.append(body)
            return 200, replies[len(seen) - 1]

        helper = OmlxAndChainTests()
        server, _ = helper._server(handler)
        self.addCleanup(lambda: None)
        backend = helper._backend(server, self.tmp.name)
        response = bridge.consult_with_retry(
            request, backend, retries=0, allow_restricted=False, backend_name="omlx"
        )
        self.assertEqual(response["state"], "completed")
        self.assertEqual(response["evidence_checked"], [self.uri])
        self.assertIn("tools", seen[0])
        self.assertNotIn("response_format", seen[0])
        tool_messages = [m for m in seen[1]["messages"] if m["role"] == "tool"]
        self.assertIn("7: line 7 PA7", tool_messages[0]["content"])

    def test_answer_without_grep_is_sent_back_once_then_accepted_after_grep(self):
        request = self.request()
        ok = OmlxAndChainTests._ok(self, request)
        final = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ok)}}]}
        grep = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "grep", "arguments": json.dumps({"uri": self.uri, "pattern": "line 7 "})}}
        ]}}]}
        replies = [final, grep, final]
        seen = []

        def handler(body):
            seen.append(body)
            return 200, replies[len(seen) - 1]

        helper = OmlxAndChainTests()
        server, _ = helper._server(handler)
        response = helper._backend(server, self.tmp.name).invoke(request)
        self.assertEqual(len(seen), 3)
        self.assertIn("without checking the file", seen[1]["messages"][-1]["content"])
        self.assertIn("bridge verification log: grep(line 7 )", response["reason"])

    def test_reviewer_that_never_greps_fails(self):
        request = self.request()
        ok = OmlxAndChainTests._ok(self, request)
        final = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ok)}}]}
        helper = OmlxAndChainTests()
        server, _ = helper._server(lambda body: (200, final))
        with self.assertRaises(bridge.MalformedResponse):
            helper._backend(server, self.tmp.name).invoke(request)

    def test_proceed_must_address_an_inference_in_the_executor_position(self):
        request = self.request()
        request["executor_position"] = "cmpsslite3 base is unset (defaults to CMPSS0)."
        ok = OmlxAndChainTests._ok(self, request)
        ok["reason"] = "Confirmed the base is unset."          # silent about the 'default' claim
        ok["recommendation"] = "Proceed."
        grep = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "grep", "arguments": json.dumps({"uri": self.uri, "pattern": "line 7 "})}}
        ]}}]}
        final = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ok)}}]}
        replies = [grep, final]
        seen = []

        def handler(body):
            seen.append(body)
            return 200, replies[min(len(seen) - 1, 1)]

        helper = OmlxAndChainTests()
        server, _ = helper._server(handler)
        with self.assertRaises(bridge.MalformedResponse) as ctx:
            helper._backend(server, self.tmp.name).invoke(request)
        self.assertIn("default", str(ctx.exception))
        ok["reason"] = "Confirmed unset by grep; the claim that it defaults to CMPSS0 is not in the file."
        ok["decision"] = "change"
        self.assertEqual(ok["decision"], "change")   # change is never blocked by this rule

    def test_endless_tool_calls_end_as_malformed(self):
        request = self.request()
        call = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "grep", "arguments": json.dumps({"uri": self.uri, "pattern": "x"})}}
        ]}}]}
        helper = OmlxAndChainTests()
        server, handler = helper._server(lambda body: (200, call))
        backend = helper._backend(server, self.tmp.name)
        with self.assertRaises(bridge.MalformedResponse):
            backend.invoke(request)
        self.assertEqual(len(handler.seen), bridge.OMLX_MAX_TOOL_CALLS + 2)


if __name__ == "__main__":
    unittest.main()
