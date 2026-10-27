from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit  # noqa: E402
import notecheck as nc  # noqa: E402
import workrun as wr  # noqa: E402

GOOD_NOTE = """---
title: Demo
date: 2026-10-21
tags: [demo]
---

## Summary
The interval is halved once in the interrupt handler [S1].

## Capture
The handler keeps the halved value for the output [S1]. The output is derived from it [S2] [inferred].

## Known limits and open questions
The `capture_edge` setting is not shown here [S3] [unverified].

## References
The demo source file [S1].

## Provenance
| id | status | source | quote | claim |
|---|---|---|---|---|
| S1 | verified | `{src}/cap.c:3` | `ival = t >> 1;` | ival is t halved |
| S2 | inferred | inference: S1 | | the output follows from `ival` |
| S3 | unverified | | | the `capture_edge` setting |
"""


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.src = self.tmp / "src"
        self.src.mkdir()
        (self.src / "cap.c").write_text("int a;\nvoid isr(void) {\n  ival = t >> 1;\n  ertm = sum >> 1;\n}\n", encoding="utf-8")
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        self.note = self.tmp / "vault" / "note.md"
        self.args = argparse.Namespace(question="Analyze how the captured period becomes the output value in the demo project.", note=str(self.note), classification="public",
                                       root=[str(self.src), str(self.tmp / "vault")], deny=[], model="omlx/Ornith-1.5-35B-A3B-MLX-8bit#low", timeout=20, retries=1,
                                       executor="x", no_review=True, workdir=str(self.wd), final_editor="none", final_model="gpt-6-sol", final_effort="high", final_timeout=30, review_after_edit=False)
        self.spec = wr.work_spec(self.args, self.wd)
        nc._cache.clear()

    def tearDown(self):
        self._t.cleanup()

    def good(self):
        return GOOD_NOTE.replace("{src}", str(self.src))


