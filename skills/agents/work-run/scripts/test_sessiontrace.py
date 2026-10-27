from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sessiontrace as tr  # noqa: E402


def msg(finish, inp, out, content=None):
    return {"type": "assistant", "finish": finish, "tokens": {"input": inp, "output": out}, "content": content or []}


def export(msgs, extra=None):
    return {"info": {"title": "work", "outcome": "succeeded", "time": {"created": 0, "updated": 10000}}, "messages": [{"type": "user", "text": "task"}] + msgs + (extra or [])}


class TraceTests(unittest.TestCase):
    def test_output_limit_hit_and_share_of_the_limit(self):
        s = tr.summarize(export([msg("tool-calls", 100, 20), msg("length", 300, 32768)]))
        self.assertTrue(s["output_limit_hit"])
        self.assertEqual(s["truncated"], 1)
        self.assertEqual(s["output_max_pct"], 100 if s["output_limit"] == 32768 else s["output_max_pct"])
        self.assertIn("OUTPUT-LIMIT HIT", tr.render([{**s, "session": "x"}]))
        self.assertFalse(tr.summarize(export([msg("stop", 100, 20)]))["output_limit_hit"])

    def test_a_sharp_fall_of_the_context_is_a_compaction_drop(self):
        s = tr.summarize(export([msg("tool-calls", 30000, 10), msg("tool-calls", 90000, 10), msg("tool-calls", 20000, 10), msg("stop", 25000, 10)]))
        self.assertEqual(s["context_drops"], [[2, 90000, 20000]])
        self.assertIn("COMPACTION", tr.render([{**s, "session": "x"}]))
        self.assertEqual(tr.summarize(export([msg("tool-calls", 30000, 10), msg("tool-calls", 90000, 10)]))["context_drops"], [])

    def test_the_cache_is_part_of_the_context_and_a_cache_hit_is_not_a_compaction(self):
        def cached(inp, read):
            m = msg("tool-calls", inp, 10)
            m["tokens"]["cache"] = {"read": read, "write": 0}
            return m
        s = tr.summarize(export([cached(30000, 0), cached(5000, 60000), cached(8000, 62000)]))
        self.assertEqual(s["context_max"], 70000)
        self.assertEqual(s["context_drops"], [])

    def test_a_summary_message_counts_as_a_compaction_marker(self):
        s = tr.summarize(export([msg("tool-calls", 30000, 10)], extra=[{"type": "compaction", "text": "summary of the work so far"}]))
        self.assertEqual(s["compaction_markers"], 1)
        self.assertEqual(tr.summarize(export([msg("stop", 100, 1)]))["compaction_markers"], 0)

    def test_the_handoff_view_shows_the_summary_and_what_the_model_did_next(self):
        tool = {"type": "tool", "name": "read", "state": {"input": {"path": "/x/main.c"}}}
        ex = export([msg("tool-calls", 90000, 10), {"type": "compaction", "text": "Summary: request, draft path, findings"}, msg("tool-calls", 20000, 10, [tool])])
        view = "\n".join(tr.handoff_view(ex))
        self.assertIn("Summary: request, draft path, findings", view)
        self.assertIn("read", view)
        self.assertEqual(tr.handoff_view(export([msg("stop", 100, 1)])), ["no compaction found"])


if __name__ == "__main__":
    unittest.main()
