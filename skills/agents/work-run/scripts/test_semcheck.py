# python3 -m unittest test_semcheck : parsing, row selection and source lookup of semcheck.py (no model is called)
import tempfile, unittest
from pathlib import Path
import semcheck as sc


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "io.c").write_text("int a;\n// Telemetry rate is 2.5x\nx = CLK_CNT(clk, 375000 << m) - 1;\nreturn;\n")

    def test_verdict_parsing(self):
        self.assertEqual(sc.verdict("SUPPORTED")[0], "SUPPORTED")
        self.assertEqual(sc.verdict("NOT_SUPPORTED: the line is an ISR flag clear"), ("NOT_SUPPORTED", "the line is an ISR flag clear"))
        self.assertEqual(sc.verdict("`UNCLEAR` - needs the value of t")[0], "UNCLEAR")
        self.assertEqual(sc.verdict("I think so")[0], "UNCLEAR")  # no verdict is never read as support

    def test_only_verified_rows_are_checked(self):
        rows = [{"claim": "a", "basis": "verified", "evidence": "x"}, {"claim": "b", "basis": "inferred", "evidence": "y"},
                {"claim": "c", "basis": "`verified`+calc", "evidence": "z"}, {"claim": "d", "basis": "external", "evidence": "u"}]
        res = sc.check_rows(rows, [self.d], fast=lambda p: "SUPPORTED", slow=None)
        self.assertEqual([r["row"] for r in res], [1, 3])

    def test_stage_two_decides_only_for_flagged_rows(self):
        calls = {"slow": 0}
        def slow(p):
            calls["slow"] += 1
            return "SUPPORTED"
        rows = [{"claim": "a", "basis": "verified", "evidence": "io.c:3"}, {"claim": "b", "basis": "verified", "evidence": "io.c:3"}]
        fast = lambda p: "NOT_SUPPORTED: pedantic" if "Claim: a" in p else "SUPPORTED"
        res = sc.check_rows(rows, [self.d], fast=fast, slow=slow)
        self.assertEqual(calls["slow"], 1)  # the supported row is never re-asked
        self.assertEqual([r["verdict"] for r in res], ["SUPPORTED", "SUPPORTED"])  # stage two cleared the false alarm
        self.assertEqual(res[0]["first"][0], "NOT_SUPPORTED")  # and the first opinion is kept

    def test_no_source_row_is_not_sent_to_the_model(self):
        calls = []
        rows = [{"claim": "a", "basis": "verified", "evidence": "`never_in_any_file_xyz = 1;`"}]
        res = sc.check_rows(rows, [self.d], fast=lambda p: calls.append(p) or "SUPPORTED", slow=lambda p: calls.append(p) or "SUPPORTED", seen=[self.d / "io.c"])
        self.assertEqual(calls, [])  # fault injection: no evidence lines -> the model is never asked
        self.assertEqual(res[0]["verdict"], "NO_SOURCE")
        self.assertIn("could not be checked", sc.report(res))

    def test_time_budget_skips_stage_two_but_keeps_the_flag(self):
        rows = [{"claim": "a", "basis": "verified", "evidence": "io.c:3"}] * 2
        slow_calls = []
        res = sc.check_rows(rows, [self.d], fast=lambda p: "UNCLEAR: x", slow=lambda p: slow_calls.append(p) or "SUPPORTED", seen=[self.d / "io.c"], budget=-1)
        self.assertEqual(slow_calls, [])  # fault injection: budget already used up
        self.assertTrue(all(r["verdict"] == "UNCLEAR" and "stage 2 skipped" in r["why"] for r in res))

    def test_stage_two_can_confirm(self):
        rows = [{"claim": "a", "basis": "verified", "evidence": "io.c:3"}]
        res = sc.check_rows(rows, [self.d], fast=lambda p: "UNCLEAR: x", slow=lambda p: "NOT_SUPPORTED: wrong line")
        self.assertEqual(res[0]["verdict"], "NOT_SUPPORTED")

    def test_seen_files_limit_where_evidence_is_looked_up(self):
        other = self.d / "other.c"; other.write_text("x = CLK_CNT(clk, 375000 << m) - 1;\n")
        self.assertEqual(sc.source_files([], [self.d / "io.c", self.d / "gone.c"]), [self.d / "io.c"])
        self.assertIn("3: x = CLK_CNT", sc.quote_context("`x = CLK_CNT(clk, 375000 << m) - 1;`", [self.d / "io.c"]))
        self.assertEqual(sc.quote_context("`x = CLK_CNT(clk, 375000 << m) - 1;`", [self.d / "missing.c"]), "")

    def test_quote_is_found_in_source_without_a_line_number(self):
        ctx = sc.quote_context("`x = CLK_CNT(clk, 375000 << m) - 1;`", [self.d / "io.c"])
        self.assertIn("3: x = CLK_CNT", ctx)
        self.assertIn("2: // Telemetry", ctx)

    def test_path_only_quote_is_not_searched(self):
        self.assertEqual(sc.quote_context("`src/io.c:212`", [self.d / "io.c"]), "")

    def test_prompt_uses_cited_lines(self):
        p = sc.build_prompt({"claim": "c", "evidence": "io.c:3"}, [self.d / "io.c"])
        self.assertIn("375000", p)

    def test_report_lists_only_the_rows_to_confirm(self):
        out = sc.report([{"row": 1, "claim": "a", "verdict": "SUPPORTED", "why": ""}, {"row": 2, "claim": "b", "verdict": "NOT_SUPPORTED", "why": "wrong line"}])
        self.assertIn("row 2", out)
        self.assertNotIn("row 1", out)
        self.assertIn("2 verified rows: 1 supported, 1 to confirm, 0 could not be checked", out)


if __name__ == "__main__":
    unittest.main()
