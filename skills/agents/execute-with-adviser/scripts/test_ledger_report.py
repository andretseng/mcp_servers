#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Tests for ledger_report.py with a synthetic ledger."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger_report as lr  # noqa: E402


def review(ctx, idx, backend, decision, state="completed", executor="pi:dm/Ornith", model="m", is_test=False, **kw):
    return {"event": "review-round", "ts": f"2026-10-12T10:00:{idx:02d}Z", "context_id": ctx, "consult_index": idx,
            "backend": backend, "model": model, "effort": "low", "decision": decision, "state": state,
            "executor": executor, "is_test": is_test, "latency_s": 10, "attempts": 1, "recommendation": "rec", **kw}


def disp(ctx, rnd, verdict):
    return {"event": "disposition", "context_id": ctx, "round": rnd, "verdict": verdict}


class ReportTests(unittest.TestCase):
    def report(self, reviews, dispositions, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "reviews.jsonl").write_text("\n".join(json.dumps(r) for r in reviews), encoding="utf-8")
            (d / "dispositions.jsonl").write_text("\n".join(json.dumps(r) for r in dispositions), encoding="utf-8")
            return lr.build(d, **kw)

    def outcome(self, rep, ctx):
        return next(c["outcome"] for c in rep["contexts"] if c["context_id"] == ctx)

    def test_each_outcome(self):
        reviews = [
            review("a", 1, "omlx", "proceed"),
            review("b", 1, "omlx", "change"),
            review("c", 1, "omlx", "change"), review("c", 2, "omlx", "change"), review("c", 3, "codex", "proceed"),
            review("d", 1, "omlx", "proceed"), review("d", 2, "omlx", "proceed"), review("d", 3, "codex", "change"),
            review("e", 1, "omlx", "change"), review("e", 2, "omlx", "change"), review("e", 3, "omlx", "proceed"),
            review("f", 1, "omlx", "proceed", executor="codex:Ornith"),
            review("g", 1, "omlx", "proceed", state="failed"),
            review("h", 1, "omlx", "change"), review("h", 2, "omlx", "change"), review("h", 3, "codex", "need-evidence"),
        ]
        disps = [
            disp("a", 1, "adopt"), disp("b", 1, "adopt"),
            disp("c", 1, "reject"), disp("c", 2, "reject"), disp("c", 3, "adopt"),
            disp("d", 1, "reject"), disp("d", 2, "reject"), disp("d", 3, "adopt"),
            disp("e", 1, "reject"), disp("e", 2, "reject"), disp("e", 3, "adopt"),
            disp("g", 1, "adopt"),
            disp("h", 1, "reject"), disp("h", 2, "reject"), disp("h", 3, "adopt"),
        ]
        rep = self.report(reviews, disps)
        got = {c: self.outcome(rep, c) for c in "abcdefgh"}
        self.assertEqual(got, {"a": "agreed_proceed", "b": "executor_accepted", "c": "reviewer_overruled",
                               "d": "executor_overruled", "e": "unresolved", "f": "agreed_proceed",
                               "g": "no_review", "h": "inconclusive"})

    def test_dispositions_are_inferred_from_the_round_sequence_when_not_logged(self):
        reviews = [
            review("c1", 1, "omlx", "change", executor="codex:omlx/Ornith"), review("c1", 2, "omlx", "change", executor="codex:omlx/Ornith"),
            review("c1", 3, "codex", "proceed", executor="codex:omlx/Ornith"),
            review("c2", 1, "omlx", "change", executor="codex:omlx/Ornith"),
            review("c3", 1, "omlx", "need-evidence", executor="codex:omlx/Ornith"), review("c3", 2, "omlx", "proceed", executor="codex:omlx/Ornith"),
        ]
        rep = self.report(reviews, [])
        self.assertEqual(self.outcome(rep, "c1"), "reviewer_overruled")     # two challenges, then Codex proceed
        self.assertEqual(self.outcome(rep, "c2"), "executor_accepted")      # single flagged round, assumed adopted
        self.assertEqual(self.outcome(rep, "c3"), "executor_accepted")      # need-evidence is not a rejection
        self.assertTrue(all(c["inferred"] for c in rep["contexts"]))
        self.assertIn("inferred from the round sequence", lr.render(rep))

    def test_test_backends_excluded_by_default_and_rates_rendered(self):
        reviews = [review("x", 1, "scripted", "change", is_test=True), review("y", 1, "omlx", "proceed")]
        disps = [disp("y", 1, "adopt")]
        rep = self.report(reviews, disps)
        self.assertEqual([c["context_id"] for c in rep["contexts"]], ["y"])
        self.assertEqual(len(self.report(reviews, disps, include_test=True)["contexts"]), 2)
        text = lr.render(rep)
        self.assertIn("pi:dm/Ornith", text)
        self.assertIn("1/1 (100%)", text)

    def test_reviewer_failure_stats_and_codex_section(self):
        reviews = [review("p", 1, "omlx", "proceed", state="timeout", attempts=2), review("p", 2, "codex", "change")]
        rep = self.report(reviews, [])
        self.assertEqual(rep["reviewers"]["omlx:m"]["failed"], 1)
        self.assertEqual(rep["reviewers"]["omlx:m"]["attempts_gt1"], 1)
        self.assertEqual(len(rep["codex_rounds"]), 1)

    def test_empty_ledger_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(lr.main(["--dir", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