class SpecTests(Base):
    def test_the_spec_is_built_from_the_request_alone(self):
        s = self.spec
        self.assertEqual((s["mode"], s["gate_level"], s["vault_first"]), ("work", "thin", True))
        self.assertEqual(s["first_reads"], [wr.KARPATHY])
        self.assertTrue(s["out"].endswith("draft.md"))
        self.assertEqual(s["final_note"], str(self.note))
        self.assertEqual(s["seeds"], self.args.root)
        for gone in ("index", "path", "sections", "start", "end", "out_of_scope"):
            self.assertNotIn(gone, s)

    def test_a_relative_note_path_is_a_vault_path(self):
        self.assertEqual(wr.note_path("Project筆記/x.md"), Path("/home/andre/llm-wiki/Project筆記/x.md"))

    def test_the_prompt_has_the_research_path_the_module_rule_and_the_note_rules(self):
        spec = wr.work_spec(self.args, self.wd)
        prompt = wr.work_prompt(spec)
        for needle in ("First read", "the knowledge vault", "2) the project source, 3) the SDK", "read its vault note first", "first grep -n the vault notes",
                       "## Provenance", "check-note", "src=<absolute path>", "Nothing is checked while you write", "Known limits and open questions"):
            self.assertIn(needle, prompt)
        for gone in ("max_claims", "Index of notes", "Path to follow", "Out of scope", "codegraph", "Directory-level grep"):
            self.assertNotIn(gone, prompt)
        self.assertIn("fix exactly these points", wr.work_prompt(spec, ["R1-1 [B] Capture: wrong"]))
        for text in (wr.work_prompt(spec, ["R1-1 [B] Capture: wrong"]), wr.continue_prompt(spec, ["R1-1 [B] x: y"], False)):
            self.assertIn("FIRST look for it", text)  # a point about missing evidence sends the executor to the sources before it may weaken a claim
            self.assertIn("never delete or weaken a claim you can support", text)
        self.assertTrue(any("do not ask for the claim to be marked unverified" in c for c in wr.NOTE_CONSTRAINTS))

    def test_the_continue_prompts_do_not_ask_for_a_new_exploration(self):
        c = wr.continue_prompt(self.spec, ["R1-1 [B] x: y"], False)
        self.assertIn("A reviewer read your finished note", c)
        self.assertIn("from what you already know", c)
        n = wr.continue_prompt(self.spec, None, True)
        self.assertIn("does not exist or is empty", n)

    def test_pi_command_splits_the_thinking_level_and_keeps_one_session_id(self):
        cmd = wr.pi_command("omlx/Ornith-1.5-35B-A3B-MLX-8bit#low", "do it", "work-x")
        self.assertEqual(cmd[cmd.index("--model") + 1], "omlx/Ornith-1.5-35B-A3B-MLX-8bit")
        self.assertEqual(cmd[cmd.index("--thinking") + 1], "low")
        self.assertEqual(cmd[cmd.index("--session-id") + 1], "work-x")
        self.assertEqual(cmd[-1], "do it")
        self.assertIn("-ne", cmd)  # no extension of the user's own setup, only the gate
        self.assertNotIn("--thinking", wr.pi_command("omlx/m", "p", "s"))
        self.assertEqual(wr.pi_session_id(Path("/x/erpm-run29")), "work-erpm-run29")

    def test_pi_trace_counts_calls_context_and_cut_off_outputs(self):
        import pitrace
        rows = [{"type": "message", "message": {"role": "user", "timestamp": 1000}},
                {"type": "message", "message": {"role": "assistant", "timestamp": 3000, "durationMs": 1500, "model": "m", "stopReason": "length",
                                                 "usage": {"input": 100, "cacheRead": 50, "output": 9}, "content": [{"type": "toolCall"}]}},
                {"type": "message", "message": {"role": "toolResult", "timestamp": 4000, "isError": True}}]
        s = pitrace.summarize(rows)
        self.assertEqual((s["calls"], s["tool_err"], s["ctx_max"], s["out_max"], s["truncated"], s["wall_s"]), (1, 1, 150, 9, 1, 3))

    def test_exec_config_changes_only_the_temperature(self):
        self.assertEqual(wr.exec_config("a/b#high", 0.1), {"providers": {"a": {"models": {"b": {"body": {"temperature": 0.1}}}}}})
        self.assertEqual(wr.exec_config("a/b", None), {})
        self.assertEqual(wr.EXEC_TEMPERATURE, 0.3)


class ReportTests(Base):
    def test_a_clean_note_gets_no_report_and_a_bad_one_gets_one_that_can_be_stripped(self):
        self.assertEqual(wr.harness_report([], []), "")
        r = wr.harness_report(["S1: the quote is not in cap.c"], ["a warning"])
        self.assertIn("## Harness check (script, after the session)", r)
        self.assertIn("- error: S1:", r)
        text = self.good() + r
        self.assertEqual(wr.strip_report(text).rstrip("\n"), self.good().rstrip("\n"))
        self.assertEqual(wr.strip_report(self.good()).rstrip("\n"), self.good().rstrip("\n"))

    def test_finalize_publishes_the_draft_with_the_report_and_never_blocks(self):
        draft = Path(self.spec["out"])
        draft.write_text(self.good().replace("ival = t >> 1;", "ival = t >> 9;"), encoding="utf-8")
        errs, warns = wr.finalize(self.spec)
        self.assertTrue(any("S1" in e for e in errs))
        published = self.note.read_text()
        self.assertIn("ival is t halved", published)
        self.assertIn("## Harness check (script, after the session)", published)
        draft.write_text(self.good(), encoding="utf-8")
        wr.finalize(self.spec)
        self.assertNotIn("Harness check", self.note.read_text())


