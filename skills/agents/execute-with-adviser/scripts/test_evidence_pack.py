#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Deterministic tests for evidence_pack.py; no adviser is invoked."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import adviser_bridge as bridge  # noqa: E402
import evidence_pack as packer  # noqa: E402


def evidence_request(uri: str, locator: str, *, excerpt: str | None = None):
    event = {
        "event": "advice-triggered",
        "event_id": "event-1",
        "trigger": "critical-fact",
        "trigger_source": "automatic",
        "condition": "A critical fact changes the method.",
        "blocking": True,
        "evidence_ready": True,
    }
    item = {"uri": uri, "locator": locator}
    if excerpt is not None:
        item["excerpt"] = excerpt
    return {
        "protocol": bridge.PROTOCOL_VERSION,
        "id": "request-1",
        "context_id": "task-1",
        "state": "submitted",
        "trigger": "critical-fact",
        "trigger_event": event,
        "review_scope": "evidence-read",
        "goal": "Choose the safe interpretation.",
        "question": "Which interpretation should be used?",
        "evidence": [item],
        "attempts": [],
        "constraints": ["Do not modify external state."],
        "executor_position": "The reversible interpretation is safer.",
        "fallback": "STOP",
        "data_classification": "public",
        "consult_index": 1,
        "consult_limit": 1,
    }


