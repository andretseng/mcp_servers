#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""S4 tests: `start` / `status` / `stop` with a fake Orca CLI (ORCA_CLI_COMMAND) and stub ask scripts. No model, no Orca needed.
Run: uv run --script test_start.py -v"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_s3 import STUB  # noqa: E402

FAKE_ORCA = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(" ".join(a) + "\n")
if a[:2] == ["terminal", "split"]:
    cmd = a[a.index("--command") + 1]
    h = "h-ornith" if "Ornith" in cmd else "h-qwen"
    print(json.dumps({"ok": True, "result": {"split": {"handle": h}}}))
elif a[:2] == ["terminal", "show"]:
    print(json.dumps({"ok": True, "result": {"terminal": {"agentIdentity": os.environ.get("FAKE_IDENTITY", "pi")}}}))
elif a[:2] == ["terminal", "close"]:
    print(json.dumps({"ok": True, "result": {}}))
else:
    print(json.dumps({"ok": False, "error": "unknown"})); sys.exit(1)
'''

SLOW = "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"


def exe(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class StartBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "io.c").write_text("#include <stdint.h>\n// c\nint m = 3;\n")
        self.fake = exe(self.tmp / "fake-orca.py", FAKE_ORCA)
        self.stub = exe(self.tmp / "stub-ask.py", STUB)
        self.slow = exe(self.tmp / "slow-ask.py", SLOW)
        self.log = self.tmp / "orca.log"
        self.out = self.tmp / "run"
        self.env = {**os.environ, "ORCA_CLI_COMMAND": str(self.fake), "FAKE_LOG": str(self.log), "ORCA_TERMINAL_HANDLE": "h-lead"}
        f = self.tmp / "brief.md"
        f.write_text(f"type: plan\nparticipants: Ornith, Qwen\nroots: {self.tmp}\n\nWhy is m wrong?\n")
        self.brief = f

    def tearDown(self):
        st = self.out / "state.json"
        if st.exists():
            try:
                pid = json.loads(st.read_text())["pid"]
                os.killpg(pid, 9)
            except (OSError, ValueError):
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(HERE / "council.py"), *map(str, args)], capture_output=True, text=True,
                              env=env or self.env, timeout=60)

    def start(self, ask=None, brief=None, **kw):
        return self.cli("start", brief or self.brief, "--out", self.out, "--ask-script", ask or self.stub, "--pane-wait", "3", env=kw.get("env"))

    def orca_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def wait_for(self, path, secs=30):
        end = time.monotonic() + secs
        while not path.exists() and time.monotonic() < end:
            time.sleep(0.2)
        return path.exists()


class StartTests(StartBase):
    def test_start_returns_at_once_and_the_run_finishes_by_itself(self):
        t0 = time.monotonic()
        p = self.start()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(time.monotonic() - t0, 15)
        state = json.loads((self.out / "state.json").read_text())
        self.assertEqual(state["panes"], {"Ornith": "h-ornith", "Qwen": "h-qwen"})
        splits = [c for c in self.orca_calls() if c.startswith("terminal split")]
        self.assertEqual(len(splits), 2)
        self.assertTrue(all("--terminal h-lead" in c for c in splits))
        self.assertTrue(splits[0].endswith("--thinking high --json") and "Ornith" in splits[0])
        self.assertTrue(splits[1].endswith("--thinking medium --json") and "Qwen" in splits[1])
        self.assertIn("dreammaker/Ornith-1.5-35B-A3B-MLX-8bit", splits[0])
        self.assertIn("dreammaker/Qwen3.8-27B-4bit-MTP", splits[1])
        self.assertTrue(self.wait_for(self.out / "result.json"), "run never finished")
        s = self.cli("status", self.out)
        self.assertEqual(s.returncode, 0)
        self.assertIn("state: finished", s.stdout)
        self.assertIn("status: consensus", s.stdout)
        self.assertIn("report:", s.stdout)

    def test_status_of_a_running_run_then_stop_kills_it_and_closes_the_panes(self):
        self.assertEqual(self.start(ask=self.slow).returncode, 0)
        s = self.cli("status", self.out)
        self.assertIn("state: running", s.stdout)
        pid = json.loads((self.out / "state.json").read_text())["pid"]
        p = self.cli("stop", self.out)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("run process stopped", p.stdout)
        self.assertNotIn("SIGKILL", p.stdout, "SIGTERM alone should end the run")
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        closes = [c for c in self.orca_calls() if c.startswith("terminal close")]
        self.assertEqual(sorted(closes), ["terminal close --terminal h-ornith --json", "terminal close --terminal h-qwen --json"])
        self.assertIn("state: stopped", self.cli("status", self.out).stdout)

    def test_stop_is_idempotent_and_does_not_close_panes_twice(self):
        self.assertEqual(self.start(ask=self.slow).returncode, 0)
        self.cli("stop", self.out)
        again = self.cli("stop", self.out)
        self.assertEqual(again.returncode, 0)
        self.assertIn("was not running", again.stdout)
        self.assertEqual(len([c for c in self.orca_calls() if c.startswith("terminal close")]), 2)

    def test_stop_after_a_finished_run_closes_panes_without_killing(self):
        self.assertEqual(self.start().returncode, 0)
        self.assertTrue(self.wait_for(self.out / "result.json"))
        p = self.cli("stop", self.out)
        self.assertIn("was not running", p.stdout)
        self.assertEqual(len([c for c in self.orca_calls() if c.startswith("terminal close")]), 2)
        self.assertIn("state: finished", self.cli("status", self.out).stdout)

    def test_keep_panes_leaves_them_open(self):
        self.assertEqual(self.start(ask=self.slow).returncode, 0)
        p = self.cli("stop", self.out, "--keep-panes")
        self.assertIn("panes left open", p.stdout)
        self.assertEqual([c for c in self.orca_calls() if c.startswith("terminal close")], [])

    def test_thinking_can_be_overridden_per_participant_and_bad_values_are_refused(self):
        p = self.cli("start", self.brief, "--out", self.out, "--ask-script", self.stub, "--thinking", "Qwen=low", "--thinking", "Ornith=off")
        self.assertEqual(p.returncode, 0, p.stderr)
        splits = [c for c in self.orca_calls() if c.startswith("terminal split")]
        self.assertTrue(splits[0].endswith("--thinking off --json"))
        self.assertTrue(splits[1].endswith("--thinking low --json"))
        for bad in ("Qwen=ultra", "Codex=low", "Qwen"):
            q = self.cli("start", self.brief, "--out", self.tmp / "other", "--ask-script", self.stub, "--thinking", bad)
            self.assertEqual(q.returncode, 2, bad)
        self.assertEqual(len([c for c in self.orca_calls() if c.startswith("terminal split")]), 2, "a bad value must be refused before any pane opens")

    def test_a_pane_that_never_shows_pi_fails_start_and_cleans_up(self):
        p = self.start(env={**self.env, "FAKE_IDENTITY": "none"})
        self.assertEqual(p.returncode, 2)
        self.assertIn("did not show a pi agent", p.stderr)
        self.assertFalse((self.out / "state.json").exists())
        self.assertEqual(len([c for c in self.orca_calls() if c.startswith("terminal close")]), 2)

    def test_dead_run_is_reported_with_the_log_tail(self):
        self.assertEqual(self.start(ask=self.slow).returncode, 0)
        pid = json.loads((self.out / "state.json").read_text())["pid"]
        os.killpg(pid, 9)
        time.sleep(0.5)
        s = self.cli("status", self.out)
        self.assertIn("state: dead", s.stdout)
        self.assertIn("run.log tail", s.stdout)

    def test_a_reused_pid_is_not_taken_for_the_run(self):
        self.assertEqual(self.start(ask=self.slow).returncode, 0)
        st = json.loads((self.out / "state.json").read_text())
        os.killpg(st["pid"], 9)
        time.sleep(0.5)
        st["pid"] = os.getpid()  # alive, but it is this test, not council.py run
        (self.out / "state.json").write_text(json.dumps(st))
        self.assertIn("state: dead", self.cli("status", self.out).stdout)
        self.assertNotIn("run process stopped", self.cli("stop", self.out).stdout)

    def test_usage_errors(self):
        no_src = {k: v for k, v in self.env.items() if k != "ORCA_TERMINAL_HANDLE"}
        p = self.start(env=no_src)
        self.assertEqual(p.returncode, 2)
        self.assertIn("no pane to split from", p.stderr)
        bad = self.tmp / "bad.md"
        bad.write_text(f"type: plan\nparticipants: Ornith, Codex\nroots: {self.tmp}\n\nx\n")
        p = self.start(brief=bad)
        self.assertEqual(p.returncode, 2)
        self.assertIn("Codex", p.stderr)
        self.assertEqual(self.orca_calls(), [])
        self.assertEqual(self.cli("status", self.tmp / "nowhere").returncode, 2)

    def test_start_refuses_to_reuse_a_run_dir(self):
        self.assertEqual(self.start(ask=self.slow).returncode, 0)
        p = self.start(ask=self.slow)
        self.assertEqual(p.returncode, 2)
        self.assertIn("already holds a run", p.stderr)


if __name__ == "__main__":
    unittest.main()