class ReviewItemTests(unittest.TestCase):
    def test_items_are_parsed_and_ids_stay_stable(self):
        items = wr.parse_review_items("[B] Capture: the order is wrong\n[U] GENERAL: add a section", 1)
        self.assertEqual([(i["tag"], i["target"]) for i in items], [("B", "Capture"), ("U", "GENERAL")])
        again, st = wr.settle_items(wr.parse_review_items("[B] Capture: (R1-1) still wrong\n[B] X: n1\n[B] X: n2\n[B] X: n3\n[B] X: n4", 2), items, 2)
        self.assertEqual((again[0]["id"], again[0]["status"]), ("R1-1", "persisting"))
        self.assertEqual([i["tag"] for i in again[1:]], ["B", "B", "B", "U"])
        self.assertEqual((st["persisting"], st["demoted"], st["resolved"]), (1, 1, 0))

    def test_prior_constraints_are_short(self):
        prior = [{"id": f"R1-{i}", "tag": "B", "target": "S", "text": "x" * 900} for i in range(1, 15)]
        cs = wr.prior_constraints(prior)
        self.assertTrue(all(len(c) <= 1000 for c in cs))
        self.assertIn("R1-14", cs[-1])


class WorkSessionTests(Base):
    def fake_opencode(self, body: str):
        f = self.tmp / "fake-opencode"
        f.write_text("#!/usr/bin/env python3\nimport json, os, sys\nif len(sys.argv) > 1 and sys.argv[1] == 'session':\n    print('ses_fake1  work  now')\n    sys.exit(0)\n"
                     "spec = json.load(open(os.environ['GATE_FILE']))\n" + textwrap.dedent(body))
        f.chmod(0o755)
        self.orig = wr.opencode_bin
        wr.opencode_bin = lambda: str(f)

    def tearDown(self):
        if hasattr(self, "orig"):
            wr.opencode_bin = self.orig
        super().tearDown()

    def test_a_session_that_writes_a_good_draft_is_published_without_a_report(self):
        self.fake_opencode(f"""
            from pathlib import Path
            Path(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))
        """)
        rec = wr.run_work(self.spec, self.wd, self.args)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual((rec["attempts"], rec["session"], rec["errors"]), (1, "ses_fake1", []))
        self.assertIn("ival is t halved", self.note.read_text())
        self.assertNotIn("Harness check", self.note.read_text())
        self.assertIn("===== work (", (self.wd / "logs" / "live.log").read_text())
        self.assertTrue((self.wd / "logs" / "work.turn1.log").is_file())

    def test_a_draft_with_problems_is_published_with_the_report_and_gets_no_extra_session(self):
        self.fake_opencode(f"""
            from pathlib import Path
            Path(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}).replace('ival = t >> 1;', 'ival = t >> 9;'))
        """)
        rec = wr.run_work(self.spec, self.wd, self.args)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["attempts"], 1)
        self.assertTrue(any("quote is not in" in e for e in rec["errors"]))
        self.assertIn("- error: S1: the quote is not in", self.note.read_text())

    def test_the_work_session_runs_in_its_own_directory_with_its_own_sampling(self):
        self.fake_opencode(f"""
            import json, os
            from pathlib import Path
            cfg = json.load(open('opencode.json'))
            print('cwd', os.getcwd(), 'pwd', os.environ['PWD'], 'temperature', cfg['providers']['omlx']['models']['Ornith-1.5-35B-A3B-MLX-8bit']['body']['temperature'])
            Path(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))
        """)
        rec = wr.run_work(self.spec, self.wd, self.args)
        self.assertTrue(rec["ok"], rec)
        self.assertIn(f"cwd {self.wd / 'work'} pwd {self.wd / 'work'} temperature 0.3", (self.wd / "logs" / "work.turn1.log").read_text())

    def test_a_session_that_wrote_nothing_is_nudged_in_the_same_session(self):
        counter = self.tmp / "n"
        self.fake_opencode(f"""
            from pathlib import Path
            n = Path({str(counter)!r})
            k = int(n.read_text()) + 1 if n.exists() else 1
            n.write_text(str(k))
            if k == 1:
                assert '--session' not in sys.argv
            else:
                assert sys.argv[sys.argv.index('--session') + 1] == 'ses_fake1', sys.argv
                assert 'does not exist or is empty' in sys.argv[-1], sys.argv[-1]
                Path(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))
        """)
        rec = wr.run_work(self.spec, self.wd, self.args)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["attempts"], 2)
        self.assertEqual(len(list((self.wd / "logs").glob("work.turn*.log"))), 2)

    def test_a_session_that_never_writes_fails_and_leaves_the_vault_alone(self):
        self.fake_opencode("print('nothing')")
        rec = wr.run_work(self.spec, self.wd, self.args)
        self.assertFalse(rec["ok"])
        self.assertFalse(self.note.exists())

    def test_a_review_revision_goes_into_the_same_session_and_starts_from_the_reviewed_note(self):
        self.fake_opencode(f"""
            from pathlib import Path
            d = Path(spec['out'])
            if '--session' in sys.argv:
                assert 'A reviewer read your finished note' in sys.argv[-1] and 'R1-1' in sys.argv[-1], sys.argv[-1]
                assert 'Harness check' not in d.read_text(), 'the report must not be in the draft'
                d.write_text(d.read_text().replace('ival is t halved', 'ival is the halved t'))
            else:
                d.write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}).replace('ival = t >> 1;', 'ival = t >> 9;'))
        """)
        first = wr.run_work(self.spec, self.wd, self.args)
        self.assertIn("Harness check", self.note.read_text())
        second = wr.run_work(self.spec, self.wd, self.args, feedback=["R1-1 [B] Capture: wording"])
        self.assertTrue(second["ok"], second)
        self.assertIn("the halved t", self.note.read_text())
        self.assertEqual(second["session"], "ses_fake1")

    def test_a_session_that_cannot_be_continued_is_replaced_by_a_new_one(self):
        (self.wd / "work.session").write_text("ses_gone", encoding="utf-8")
        self.fake_opencode(f"""
            from pathlib import Path
            if '--session' in sys.argv:
                sys.exit(3)
            Path(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))
        """)
        rec = wr.run_work(self.spec, self.wd, self.args)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["attempts"], 2)

    def test_cmd_run_writes_the_request_runs_the_session_and_publishes(self):
        self.fake_opencode(f"""
            from pathlib import Path
            assert 'Analyze how the captured period' in sys.argv[-1]
            Path(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))
        """)
        self.assertEqual(wr.cmd_run(self.args), 0)
        self.assertIn("## Question\nAnalyze how the captured period", (self.wd / "request.md").read_text())
        self.assertTrue(self.note.is_file())
        self.assertIn("total_seconds", json.loads((self.wd / "run.json").read_text()))

    def test_check_note_cli_reports_errors_and_warnings_without_the_report_section(self):
        wf = self.wd / "work.json"
        wf.write_text(json.dumps(self.spec))
        f = self.wd / "n.md"
        f.write_text(self.good())
        sys.argv = ["workrun", "check-note", "--work", str(wf), "--file", str(f)]
        self.assertEqual(wr.main(), 0)
        f.write_text(self.good().replace("ival = t >> 1;", "ival = t >> 9;") + wr.harness_report(["old"], []))
        self.assertEqual(wr.main(), 2)
        wr.append_open_items(f, ["R3-1 [B] Capture: still open"])
        self.assertIn("## Open items (review)\n- R3-1", f.read_text())