class EvidencePackTests(unittest.TestCase):
    def test_local_line_range_becomes_tool_free_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "evidence.txt"
            source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            packed = packer.pack_request(
                evidence_request(str(source), "lines 2-3"),
                base_dir=root,
            )

        self.assertEqual(packed["review_scope"], "summary-only")
        self.assertEqual(packed["evidence"][0]["excerpt"], "two\nthree\n")
        self.assertIs(bridge.validate_request(packed), packed)

    def test_relative_path_and_l_locator_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "evidence.txt"
            source.write_text("one\ntwo\nthree\n", encoding="utf-8")
            packed = packer.pack_request(
                evidence_request("evidence.txt", "L2-L3"),
                base_dir=root,
            )
        self.assertEqual(packed["evidence"][0]["excerpt"], "two\nthree\n")

    def test_existing_excerpt_does_not_touch_url(self):
        request = evidence_request(
            "https://example.com/evidence",
            "claim",
            excerpt="executor already captured this claim",
        )
        packed = packer.pack_request(request, base_dir=Path.cwd())
        self.assertEqual(packed["review_scope"], "summary-only")
        self.assertEqual(
            packed["evidence"][0]["excerpt"], "executor already captured this claim"
        )

    def test_url_without_excerpt_requires_executor_evidence(self):
        with self.assertRaisesRegex(packer.EvidencePackError, "does not fetch URLs"):
            packer.pack_request(
                evidence_request("https://example.com/evidence", "claim"),
                base_dir=Path.cwd(),
            )

    def test_ambiguous_locator_is_rejected_without_search(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "evidence.txt"
            source.write_text("claim\n", encoding="utf-8")
            with self.assertRaisesRegex(packer.EvidencePackError, "explicit line"):
                packer.pack_request(
                    evidence_request(str(source), "section: conclusion"),
                    base_dir=source.parent,
                )

    def test_binary_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "evidence.bin"
            source.write_bytes(b"\x00\xff\x00\xff\n")
            with self.assertRaisesRegex(packer.EvidencePackError, "not UTF-8"):
                packer.pack_request(
                    evidence_request(str(source), "line 1"),
                    base_dir=source.parent,
                )

    def test_read_range_is_killed_when_source_blocks(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "blocked-evidence"
            os.mkfifo(fifo)
            started = time.monotonic()
            with self.assertRaisesRegex(packer.EvidencePackError, "timed out"):
                packer.pack_request(
                    evidence_request(str(fifo), "line 1"),
                    base_dir=fifo.parent,
                    file_timeout_seconds=0.5,
                    total_timeout_seconds=1.0,
                )
            self.assertLess(time.monotonic() - started, 3.0)

    def test_line_and_total_budgets_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "evidence.txt"
            source.write_text("one\ntwo\nthree\n", encoding="utf-8")
            with self.assertRaisesRegex(packer.EvidencePackError, "bounded pack limit"):
                packer.pack_request(
                    evidence_request(str(source), "lines 1-3"),
                    base_dir=source.parent,
                    max_lines=2,
                )


class FindingsEvidenceTests(unittest.TestCase):
    def _tree(self, tmp: Path):
        (tmp / "src").mkdir()
        (tmp / "mcu").mkdir()
        (tmp / "src" / "main.c").write_text("".join(f"main {i}\n" for i in range(1, 400)), encoding="utf-8")
        (tmp / "mcu" / "config.c").write_text("".join(f"config {i}\n" for i in range(1, 400)), encoding="utf-8")
        return tmp

    def test_findings_references_become_bounded_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            findings = tmp / "f.md"
            findings.write_text(
                "- a | main.c:144-147 | verified\n- b | main.c:150 | verified\n- c | config.c:372-373 | verified\n- no line ref | dl_timer.h\n",
                encoding="utf-8",
            )
            items = packer.findings_evidence(str(findings), [tmp], max_lines=120)
            by_file = {Path(i["uri"]).name: i["locator"] for i in items}
            self.assertEqual(by_file["main.c"], "L144-150")  # nearby ranges merge
            self.assertEqual(by_file["config.c"], "L372-373")
            self.assertEqual(len(items), 2)

    def test_ambiguous_basename_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            (tmp / "mcu" / "main.c").write_text("x\n" * 10, encoding="utf-8")
            findings = tmp / "f.md"
            findings.write_text("- a | main.c:3 | verified\n", encoding="utf-8")
            with self.assertRaises(packer.EvidencePackError):
                packer.findings_evidence(str(findings), [tmp], max_lines=120)
            findings.write_text("- a | src/main.c:3 | verified\n", encoding="utf-8")
            items = packer.findings_evidence(str(findings), [tmp], max_lines=120)
            self.assertTrue(items[0]["uri"].endswith("src/main.c"))

    def test_missing_file_and_too_many_ranges_fail_loud(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            findings = tmp / "f.md"
            findings.write_text("- a | nothere.c:3 | verified\n", encoding="utf-8")
            with self.assertRaises(packer.EvidencePackError):
                packer.findings_evidence(str(findings), [tmp], max_lines=120)
            findings.write_text("".join(f"- x | main.c:{i * 20} | v\n" for i in range(1, 20)) , encoding="utf-8")
            with self.assertRaises(packer.EvidencePackError):
                packer.findings_evidence(str(findings), [tmp], max_lines=120, max_items=5)

    def test_cli_merges_findings_into_a_packed_request(self):
        import json, subprocess
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            findings = tmp / "f.md"
            findings.write_text("- a | main.c:10-12 | verified\n", encoding="utf-8")
            req = evidence_request(str(tmp / "mcu" / "config.c"), "L1-2")
            (tmp / "req.json").write_text(json.dumps(req), encoding="utf-8")
            out = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "evidence_pack.py"), "--request", str(tmp / "req.json"),
                 "--from-findings", str(findings), "--root", str(tmp)],
                capture_output=True, text=True,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            packed = json.loads(out.stdout)
            self.assertEqual(packed["review_scope"], "summary-only")
            self.assertEqual(len(packed["evidence"]), 2)
            self.assertIn("main 10", packed["evidence"][1]["excerpt"])

    def test_context_padding_and_artifact_chunks(self):
        import json, subprocess
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            findings = tmp / "f.md"
            findings.write_text("- a | main.c:100 | verified\n", encoding="utf-8")
            items = packer.findings_evidence(str(findings), [tmp], max_lines=120, context=5)
            self.assertEqual(items[0]["locator"], "L95-105")
            note = tmp / "note.md"
            note.write_text("".join(f"line {i} " + "x" * 60 + "\n" for i in range(1, 301)), encoding="utf-8")
            chunks = packer.artifact_evidence(str(note), max_lines=120)
            self.assertGreater(len(chunks), 2)
            self.assertTrue(all(c["uri"].endswith("note.md") for c in chunks))
            req = evidence_request(str(tmp / "mcu" / "config.c"), "L1-2")
            (tmp / "req.json").write_text(json.dumps(req), encoding="utf-8")
            out = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "evidence_pack.py"), "--request", str(tmp / "req.json"),
                 "--from-findings", str(findings), "--root", str(tmp), "--artifact", str(note)],
                capture_output=True, text=True,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            packed = json.loads(out.stdout)
            uris = [e["uri"] for e in packed["evidence"]]
            self.assertTrue(any(u.endswith("note.md") for u in uris))
            self.assertTrue(all(e.get("excerpt") for e in packed["evidence"]))

    def test_l_notation_comma_lists_and_silent_empty_findings(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            findings = tmp / "f.md"
            findings.write_text("- a | config.c L10 | v\n- b | main.c L100,120 | v\n- c | main.c line 150-152 | v\n", encoding="utf-8")
            items = packer.findings_evidence(str(findings), [tmp], max_lines=120)
            got = sorted((Path(i["uri"]).name, i["locator"]) for i in items)
            self.assertEqual(got, [("config.c", "L10-10"), ("main.c", "L100-100"), ("main.c", "L120-120"), ("main.c", "L150-152")])
            findings.write_text("- this is a long finding without any usable reference. " * 8, encoding="utf-8")
            with self.assertRaises(packer.EvidencePackError):
                packer.findings_evidence(str(findings), [tmp], max_lines=120)

    def test_first_root_wins_over_later_roots(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = self._tree(Path(raw))
            sdk = tmp / "sdk"
            (sdk / "a").mkdir(parents=True)
            (sdk / "b").mkdir(parents=True)
            (sdk / "a" / "main.c").write_text("x\n" * 10, encoding="utf-8")
            (sdk / "b" / "main.c").write_text("y\n" * 10, encoding="utf-8")
            findings = tmp / "f.md"
            findings.write_text("- a | main.c:3 | v\n", encoding="utf-8")
            # project root has exactly one main.c; the SDK root has two, but is never consulted
            items = packer.findings_evidence(str(findings), [tmp / "src", sdk], max_lines=120)
            self.assertTrue(items[0]["uri"].endswith("src/main.c"))
            with self.assertRaises(packer.EvidencePackError):
                packer.findings_evidence(str(findings), [sdk], max_lines=120)


if __name__ == "__main__":
    unittest.main()
