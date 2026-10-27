#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Tests for adviser_review.py with a stub bridge (no adviser is invoked)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adviser_review as review  # noqa: E402

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "adviser_review.py"

STUB = '''
import json, sys
a = sys.argv
req = json.load(open(a[a.index("--request") + 1]))
assert all(e.get("excerpt") for e in req["evidence"]), "unpacked evidence reached the bridge"
assert req["review_scope"] == "summary-only"
mode = open(__file__ + ".mode").read().strip()
open(__file__ + ".argv", "w").write(json.dumps(a))
print(json.dumps({"state": "completed" if mode == "ok" else "input-required", "decision": "proceed" if mode == "ok" else "need-evidence", "recommendation": "R " + "x" * 3000, "change_evidence": "supply main.c L10-12 and config.h CLK_MHZ"}))
sys.exit(0 if mode == "ok" else 4)
'''


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "src").mkdir()
        (t / "src" / "main.c").write_text("".join(f"line {i}\n" for i in range(1, 200)), encoding="utf-8")
        self.note = t / "note.md"
        self.note.write_text("# Note\nerpm = 60000000/ertm (main.c:120)\n", encoding="utf-8")
        self.findings = t / "f.md"
        self.findings.write_text("- erpm | main.c:120 | verified\n", encoding="utf-8")
        self.stub = t / "stub_bridge.py"
        self.stub.write_text(STUB, encoding="utf-8")
        (t / "stub_bridge.py.mode").write_text("need", encoding="utf-8")
        (t / "home" / ".local" / "state" / "adviser-ledger").mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(t / "home")}
        self.t = t
        self.cid = review.default_context_id(self.note.resolve(), str(self.findings.resolve()))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *extra, question="Does the note match the source?"):
        cmd = [sys.executable, str(SCRIPT), "--note", str(self.note), "--findings", str(self.findings), "--root", str(self.t),
               "--question", question, "--goal", "Check the note.", "--position", "I believe it matches.",
               "--data-class", "public", "--executor", "test:stub", "--bridge", str(self.stub), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, env=self.env)

    def ledger(self, rows):
        p = self.t / "home" / ".local" / "state" / "adviser-ledger" / "reviews.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_tagged_and_scope_file_reach_the_request(self):
        scope = self.t / "scope.md"
        scope.write_text("# J\n## Question\nHow does the period become the output value?\n## Out of scope\n- telemetry encoding\n- debug pins\n", encoding="utf-8")
        self.run_cli("--tagged", "--scope-file", str(scope))
        saved = Path(self.env["HOME"]) / ".local" / "state" / "agent-checkpoints" / "reviews" / self.cid / "round-1.request.json"
        req = json.loads(saved.read_text(encoding="utf-8"))
        joined = " ".join(req["constraints"])
        self.assertIn("[B] <id>", joined)
        self.assertIn("telemetry encoding; debug pins", joined)
        self.assertTrue(req["goal"].startswith("TASK (verbatim): How does the period become"))

    def test_every_request_makes_the_reviewer_check_the_premises_not_only_the_wording(self):
        self.run_cli()
        saved = Path(self.env["HOME"]) / ".local" / "state" / "agent-checkpoints" / "reviews" / self.cid / "round-1.request.json"
        joined = " ".join(json.loads(saved.read_text(encoding="utf-8"))["constraints"])
        self.assertIn("a code comment, a variable name, or a note is a claim, not evidence", joined)
        self.assertIn("your own knowledge of the specification", joined)
        self.assertIn("outside the supplied evidence", joined)

    def test_chinese_question_is_rejected_before_any_round(self):
        out = self.run_cli(question="請檢查這份筆記是否與原始碼一致嗎")
        self.assertEqual(out.returncode, 2)
        self.assertIn("English", out.stderr)

    def test_need_evidence_prints_short_summary_and_saves_full_response(self):
        out = self.run_cli()
        self.assertEqual(out.returncode, 4, out.stderr)
        self.assertIn("ADVISER ROUND 1/3", out.stdout)
        self.assertLess(len(out.stdout), 2400)  # the 3000-char answer is truncated in the summary
        saved = list((self.t / "home" / ".local" / "state" / "agent-checkpoints" / "reviews" / self.cid).glob("round-1.*"))
        self.assertEqual(sorted(p.name for p in saved), ["round-1.note_size", "round-1.request.json", "round-1.response.json"])

    def test_round_two_needs_changed_text(self):
        self.ledger([{"event": "review-round", "context_id": self.cid, "state": "input-required", "backend": "codex", "decision": "need-evidence"}])
        out = self.run_cli()
        self.assertEqual(out.returncode, 2)
        self.assertIn("--changed", out.stderr)

    def test_limit_blocks_without_spending_a_round(self):
        self.ledger([{"event": "review-round", "context_id": self.cid, "state": "input-required", "backend": "codex", "decision": "need-evidence"}] * 3)
        out = self.run_cli("--changed", "Fixed things.")
        self.assertEqual(out.returncode, 3)
        self.assertIn("NOT APPROVED", out.stderr)

    def test_completed_round_is_not_repeated(self):
        self.ledger([{"event": "review-round", "context_id": self.cid, "state": "completed", "backend": "codex", "decision": "proceed"}])
        out = self.run_cli()
        self.assertEqual(out.returncode, 0)
        self.assertIn("already completed", out.stdout)
        self.assertIn("Adviser answer", out.stdout)

    def argv(self):
        return json.loads((self.t / "stub_bridge.py.argv").read_text(encoding="utf-8"))

    def test_public_goes_to_codex_with_claude_as_second_tier(self):
        self.run_cli()
        a = self.argv()
        self.assertEqual(a[a.index("--backend") + 1], "codex")
        self.assertEqual(a[a.index("--failover") + 1], "claude")

    def test_claude_can_be_chosen_for_public_and_has_no_failover(self):
        self.run_cli("--backend", "claude")
        a = self.argv()
        self.assertEqual(a[a.index("--backend") + 1], "claude")
        self.assertNotIn("--failover", a)

    def test_restricted_never_goes_to_a_cloud_backend(self):
        for cloud in ("codex", "claude"):
            out = self.run_cli("--data-class", "restricted", "--backend", cloud)
            self.assertEqual(out.returncode, 3, cloud)
            self.assertIn("restricted data may only be reviewed by the local omlx backend", out.stderr)

    def test_omlx_is_not_a_reviewer_for_public(self):
        out = self.run_cli("--backend", "omlx")
        self.assertEqual(out.returncode, 3)
        self.assertIn("omlx is not a reviewer", out.stderr)

    def test_a_claude_round_after_failover_counts_as_the_review(self):
        self.ledger([{"event": "review-round", "context_id": self.cid, "state": "completed", "backend": "claude", "decision": "proceed"}])
        out = self.run_cli()
        self.assertEqual(out.returncode, 0)
        self.assertIn("already completed", out.stdout)

    def test_new_session_findings_get_a_fresh_context_id(self):
        a = review.default_context_id(Path("/v/n.md"), "/state/findings-ses_A.md")
        b = review.default_context_id(Path("/v/n.md"), "/state/findings-ses_B.md")
        self.assertNotEqual(a, b)

    def test_unresolvable_finding_fails_before_the_adviser(self):
        self.findings.write_text("- x | nothere.c:5 | verified\n", encoding="utf-8")
        out = self.run_cli()
        self.assertEqual(out.returncode, 4)
        self.assertIn("no adviser round was spent", out.stderr)

    def test_context_id_is_stable_ascii_for_chinese_note_names(self):
        a = review.default_context_id(Path("/v/Project/ESCape32_eRPM_opencode試作.md"), "f")
        b = review.default_context_id(Path("/v/Project/ESCape32_eRPM_opencode比較.md"), "f")
        self.assertTrue(a.isascii())
        self.assertNotEqual(a, b)
        self.assertEqual(a, review.default_context_id(Path("/v/Project/ESCape32_eRPM_opencode試作.md"), "f"))

    def test_change_decision_is_not_final_and_allows_a_confirming_round(self):
        self.ledger([{"event": "review-round", "context_id": self.cid, "state": "completed", "backend": "codex", "decision": "change"}])
        no_changed = self.run_cli()
        self.assertEqual(no_changed.returncode, 2)  # round 2 needs --changed, it is not "already completed"
        self.assertIn("--changed", no_changed.stderr)
        (self.t / "stub_bridge.py.mode").write_text("ok", encoding="utf-8")
        out = self.run_cli("--changed", "Fixed the sum wording.")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ADVISER ROUND 2/3", out.stdout)

    def test_adviser_requests_become_need_lines_and_note_growth_is_flagged(self):
        out = self.run_cli()
        self.assertEqual(out.returncode, 4, out.stderr)
        text = self.findings.read_text(encoding="utf-8")
        self.assertIn("NEED: main.c L10-12", text)
        self.assertIn("NEED: config.h", text)
        self.assertIn("NEED lines were added", out.stdout)
        self.ledger([{"event": "review-round", "context_id": self.cid, "state": "input-required", "backend": "codex", "decision": "need-evidence"}])
        self.note.write_text(self.note.read_text(encoding="utf-8") + "extra claim line\n" * 200, encoding="utf-8")
        out2 = self.run_cli("--changed", "Added detail.")
        self.assertIn("grew", out2.stdout)

    def test_request_asks_for_an_exhaustive_list(self):
        self.run_cli()
        req = json.loads(next((self.t / "home" / ".local" / "state" / "agent-checkpoints" / "reviews" / self.cid).glob("round-1.request.json")).read_text(encoding="utf-8"))
        self.assertTrue(any("EVERY contradiction" in c for c in req["constraints"]))

    def test_completed_path(self):
        (self.t / "stub_bridge.py.mode").write_text("ok", encoding="utf-8")
        out = self.run_cli()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Review approved", out.stdout)

    def test_many_findings_are_merged_to_fit_the_item_limit(self):
        (self.t / "src" / "big.c").write_text("".join(f"x {i}\n" for i in range(1, 1500)), encoding="utf-8")
        self.findings.write_text("".join(f"- fact {i} | big.c:{i * 40} | verified\n" for i in range(1, 36)), encoding="utf-8")
        out = self.run_cli()
        self.assertIn(out.returncode, (0, 4), out.stderr)
        req = json.loads(next((self.t / "home" / ".local" / "state" / "agent-checkpoints" / "reviews" / review.default_context_id(self.note.resolve(), str(self.findings.resolve()))).glob("round-1.request.json")).read_text(encoding="utf-8"))
        self.assertLessEqual(len(req["evidence"]), 24)
        self.assertTrue(any(e["uri"].endswith("note.md") for e in req["evidence"]))


if __name__ == "__main__":
    unittest.main()
