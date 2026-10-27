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


if __name__ == "__main__":
    unittest.main()
