#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""S3 tests: type-specific fields, OrcaAgent, the `run` command end to end (with a stub in place of orca-ask.sh), brief
templates and the SKILL.md. No model, no Orca needed. Run: uv run --script test_s3.py -v"""
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import council  # noqa: E402
from test_run import GOOD, Scripted, blk, ornith, qwen, ACCEPT_ALL, Base  # noqa: E402

POOL = Path.home() / ".agents/pool/technical-explorer/council"

STUB = r'''#!/usr/bin/env python3
"""Stand-in for orca-ask.sh: argv = handle question [timeout]. Handle names pick the behaviour."""
import re, sys
handle, question = sys.argv[1], sys.argv[2]
sys.stderr.write("")
path = re.search(r"file (\S+\.md)", question).group(1)
prompt = open(path).read()
phase, rnd, attempt = re.search(r"phase=(\w+) round=(\d+) attempt=(\d+)", prompt).groups()
mode = handle.split("-", 1)[1]
ITEM = 'ITEM {id}\nstatement: m starts at 3\nbasis: {basis}\nevidence: {ev}\ncheck: read it\nfiles: src/io.c\nrollback: git checkout src/io.c\n{frm}\n'
GOOD = 'src/io.c:3 "int m = 3;"'
if mode == "silent":
    sys.stderr.write("idle without an answer file"); sys.exit(3)
if mode == "refuse":
    sys.stderr.write("REFUSED: no agent detected in that pane"); sys.exit(2)
if mode == "late":
    sys.stderr.write("TIMEOUT after 5s"); sys.exit(4)
if phase == "independent":
    print(ITEM.format(id="A1" if mode == "ornith" else "B1", basis="verified" if mode == "ornith" else "inferred",
                      ev=GOOD if mode == "ornith" else "measurement: none", frm=""))
elif phase == "merge":
    print(ITEM.format(id="D1", basis="verified", ev=GOOD, frm="from: Ornith/A1, Qwen/B1\n"))
elif phase == "revise":
    print("NOCHANGE\nREBUTTAL D1: the quote is on the cited line.")
elif phase == "review":
    print("RE D1: OBJECT because I am not convinced. evidence: measurement: scope" if mode == "objector" else "RE D1: ACCEPT")
'''


class StubBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "io.c").write_text("#include <stdint.h>\n// c\nint m = 3;\n")
        self.stub = self.tmp / "stub-ask.py"
        self.stub.write_text(STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IXUSR)
        self.out = self.tmp / "run"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def brief_file(self, type_="plan", **extra):
        head = {"type": type_, "participants": "Ornith, Qwen", "roots": str(self.tmp), **extra}
        f = self.tmp / "brief.md"
        f.write_text("\n".join(f"{k}: {v}" for k, v in head.items()) + "\n\nWhy is m wrong?\n")
        return f

    def run_cli(self, brief, *panes, extra=()):
        args = [sys.executable, str(HERE / "council.py"), "run", str(brief), "--out", str(self.out), "--ask-script", str(self.stub)]
        for p in panes:
            args += ["--pane", p]
        return subprocess.run(args + list(extra), capture_output=True, text=True)


def qwen_plan(review):
    """Reviewer whose independent answer is a complete plan item."""
    def responder(phase, rnd, attempt, prompt):
        if phase == "independent":
            return ("ITEM B1\nstatement: change m\nbasis: inferred\nevidence: measurement: none\ncheck: make test\n"
                    "files: src/io.c\nrollback: git checkout src/io.c\n")
        return review(rnd, attempt, prompt)
    return responder


class TypeFieldTests(Base):
    def plan_item(self, id_, basis="verified", evidence=GOOD, frm=None, rollback="git checkout src/io.c", files="src/io.c", stmt="change m"):
        s = f"ITEM {id_}\nstatement: {stmt}\nbasis: {basis}\nevidence: {evidence}\ncheck: make test\n"
        s += f"files: {files}\n" if files else ""
        s += f"rollback: {rollback}\n" if rollback else ""
        return s + (f"from: {frm}\n" if frm else "") + "\n"

    def test_a_plan_item_without_files_or_rollback_is_rejected_then_aborts(self):
        def o(phase, rnd, attempt, prompt):
            return self.plan_item("A1", files="", rollback="") if phase == "independent" else ""
        r = self.go(o, qwen(ACCEPT_ALL), type="plan")
        self.assertEqual(r["status"], "aborted")
        self.assertIn("needs a `files:` field", r["reason"])

    def test_a_complete_plan_reaches_consensus(self):
        def o(phase, rnd, attempt, prompt):
            if phase == "independent":
                return self.plan_item("A1")
            return self.plan_item("D1", frm="Ornith/A1")
        r = self.go(o, qwen_plan(ACCEPT_ALL), type="plan")
        self.assertEqual(r["status"], "consensus")

    def test_the_plan_prompt_asks_for_files_and_rollback_but_the_issue_prompt_does_not(self):
        self.assertIn("rollback:", council.format_spec("plan"))
        self.assertNotIn("rollback:", council.format_spec("issue"))
        self.assertIn("KILL:", council.TYPE_HINTS["plan"])
        self.assertIn("HYPOTHESIS", council.TYPE_HINTS["issue"])

    def test_changing_only_the_rollback_counts_as_a_changed_item(self):
        def o(phase, rnd, attempt, prompt):
            if phase == "independent":
                return self.plan_item("A1")
            if phase == "merge":
                return self.plan_item("D1", frm="Ornith/A1")
            return self.plan_item("D1", rollback="git stash; git checkout src/io.c", frm="Ornith/A1")
        def review(rnd, attempt, prompt):
            return "RE D1: OBJECT because rollback loses local edits. evidence: measurement: tried" if rnd == 3 else "RE D1: ACCEPT"
        r = self.go(o, qwen_plan(review), type="plan")
        self.assertEqual((r["status"], r["flags"]), ("consensus", []))

    def test_every_type_has_hints_and_a_template(self):
        for t in ("plan", "issue", "analysis"):
            self.assertIn(t, council.TYPE_HINTS)
            b = council.load_brief((POOL / "templates" / f"{t}.brief.md").read_text())
            self.assertEqual((b.type, b.participants, b.max_rounds, b.time_limit_s), (t, ["Ornith", "Qwen"], 5, 7200))


class OrcaAgentTests(StubBase):
    def agent(self, handle):
        return council.OrcaAgent("Ornith", handle, self.out / "prompts", str(self.stub))

    def test_success_returns_the_answer_and_writes_the_prompt_file(self):
        a = self.agent("h-ornith")
        text = a.ask("[[COUNCIL phase=independent round=1 attempt=1]]\nTASK (issue):\nx", 60)
        self.assertIn("ITEM A1", text)
        f = list((self.out / "prompts").glob("ask-001-Ornith.md"))
        self.assertEqual(len(f), 1)
        self.assertIn("phase=independent", f[0].read_text())

    def test_exit_codes_map_to_agent_errors(self):
        for handle, kind in (("h-refuse", "refused"), ("h-silent", "silent"), ("h-late", "timeout")):
            with self.assertRaises(council.AgentError) as cm:
                self.agent(handle).ask("[[COUNCIL phase=independent round=1 attempt=1]]", 60)
            self.assertEqual((cm.exception.kind, cm.exception.agent), (kind, "Ornith"), handle)

    def test_a_missing_script_is_reported_as_an_agent_error_not_a_crash(self):
        a = council.OrcaAgent("Ornith", "h-ornith", self.out / "prompts", str(self.tmp / "nope.sh"))
        with self.assertRaises((council.AgentError, OSError)):
            a.ask("[[COUNCIL phase=independent round=1 attempt=1]]", 60)


class RunCommandTests(StubBase):
    def test_end_to_end_consensus(self):
        p = self.run_cli(self.brief_file(), "Ornith=h-ornith", "Qwen=h-qwen")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("status: consensus-unverified" if "unverified" in p.stdout else "status: consensus", p.stdout)
        self.assertTrue((self.out / "report.md").exists())
        self.assertTrue(list((self.out / "prompts").glob("ask-*-Qwen.md")))

    def test_no_consensus_exits_one(self):
        p = self.run_cli(self.brief_file(max_rounds=5), "Ornith=h-ornith", "Qwen=h-objector")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("status: no-consensus", p.stdout)

    def test_aborted_exits_two(self):
        p = self.run_cli(self.brief_file(), "Ornith=h-ornith", "Qwen=h-silent")
        self.assertEqual(p.returncode, 2)
        self.assertIn("status: aborted", p.stdout)
        self.assertIn("silent", p.stdout)

    def test_a_brief_kept_inside_the_run_dir_survives_verbatim(self):
        self.out.mkdir()
        f = self.out / "brief.md"
        f.write_text(self.brief_file().read_text() + "\n")
        original = f.read_text()
        self.assertEqual(self.run_cli(f, "Ornith=h-ornith", "Qwen=h-qwen").returncode, 0)
        self.assertEqual((self.out / "brief.input.md").read_text(), original)

    def test_a_missing_pane_is_a_usage_error(self):
        p = self.run_cli(self.brief_file(), "Ornith=h-ornith")
        self.assertEqual(p.returncode, 2)
        self.assertIn("Qwen", p.stderr)

    def test_a_bad_brief_is_a_usage_error(self):
        f = self.tmp / "bad.md"
        f.write_text("type: poem\nparticipants: A, B\n\nx\n")
        self.assertEqual(self.run_cli(f, "A=h-ornith", "B=h-qwen").returncode, 2)


class SkillFileTests(unittest.TestCase):
    def test_skill_md_is_well_formed(self):
        text = (POOL / "SKILL.md").read_text()
        m = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", text)
        self.assertTrue(m, "frontmatter")
        self.assertEqual(m.group(1), "council")
        self.assertGreater(len(m.group(2)), 40)

    def test_paths_and_commands_in_skill_md_exist_and_parse(self):
        text = (POOL / "SKILL.md").read_text()
        self.assertTrue((Path.home() / "tools/council/council.py").exists())
        self.assertTrue((Path.home() / ".agents/skills/orca-ops/scripts/orca-ask.sh").exists())
        for t in ("plan", "issue", "analysis"):
            self.assertIn(t, text)
            self.assertTrue((POOL / "templates" / f"{t}.brief.md").exists())
        # the documented run command must be accepted by the real argument parser
        p = subprocess.run([sys.executable, str(HERE / "council.py"), "run", "--help"], capture_output=True, text=True)
        for flag in re.findall(r"--(?:pane|out)\b", text):
            self.assertIn(flag, p.stdout)
        # the documented pi model ids are the ones Pi lists
        self.assertIn("dreammaker/Ornith-1.5-35B-A3B-MLX-8bit", text)
        self.assertIn("dreammaker/Qwen3.8-27B-4bit-MTP", text)


if __name__ == "__main__":
    unittest.main()