ZH_NOTE = """---
title: 示範
date: 2026-10-21
tags: [demo]
---

## 摘要
中斷處理程式把區間值右移一位，得到半值 [S1]。電氣週期由這個值推導 [S2] [inferred]。

## 擷取
處理程式在每次擷取時更新區間變數，後續的總和與平均都依賴它 [S1]。

## 已知限制與待驗證
擷取邊緣是上升緣或下降緣，這些檔案沒有顯示 [S3] [unverified]。

## 參考
示範用的來源檔案 [S1]。

## Provenance
| id | status | source | quote | claim |
|---|---|---|---|---|
| S1 | verified | `{src}/cap.c:3` | `ival = t >> 1;` | ival 是 t 的一半 |
| S2 | inferred | inference: S1 | | 輸出由 ival 推得 |
| S3 | unverified | | | 擷取邊緣設定 |

## 編修紀錄
- S1：把「右移一位」改成「半值」，並確認引用行。
"""


class FinalEditTests(Base):
    def fake_codex(self, body: str):
        f = self.tmp / "fake-codex"
        f.write_text("#!/usr/bin/env python3\nimport sys\nargs = sys.argv\nprompt = sys.stdin.read()\nout = args[args.index('--output-last-message') + 1]\n" + textwrap.dedent(body))
        f.chmod(0o755)
        return str(f)

    def publish_ornith_note(self, flawed: bool = False):
        self.note.parent.mkdir(parents=True, exist_ok=True)
        body = self.good().replace("ival = t >> 1;", "ival = t >> 9;") if flawed else self.good()
        self.note.write_text(body + wr.harness_report(["S9: something old"], []), encoding="utf-8")

    def test_the_prompt_asks_for_a_checked_traditional_chinese_rewrite_without_new_facts(self):
        import finaledit as fe2
        p = fe2.build_prompt("Q?", "---\ntitle: t\n---\nbody", "- error: S1: x", ["/r1", "/r2"], ["secret"])
        for needle in ("FINAL EDITOR", "Traditional Chinese", "READ files", "/r1", "never read or cite: secret", "Do not add a fact you have not confirmed", "exact text of the cited line",
                       "編修紀錄", "Do not run or mention an adviser review", "<<<NOTE", "NOTE>>>", "- error: S1: x", "verified`, `inferred`, `unverified` in English", "reader never sees the local model's version", "no 原稿"):
            self.assertIn(needle, p)

    def test_parse_note_needs_the_markers_the_frontmatter_and_the_provenance_section(self):
        import finaledit as fe2
        ok = f"some chatter\n{fe2.START}\n{ZH_NOTE}\n{fe2.END}\nmore"
        self.assertTrue(fe2.parse_note(ok).startswith("---"))
        self.assertIsNone(fe2.parse_note(ZH_NOTE))
        self.assertIsNone(fe2.parse_note(f"{fe2.START}\njust text\n{fe2.END}"))

    def test_restricted_material_is_never_sent(self):
        import finaledit as fe2
        with self.assertRaises(fe2.FinalEditError):
            fe2.final_edit("Q", "n", "", [], [], "restricted", codex="/nonexistent")

    def test_an_exclude_flag_that_names_a_denied_source_is_not_a_touch(self):
        import finaledit as fe2
        fe2.LAST_COMMANDS[:] = [("grep -rl x /v --exclude='*secret-ref*' --exclude-dir=secret-ref", ""), ("cat /v/secret-ref.md", "")]
        self.assertEqual(fe2.denied_touches(["secret-ref"]), ["cat /v/secret-ref.md"])

    def test_a_negated_glob_that_names_a_denied_source_is_not_a_touch(self):
        import finaledit as fe2
        fe2.LAST_COMMANDS[:] = [("""rg --files -g '!secret-ref/**' -g '!**/secret-ref*' | head""", ""), ("rg -g '*.md' secret-ref", "")]
        self.assertEqual(fe2.denied_touches(["secret-ref"]), ["rg -g '*.md' secret-ref"])

    def test_a_grep_v_filter_that_names_a_denied_source_is_not_a_touch(self):
        import finaledit as fe2
        fe2.LAST_COMMANDS[:] = [("grep -rl x /v | rg -v 'secret-ref|other' | head", ""), ("cat /v/secret-ref.md", "")]
        self.assertEqual(fe2.denied_touches(["secret-ref"]), ["cat /v/secret-ref.md"])

    def test_a_command_that_touches_an_excluded_source_is_reported(self):
        import finaledit as fe2
        self.publish_ornith_note(flawed=True)
        self.spec["deny"] = ["secret-ref"]
        codex = self.fake_codex(f"""
            import json
            print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution", "command": "cat /v/secret-ref.md", "aggregated_output": "x"}}}}))
            print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution", "command": "cat /v/fine.md", "aggregated_output": "y"}}}}))
            print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 5, "output_tokens": 2}}}}))
            open(out, 'w').write('<<<NOTE\\n' + {ZH_NOTE!r}.replace('{{src}}', {str(self.src)!r}) + '\\nNOTE>>>')
        """)
        self.args.codex = codex
        r = wr.apply_final_edit(self.spec, self.wd, self.args)
        self.assertEqual(r["denied_touches"], ["cat /v/secret-ref.md"])
        self.assertEqual(r["codex_tokens"]["input_tokens"], 5)
        self.assertIn("excluded source", self.note.read_text(encoding="utf-8"))

    def test_codex_edits_the_published_note_and_the_local_note_is_kept(self):
        self.publish_ornith_note(flawed=True)
        codex = self.fake_codex(f"""
            assert 'exec' in args and '--model' in args and args[args.index('--model') + 1] == 'gpt-6-sol', args
            assert '--sandbox' in args and args[args.index('--sandbox') + 1] == 'read-only', args
            assert 'model_reasoning_effort="high"' in args
            assert 'FINAL EDITOR' in prompt and 'the quote is not in' in prompt and 'Harness check' not in prompt
            open(out, 'w').write('<<<NOTE\\n' + {ZH_NOTE!r}.replace('{{src}}', {str(self.src)!r}) + '\\nNOTE>>>')
        """)
        self.args.codex = codex
        self.args.final_editor = "codex"
        r = wr.apply_final_edit(self.spec, self.wd, self.args)
        self.assertEqual(r["outcome"], "published")
        self.assertEqual((r["errors"], r["warnings"]), (0, 0))
        published = self.note.read_text()
        self.assertIn("## 摘要", published)
        self.assertIn("## 編修紀錄", published)
        self.assertNotIn("Harness check", published)
        self.assertIn("ival = t >> 9;", (self.wd / "note.ornith.md").read_text())
        self.assertIn("## 摘要", (self.wd / "note.edited.md").read_text())

    def test_when_codex_fails_the_local_note_stays_as_it_is(self):
        self.publish_ornith_note()
        before = self.note.read_text()
        self.args.codex = self.fake_codex("sys.exit(3)")
        r = wr.apply_final_edit(self.spec, self.wd, self.args)
        self.assertEqual(r["outcome"], "kept the local note")
        self.assertIn("codex exited 3", r["detail"])
        self.assertEqual(self.note.read_text(), before)
        self.args.codex = self.fake_codex("open(out, 'w').write('I could not do it')")
        self.assertEqual(wr.apply_final_edit(self.spec, self.wd, self.args)["outcome"], "kept the local note")

    def calls_of(self, name: str = "calls") -> int:
        f = self.tmp / name
        return len(f.read_text()) if f.is_file() else 0

    def test_relative_paths_and_free_form_premises_are_repaired_by_the_script_without_a_second_codex_call(self):
        self.publish_ornith_note()
        edited = ZH_NOTE.replace("{src}/cap.c", "cap.c").replace("inference: S1", "前提：S1、S3")
        self.args.codex = self.fake_codex(f"open({str(self.tmp / 'calls')!r}, 'a').write('x'); open(out, 'w').write('<<<NOTE\\n' + {edited!r} + '\\nNOTE>>>')")
        r = wr.apply_final_edit(self.spec, self.wd, self.args)
        self.assertEqual((r["outcome"], r["errors"], r["repair_round"]), ("published", 0, False))
        self.assertEqual(self.calls_of(), 1)
        self.assertIn(f"{self.src}/cap.c:3", self.note.read_text())
        self.assertIn("inference: S1, S3", self.note.read_text())

    def test_an_edit_that_fails_the_check_gets_one_repair_round_with_the_listed_errors(self):
        self.publish_ornith_note()
        bad = ZH_NOTE.replace("{src}", str(self.src)).replace("ival = t >> 1;", "ival = t >> 9;")
        good = ZH_NOTE.replace("{src}", str(self.src))
        self.args.codex = self.fake_codex(f"""
            n = len(open({str(self.tmp / 'calls')!r}).read()) if __import__('os').path.exists({str(self.tmp / 'calls')!r}) else 0
            open({str(self.tmp / 'calls')!r}, 'a').write('x')
            if n == 0:
                text = {bad!r}
            else:
                assert 'the quote is not in' in prompt, prompt[-600:]
                text = {good!r}
            open(out, 'w').write('<<<NOTE\\n' + text + '\\nNOTE>>>')
        """)
        r = wr.apply_final_edit(self.spec, self.wd, self.args)
        self.assertEqual((r["outcome"], r["errors"], r["repair_round"]), ("published", 0, True))
        self.assertEqual(self.calls_of(), 2)

    def test_an_edit_that_still_fails_after_the_repair_round_is_not_published(self):
        self.publish_ornith_note()
        before = self.note.read_text(encoding="utf-8")
        bad = ZH_NOTE.replace("{src}", str(self.src)).replace("ival = t >> 1;", "ival = t >> 9;")
        self.args.codex = self.fake_codex(f"open({str(self.tmp / 'calls')!r}, 'a').write('x'); open(out, 'w').write('<<<NOTE\\n' + {bad!r} + '\\nNOTE>>>')")
        r = wr.apply_final_edit(self.spec, self.wd, self.args)
        self.assertEqual(r["outcome"], "kept the local note")
        self.assertIn("still fails the note check", r["detail"])
        self.assertEqual(self.calls_of(), 2)
        self.assertEqual(self.note.read_text(encoding="utf-8"), before)
        self.assertIn("ival = t >> 9;", (self.wd / "note.edited.md").read_text())

    def test_an_edit_that_does_not_have_fewer_errors_than_the_local_note_is_not_published(self):
        self.publish_ornith_note(flawed=True)  # the local note has 1 error (a quote that is not in the file)
        bad = ZH_NOTE.replace("{src}", str(self.src)) + "\n"
        bad = bad.replace("| S3 | unverified | | | 擷取邊緣設定 |", "| S3 | verified | | | 擷取邊緣設定 |")  # 1 error as well: not fewer
        self.args.codex = self.fake_codex(f"open(out, 'w').write('<<<NOTE\\n' + {bad!r} + '\\nNOTE>>>')")
        self.assertEqual(wr.apply_final_edit(self.spec, self.wd, self.args)["outcome"], "kept the local note")

    def test_cmd_run_runs_the_final_edit_after_the_local_session_and_skips_the_review_loop(self):
        opencode = self.tmp / "fake-opencode"
        opencode.write_text(f"#!/usr/bin/env python3\nimport json, os, sys\nif len(sys.argv) > 1 and sys.argv[1] == 'session':\n    print('ses_fake1  work  now')\n    sys.exit(0)\nfrom pathlib import Path\nspec = json.load(open(os.environ['GATE_FILE']))\nPath(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))\n")
        opencode.chmod(0o755)
        orig = wr.opencode_bin
        wr.opencode_bin = lambda: str(opencode)
        try:
            self.args.codex = self.fake_codex(f"open(out, 'w').write('<<<NOTE\\n' + {ZH_NOTE!r}.replace('{{src}}', {str(self.src)!r}) + '\\nNOTE>>>')")
            self.args.final_editor = "codex"
            self.args.no_review = False
            self.assertEqual(wr.cmd_run(self.args), 0)
        finally:
            wr.opencode_bin = orig
        run = json.loads((self.wd / "run.json").read_text())
        self.assertEqual(run["final_edit"]["outcome"], "published")
        self.assertIsNone(run["review"])
        self.assertIn("## 摘要", self.note.read_text())


