from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notecheck as nc  # noqa: E402


class NoteTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.src = self.tmp / "src"
        self.src.mkdir()
        (self.src / "cap.c").write_text("int a;\nvoid isr(void) {\n  ival = t >> 1;\n  ertm = sum >> 1;\n}\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "cap.c")
        self.note = f"""---
title: Demo
date: 2026-10-21
tags: [demo]
---

## Summary
The interval is halved once in the interrupt handler [S1]. The revolution time is a halved sum of the intervals [S2].

## Path
The handler runs on every capture and keeps the latest value in a variable [S1]. The sum is formed later in the main loop [S3] [inferred].

```c src={s}:3-4
  ival = t >> 1;
  ertm = sum >> 1;
```

## Known limits and open questions
Whether the capture edge is rising or falling is not shown in these files [S4] [unverified].

## References
Source file cap.c only [S1].

## Provenance
| id | status | source | quote | claim |
|---|---|---|---|---|
| S1 | verified | `{s}:3` | `ival = t >> 1;` | ival is t halved |
| S2 | verified | `{s}:4` | `ertm = sum >> 1;` | ertm is sum halved |
| S3 | inferred | inference: S1, S2 | | the sum is formed from the intervals |
| S4 | unverified | | | the capture edge |
"""

    def tearDown(self):
        self._t.cleanup()

    def check(self, text=None, **kw):
        return nc.check_note(text or self.note, roots=[str(self.src)], deny=kw.get("deny", []))

    def test_a_good_note_passes_without_errors(self):
        errs, warns, claims = self.check()
        self.assertEqual(errs, [], errs)
        self.assertEqual([c["id"] for c in claims], ["S1", "S2", "S3", "S4"])

    def test_quote_not_in_the_file_is_rejected_and_unverified_is_not_checked(self):
        errs, _, _ = self.check(self.note.replace("`ival = t >> 1;`", "`ival = t >> 7;`"))
        self.assertTrue(any("S1" in e and "quote is not in" in e for e in errs), errs)
        self.assertFalse(any("S4" in e for e in errs))

    def test_a_pipe_in_a_quote_is_written_escaped(self):
        (self.src / "cap.c").write_text("if (a || b) go();\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "cap.c")
        text = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:1` | `if (a \\|\\| b) go();`")
        errs, _, _ = self.check(text)
        self.assertFalse(any("S1" in e for e in errs), errs)

    def test_an_unescaped_pipe_in_the_quote_is_tolerated(self):
        (self.src / "cap.c").write_text("if (a || b) go();\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "cap.c")
        text = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:1` | `if (a || b) go();`")
        errs, _, claims = self.check(text)
        self.assertFalse(any("S1" in e for e in errs), errs)
        self.assertEqual(claims[0]["quote"], "`if (a || b) go();`".strip("`"))

    def test_formatting_of_the_quote_that_does_not_change_the_text_is_tolerated(self):
        (self.src / "cap.c").write_text("static const Cfg c = {\n    .mode = FAST,\n    .hyst = 30,\n};\nint other;\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "cap.c")
        for quote in ("`.mode = FAST,` `.hyst = 30,`", "`` `.mode = FAST,` ``", ".mode = FAST, ... .hyst = 30,", ".mode = FAST, … .hyst = 30,"):
            text = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2-3` | {quote if quote.startswith('`') else '`' + quote + '`'}")
            errs, _, _ = self.check(text)
            self.assertFalse(any("S1" in e for e in errs), (quote, errs))
        bad = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2-3` | `.mode = FAST,` `.hyst = 99,`")
        self.assertTrue(any("S1" in e for e in self.check(bad)[0]))
        tiny = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2-3` | `.mode = FAST,` `a`")
        self.assertTrue(any("S1" in e for e in self.check(tiny)[0]), "a piece shorter than 4 characters must not count")
        (self.src / "cap.c").write_text("x;\nif (++skip < 3)\n{\n    return;\n}\n", encoding="utf-8")
        nc._cache.clear()
        ok = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2-4` | `if (++skip < 3)`<br>`{{`<br>`return;`")
        self.assertFalse(any("S1" in e for e in self.check(ok)[0]), "consecutive lines may include a lone brace")
        wrong = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2-4` | `if (++skip < 3)`<br>`{{`<br>`return 1;`")
        self.assertTrue(any("S1" in e for e in self.check(wrong)[0]))

    def test_inferred_needs_existing_premises(self):
        errs, _, _ = self.check(self.note.replace("inference: S1, S2", "inference: S1, S9"))
        self.assertTrue(any("premise S9" in e for e in errs), errs)
        errs, _, _ = self.check(self.note.replace("inference: S1, S2", "from the code"))
        self.assertTrue(any("needs source `inference" in e for e in errs), errs)

    def test_citing_an_unknown_claim_is_an_error(self):
        errs, _, _ = self.check(self.note.replace("a halved sum of the intervals [S2]", "a halved sum of the intervals [S9]"))
        self.assertTrue(any("S9" in e for e in errs), errs)

    def test_a_body_that_cites_little_only_warns(self):
        filler = lambda n: " ".join(f"Sentence number {i} says something long without citing any claim." for i in range(n))
        errs, warns, _ = self.check(self.note.replace("## Path", "## Path\n" + filler(12) + "\n"))
        self.assertFalse(any("cite a claim" in e for e in errs), errs)
        self.assertTrue(any("of the body sentences cite a claim" in w for w in warns), warns)
        errs, warns, _ = self.check(self.note.replace("## Path", "## Path\n" + filler(4) + "\n"))
        self.assertFalse(any("sentences of the body cite a claim" in e for e in errs), errs)
        self.assertTrue(any("of the body sentences cite a claim" in w for w in warns), warns)

    def test_an_inferred_claim_may_sit_on_a_quoted_source_line(self):
        s = str(self.src / "cap.c")
        row = f"| S9 | inferred | `{s}:3` | `ival = t >> 1;` | halved period, read as a rate |\n"
        errs, _, _ = self.check(self.note.rstrip("\n") + "\n" + row)
        self.assertFalse(any("S9" in e for e in errs), errs)
        bad = f"| S9 | inferred | `{s}:3` | `ival = t >> 7;` | wrong quote |\n"
        errs, _, _ = self.check(self.note.rstrip("\n") + "\n" + bad)
        self.assertTrue(any("S9" in e for e in errs), errs)

    def test_missing_inferred_and_unverified_tags_are_warnings(self):
        _, warns, _ = self.check(self.note.replace(" [S3] [inferred]", " [S3]").replace("[S4] [unverified]", "[S4]"))
        self.assertTrue(any("without [inferred]" in w for w in warns), warns)
        self.assertTrue(any("without [unverified]" in w for w in warns), warns)

    def test_code_block_must_match_the_lines(self):
        errs, _, _ = self.check(self.note.replace("  ertm = sum >> 1;\n```", "  ertm = sum >> 9;\n```"))
        self.assertTrue(any("not the text of those lines" in e for e in errs), errs)

    def test_source_outside_roots_or_denied_is_rejected(self):
        other = self.tmp / "other.c"
        other.write_text("x = 1;\n", encoding="utf-8")
        text = self.note.replace(f"{self.src}/cap.c:3` | `ival = t >> 1;`", f"{other}:1` | `x = 1;`")
        errs, _, _ = self.check(text)
        self.assertTrue(any("outside the allowed roots" in e for e in errs), errs)
        errs, _, _ = self.check(deny=["cap.c"])
        self.assertTrue(any("excluded source" in e for e in errs), errs)

    def test_frontmatter_and_section_names_warn_but_the_provenance_table_is_required(self):
        errs, warns, _ = self.check(self.note.replace("---\ntitle: Demo\ndate: 2026-10-21\ntags: [demo]\n---\n", ""))
        self.assertTrue(any("frontmatter" in w for w in warns), warns)
        errs, warns, _ = self.check(self.note.replace("## Known limits and open questions", "## Notes"))
        self.assertTrue(any("'limits'" in w for w in warns), warns)
        errs, _, _ = self.check(self.note[: self.note.index("## Provenance")])
        self.assertTrue(any("needs a '## Provenance'" in e for e in errs), errs)

    def test_a_source_path_with_spaces_and_parentheses_is_accepted(self):
        spaced = self.src / "COMP note (SLAU846E).md"
        spaced.write_text("line one\nthe comparator has a hysteresis setting\nline three\n", encoding="utf-8")
        nc._cache.clear()
        row = f"| S5 | verified | `{spaced}:2` | `the comparator has a hysteresis setting` | hysteresis |\n"
        errs, _, _ = self.check(self.note.rstrip("\n") + "\n" + row)
        self.assertFalse(any("S5" in e for e in errs), errs)
        block = f"\n```md src={spaced}:1-2\nline one\nthe comparator has a hysteresis setting\n```\n"
        errs, _, _ = self.check(self.note.replace("## Known limits", block + "\n## Known limits"))
        self.assertFalse(any("code block" in e for e in errs), errs)

    def test_a_traditional_chinese_note_passes_with_chinese_headings_and_sentences(self):
        s = str(self.src / "cap.c")
        zh = f"""---
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
| S1 | verified | `{s}:3` | `ival = t >> 1;` | ival 是 t 的一半 |
| S2 | inferred | inference: S1 | | 輸出由 ival 推得 |
| S3 | unverified | | | 擷取邊緣設定 |
"""
        errs, warns, claims = self.check(zh)
        self.assertEqual(errs, [], errs)
        uncited = zh.replace("後續的總和與平均都依賴它 [S1]。", "後續的總和與平均都依賴它，這一整段說明沒有引用任何一個 claim。這一整段說明沒有引用任何一個 claim。這一整段說明沒有引用任何一個 claim。這一整段說明沒有引用任何一個 claim。這一整段說明沒有引用任何一個 claim。")
        errs, warns, _ = self.check(uncited)
        self.assertEqual(errs, [], errs)
        self.assertTrue(any("of the body sentences cite a claim" in w for w in warns), (errs, warns))

    def test_codex_style_formatting_is_accepted(self):
        (self.src / "cap.c").write_text("static const Cfg c = {\n    .mode = FAST,\n    .hyst = 30, /* note `x` */\n};\nint other;\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "cap.c")
        for quote in ("`.mode = FAST,`<br>`.hyst = 30, /* note \\`x\\` */`", "`.mode = FAST,`<br/>`.hyst = 30,`"):
            text = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2-3` | {quote}")
            self.assertFalse(any("S1" in e for e in self.check(text)[0]), quote)
        listed = self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2,4` | `.mode = FAST,` <br> `int other;`")
        self.assertFalse(any("S1" in e for e in self.check(listed)[0]), self.check(listed)[0])
        self.assertTrue(any("S1" in e for e in self.check(self.note.replace(f"{s}:3` | `ival = t >> 1;`", f"{s}:2,4` | `.mode = SLOW,`"))[0]))

    def test_citations_after_the_full_stop_belong_to_the_sentence_before_it(self):
        s = str(self.src / "cap.c")
        zh = self.note.replace("The interval is halved once in the interrupt handler [S1].", "中斷處理程式把區間值右移一位得到半值。[S1][S2]").replace("The handler keeps the halved value for the output [S1].", "處理程式在每次擷取時更新區間變數而且之後都會用到它。[S1]")
        errs, warns, _ = self.check(zh)
        self.assertEqual(errs, [], errs)

    def test_findings_text_lists_each_claim(self):
        _, _, claims = self.check()
        t = nc.findings_text("Q?", claims)
        self.assertIn("- S3 the sum is formed from the intervals | inference: S1, S2 | inferred", t)


    def test_relative_source_paths_become_absolute_and_free_form_premises_become_a_premise_list(self):
        rel = self.note.replace(f"`{self.src}/cap.c:3`", "`cap.c:3`").replace(f"`{self.src}/cap.c:4`", "`cap.c:4`").replace(f"src={self.src}/cap.c:3-4", "src=cap.c:3-4")
        rel = rel.replace("inference: S1, S2", "前提：S1、S2")
        self.assertGreater(len(self.check(rel)[0]), 0)  # as written it fails
        fixed = nc.normalize_paths(rel, [str(self.src)])
        self.assertEqual(self.check(fixed)[0], [], self.check(fixed)[0])
        self.assertIn("inference: S1, S2", fixed)

    def test_a_path_written_with_a_root_alias_in_front_is_resolved_and_an_ambiguous_one_is_left(self):
        other = self.tmp / "other"
        other.mkdir()
        (other / "cap.c").write_text("x\n", encoding="utf-8")
        alias = self.note.replace(f"`{self.src}/cap.c:3`", "`SDK/cap.c:3`")
        self.assertIn(f"{self.src}/cap.c:3", nc.normalize_paths(alias, [str(self.src)]))
        ambiguous = self.note.replace(f"`{self.src}/cap.c:3`", "`cap.c:3`")
        self.assertIn("`cap.c:3`", nc.normalize_paths(ambiguous, [str(self.src), str(other)]))  # two roots hold cap.c: not guessed

    def test_a_multi_line_quote_with_a_short_line_is_found(self):
        (self.src / "gate.c").write_text("if (ertm > 1050)\n    {\n\t\tif (t < 1) // x y z\n\t\t\treturn;\n    }\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "gate.c")
        text = self.note.replace("## Provenance\n", f"## Provenance\n| S9 | verified | `{s}:1-4` | `\t\tif (ertm > 1050)`<br>`    {{`<br>`\t\t\tif (t < 1) // x y z`<br>`\t\t\t\treturn;` | the gate |\n", 1)
        text = text.replace("|---|---|---|---|---|\n| S9", "|---|---|---|---|---|\n| S9").replace("| id | status | source | quote | claim |\n|---|---|---|---|---|\n| S9 | verified", "| id | status | source | quote | claim |\n|---|---|---|---|---|\n| S9 | verified")
        errs, _, _ = self.check(text)
        self.assertFalse([e for e in errs if e.startswith("S9")], errs)

    def test_form_warnings_catch_a_note_as_evidence_an_unpaired_fence_review_ids_and_simplified_characters(self):
        (self.src / "n.md").write_text("a note says ival is halved\n", encoding="utf-8")
        nc._cache.clear()
        s = str(self.src / "n.md")
        bad = self.note.replace("## Provenance\n| id | status | source | quote | claim |\n|---|---|---|---|---|\n", f"## Provenance\n| id | status | source | quote | claim |\n|---|---|---|---|---|\n| S8 | verified | `{s}:1` | `a note says ival is halved` | ival is halved |\n", 1)
        bad = bad.replace("## References", "Look (R1-2) at this 开始 text.\n\n```\nunclosed fence\n\n## References", 1)
        warns = " | ".join(self.check(bad)[1])
        self.assertIn("S8: marked verified, but the source is a note", warns)
        self.assertIn("without its partner", warns)
        self.assertIn("R1-2", warns)
        self.assertIn("Simplified Chinese characters", warns)
        self.assertNotIn("Simplified", " ".join(self.check()[1]))  # the good note gets none of these
        self.assertNotIn("without its partner", " ".join(self.check()[1]))


    def test_normalisation_is_narrow(self):
        unknown_prefix = self.note.replace(f"`{self.src}/cap.c:3`", "`whatever/cap.c:3`")
        self.assertIn("`whatever/cap.c:3`", nc.normalize_paths(unknown_prefix, [str(self.src)]))  # an unrecognised prefix is not stripped to find some other file
        verified_premises = self.note.replace(f"`{self.src}/cap.c:3`", "前提：S1、S2")
        self.assertIn("| verified | 前提：S1、S2 |", nc.normalize_paths(verified_premises, [str(self.src)]))  # only an inferred row gets its premises rewritten

    def test_a_longer_identifier_is_not_a_review_id(self):
        text = self.note.replace("## References", "The DDR1-2 bank and the R1-2 point.\n\n## References", 1)
        warns = " | ".join(self.check(text)[1])
        self.assertIn("(R1-2)", warns)
        self.assertNotIn("DDR1-2", warns)


if __name__ == "__main__":
    unittest.main()
