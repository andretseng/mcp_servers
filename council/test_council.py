#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Tests for council.py S1 (item parser + citation checker). Run: uv run --script test_council.py -v

The key tests are the ones that must FAIL a citation: a check that has never been seen to fail proves nothing."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import council  # noqa: E402

C_SRC = """\
#include <stdint.h>
// DShot telemetry runs 2.5 times the DShot bit rate
int m = 3;
uint32_t dshotarr2 = 375000 << m;  /* load value */
"""


def item(id_, basis, evidence, statement="s", check="c"):
    return f"ITEM {id_}\nstatement: {statement}\nbasis: {basis}\nevidence: {evidence}\ncheck: {check}\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "io.c").write_text(C_SRC)
        (self.tmp / "tool.py").write_text("# the answer is 42\nx = 6 * 7\n")
        self.outside = Path(tempfile.mkdtemp()).resolve()
        (self.outside / "secret.c").write_text("int leaked = 1;\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def run_one(self, basis, evidence, roots=None):
        items, problems = council.parse_items(item("A1", basis, evidence))
        self.assertEqual(problems, [])
        return council.resolve_item(items[0], roots or [self.tmp])


class ParserTests(unittest.TestCase):
    def test_two_items_and_fields(self):
        text = item("A1", "verified", 'src/io.c:3 "int m = 3;"') + "\n" + item("A2", "inferred", "measurement: scope 1.3us")
        items, problems = council.parse_items(text)
        self.assertEqual([i.id for i in items], ["A1", "A2"])
        self.assertEqual(items[0].fields["basis"], "verified")
        self.assertEqual(problems, [])

    def test_continuation_lines_join_the_previous_field(self):
        text = 'ITEM B1\nstatement: first\n  second line\nbasis: inferred\nevidence: measurement: a\n  b\ncheck: c\n'
        items, problems = council.parse_items(text)
        self.assertEqual(items[0].fields["statement"], "first\nsecond line")
        self.assertEqual(problems, [])

    def test_trailing_prose_after_blank_line_is_not_swallowed(self):
        text = item("A1", "inferred", "measurement: x") + "\nSome closing remarks.\n"
        items, _ = council.parse_items(text)
        self.assertNotIn("closing", items[0].fields["check"])

    def test_format_problems_are_reported(self):
        text = "ITEM A1\nstatement: s\nbasis: sure\ncheck: c\n\n" + item("A1", "verified", "measurement: x")
        _, problems = council.parse_items(text)
        joined = "\n".join(problems)
        self.assertIn("invalid basis 'sure'", joined)
        self.assertIn("missing or empty field 'evidence'", joined)
        self.assertIn("duplicate item id", joined)

    def test_no_items_is_a_problem(self):
        _, problems = council.parse_items("just prose")
        self.assertEqual(problems, ["no ITEM blocks found"])


class CitationTests(Base):
    def test_correct_quote_keeps_verified(self):
        r = self.run_one("verified", 'src/io.c:3 "int m = 3;"')
        self.assertEqual(r["basis_final"], "verified")
        self.assertFalse(r["downgraded"])

    def test_multiline_range_and_whitespace_difference(self):
        r = self.run_one("verified", 'src/io.c:3-4 "int   m = 3;  uint32_t dshotarr2"')
        self.assertEqual(r["basis_final"], "verified")

    def test_a_path_written_from_above_the_root_still_resolves(self):
        r = self.run_one("verified", 'src/io.c:3 "int m = 3;"', roots=[self.tmp / "src"])
        self.assertEqual(r["basis_final"], "verified")

    def test_the_root_prefix_trick_cannot_leave_the_root(self):
        r = self.run_one("verified", 'src/../tool.py:2 "x = 6 * 7"', roots=[self.tmp / "src"])
        self.assertEqual(r["basis_final"], "unverified")
        self.assertIn("outside", r["notes"][0])

    def test_all_paths_wrong_is_reported_as_a_problem_but_one_good_path_is_not(self):
        bad = item("A1", "verified", 'nope/a.c:1 "x"; nope/b.c:2 "y"; nope/c.c:3 "z"')
        items, _ = council.parse_items(bad)
        self.assertEqual(len(council.path_problems(items, [self.tmp])), 1)
        mixed, _ = council.parse_items(item("A1", "verified", 'nope/a.c:1 "x"; nope/b.c:2 "y"; src/io.c:3 "int m = 3;"'))
        self.assertEqual(council.path_problems(mixed, [self.tmp]), [])

    # ---- the checks must be able to fail ----
    def test_broken_quote_is_downgraded(self):
        r = self.run_one("verified", 'src/io.c:3 "int m = 2;"')
        self.assertEqual(r["basis_final"], "unverified")
        self.assertIn("quote not found", r["notes"][0])

    def test_right_quote_wrong_line_is_downgraded(self):
        r = self.run_one("verified", 'src/io.c:1 "int m = 3;"')
        self.assertEqual(r["basis_final"], "unverified")

    def test_range_beyond_end_of_file_is_downgraded(self):
        r = self.run_one("verified", 'src/io.c:90-95 "int m = 3;"')
        self.assertEqual(r["basis_final"], "unverified")
        self.assertIn("beyond end of file", r["notes"][0])

    def test_missing_file_is_downgraded(self):
        r = self.run_one("verified", 'src/nope.c:1 "x"')
        self.assertEqual(r["basis_final"], "unverified")
        self.assertIn("file not found", r["notes"][0])

    def test_file_outside_roots_is_downgraded_even_if_the_quote_is_right(self):
        r = self.run_one("verified", f'{self.outside}/secret.c:1 "int leaked = 1;"')
        self.assertEqual(r["basis_final"], "unverified")
        self.assertIn("outside the allowed", r["notes"][0])

    def test_dotdot_escape_is_downgraded(self):
        rel = Path("..") / self.outside.name / "secret.c"
        r = self.run_one("verified", f'{rel}:1 "int leaked = 1;"')
        self.assertEqual(r["basis_final"], "unverified")

    def test_comment_only_citation_is_downgraded_c(self):
        r = self.run_one("verified", 'src/io.c:2 "2.5 times the DShot bit rate"')
        self.assertEqual(r["basis_final"], "unverified")
        self.assertTrue(r["citations"][0]["comment_only"])

    def test_comment_only_citation_is_downgraded_python(self):
        r = self.run_one("verified", 'tool.py:1 "the answer is 42"')
        self.assertEqual(r["basis_final"], "unverified")

    def test_shebang_is_not_a_comment(self):
        (self.tmp / "run.sh").write_text("#!/usr/bin/env bash\necho hi\n")
        r = self.run_one("verified", 'run.sh:1 "#!/usr/bin/env bash"')
        self.assertEqual(r["basis_final"], "verified")

    def test_code_with_trailing_comment_counts_as_code(self):
        r = self.run_one("verified", 'src/io.c:4 "load value"')
        self.assertEqual(r["basis_final"], "verified")

    def test_verified_without_any_recognisable_evidence_is_downgraded(self):
        r = self.run_one("verified", "I read it somewhere")
        self.assertEqual(r["basis_final"], "unverified")

    def test_one_good_citation_saves_the_item_but_the_bad_one_is_flagged(self):
        r = self.run_one("verified", 'src/io.c:3 "int m = 3;" ; src/io.c:3 "int m = 9;"')
        self.assertEqual(r["basis_final"], "verified")
        self.assertTrue(any(n.startswith("warning") for n in r["notes"]))

    def test_url_or_measurement_only_stays_verified_but_is_flagged(self):
        for ev in ('https://example.com/spec "5/4 x current DShot bitrate"', "measurement: scope shows 2.67 us bit"):
            r = self.run_one("verified", ev)
            self.assertEqual(r["basis_final"], "verified")
            self.assertTrue(any("not machine-checked" in n for n in r["notes"]), ev)

    def test_url_with_port_is_not_mistaken_for_a_file_citation(self):
        cites = council.find_citations('http://host:8080/spec "a quote"')
        self.assertEqual([c["kind"] for c in cites], ["url"])

    def test_inferred_and_unverified_items_are_never_changed(self):
        for basis in ("inferred", "unverified"):
            r = self.run_one(basis, 'src/io.c:3 "this quote is wrong"')
            self.assertEqual(r["basis_final"], basis)
            self.assertFalse(r["downgraded"])


class CliTests(Base):
    def run_cli(self, text, *extra):
        f = self.tmp / "items.md"
        f.write_text(text)
        return subprocess.run([sys.executable, str(HERE / "council.py"), "check-cites", str(f), "--root", str(self.tmp), *extra],
                              capture_output=True, text=True)

    def test_json_report_and_exit_zero(self):
        p = self.run_cli(item("A1", "verified", 'src/io.c:3 "int m = 3;"') + "\n" + item("A2", "verified", 'src/io.c:3 "int m = 7;"'))
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertEqual([i["basis_final"] for i in rep["items"]], ["verified", "unverified"])

    def test_format_problem_gives_exit_one_but_still_reports(self):
        p = self.run_cli("ITEM A1\nstatement: s\n")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(json.loads(p.stdout)["problems"])

    def test_unreadable_file_gives_exit_two(self):
        p = subprocess.run([sys.executable, str(HERE / "council.py"), "check-cites", str(self.tmp / "missing.md")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_text_format(self):
        p = self.run_cli(item("A1", "verified", 'src/io.c:3 "int m = 9;"'), "--format", "text")
        self.assertIn("verified -> unverified", p.stdout)

    @unittest.skipUnless(shutil.which("uv"), "uv not installed")
    def test_runs_as_a_uv_script(self):
        f = self.tmp / "items.md"
        f.write_text(item("A1", "inferred", "measurement: x"))
        p = subprocess.run(["uv", "run", "--script", str(HERE / "council.py"), "check-cites", str(f), "--root", str(self.tmp)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