class DefaultsTests(Base):
    def parse(self, *extra):
        got = {}
        orig, argv = wr.cmd_run, sys.argv
        wr.cmd_run = lambda a: got.setdefault("args", a) and 0
        sys.argv = ["workrun.py", "run", "--question", "q", "--note", "n.md", "--root", "/r", "--workdir", "/w", *extra]
        try:
            wr.main()
        finally:
            wr.cmd_run, sys.argv = orig, argv
        return got["args"]

    def test_by_default_nothing_goes_to_codex(self):
        a = self.parse()
        self.assertEqual((a.final_editor, a.review, a.no_review), ("none", False, False))

    def test_codex_modes_are_opt_in(self):
        self.assertEqual(self.parse("--final-editor", "codex").final_editor, "codex")
        self.assertTrue(self.parse("--review").review)

    def test_cmd_run_without_review_never_calls_the_adviser(self):
        opencode = self.tmp / "fake-opencode"
        opencode.write_text(f"#!/usr/bin/env python3\nimport json, os, sys\nif len(sys.argv) > 1 and sys.argv[1] == 'session':\n    print('ses_fake1  work  now')\n    sys.exit(0)\nfrom pathlib import Path\nspec = json.load(open(os.environ['GATE_FILE']))\nPath(spec['out']).write_text({GOOD_NOTE!r}.replace('{{src}}', {str(self.src)!r}))\n")
        opencode.chmod(0o755)
        orig_bin, orig_rev = wr.opencode_bin, wr.run_review
        wr.opencode_bin = lambda: str(opencode)
        def boom(*a, **k):
            raise AssertionError("the adviser must not run by default")
        wr.run_review = boom
        try:
            self.args.final_editor, self.args.no_review, self.args.review = "none", False, False
            self.assertEqual(wr.cmd_run(self.args), 0)
        finally:
            wr.opencode_bin, wr.run_review = orig_bin, orig_rev
        run = json.loads((self.wd / "run.json").read_text())
        self.assertIsNone(run["review"])
        self.assertNotIn("final_edit", run)


class AuditTests(Base):
    def log(self, calls):
        (self.wd / "work.json").write_text(json.dumps(self.spec))
        Path(self.spec["out"]).write_text(self.good())
        rows = [{"ts": "2026-01-01T00:00:%02dZ" % i, "session": "ses_1", "card": "W", "tool": t, "blocked": False, "input": inp} for i, (t, inp) in enumerate(calls)]
        rows.append({"ts": "2026-01-01T00:01:00Z", "session": "ses_1", "card": "W", "tool": "write", "blocked": False, "input": json.dumps({"path": self.spec["out"], "bytes": 9})})
        g = self.wd / "gate.jsonl"
        g.write_text("\n".join(json.dumps(r) for r in rows))
        return audit.audit(self.wd, g)

    def test_a_clean_session_passes_and_reports_lookup(self):
        report, fails = self.log([("read", json.dumps({"path": wr.KARPATHY})), ("read", json.dumps({"path": "/home/andre/llm-wiki/x.md"})), ("grep", json.dumps({"pattern": "capture_edge", "path": str(self.src)}))])
        self.assertEqual(fails, [], fails)
        self.assertIn("[lookup-before-claim 1/2", report[0])

    def test_karpathy_not_first_and_stray_sessions_fail(self):
        _, fails = self.log([("read", json.dumps({"path": "/home/andre/llm-wiki/x.md"})), ("read", json.dumps({"path": wr.KARPATHY}))])
        self.assertTrue(any("guideline skill was read as call 2" in f for f in fails), fails)
        g = self.wd / "gate.jsonl"
        g.write_text(json.dumps({"ts": "2026-01-01T00:00:00Z", "session": "ses_9", "card": None, "tool": "read", "blocked": False, "input": "{}"}))
        self.assertTrue(any("OUTSIDE the gate" in f for f in audit.audit(self.wd, g)[1]))


if __name__ == "__main__":
    unittest.main()
