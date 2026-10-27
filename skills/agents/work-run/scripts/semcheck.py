#!/usr/bin/env python3
"""Semantic evidence check by a local model (Qwen on oMLX): does the evidence the executor quoted directly support the claim?

The harness (interactive gate, notecheck) already checks form: a table exists, a `verified` row quotes code, the quote is in a file the run opened, a comment is not evidence.
This script asks the one question the harness cannot: reading the quote literally, does it show what the claim says? One row per call, thinking off, short answer:
SUPPORTED | NOT_SUPPORTED: <reason> | UNCLEAR: <what is missing>. It only reports; nothing is blocked. Only `verified` rows are checked (inferred, external and unverified rows
carry their own premises or are the adviser's job). Output: one line per row, then a summary line.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rowaudit import parse  # noqa: E402  (the claims-table parser is shared with rowaudit)

URL = os.environ.get("SEMCHECK_URL", "http://dreammaker.tail1f5a73.ts.net:8000/v1/chat/completions")
KEY = Path(os.environ.get("SEMCHECK_KEY", str(Path.home() / ".config/omlx/key")))
MODEL = os.environ.get("SEMCHECK_MODEL", "Qwen3.8-27B-4bit-MTP")
SOURCE_EXT = (".c", ".h", ".inc", ".cc", ".cpp", ".s", ".syscfg")
SYSTEM = ("You check whether quoted evidence supports a claim. You do not solve the question and you do not use outside knowledge. "
          "A code comment, a macro name or a note is not evidence of behaviour; executable code and quoted specification text are. "
          "Answer with exactly one line: `SUPPORTED`, or `NOT_SUPPORTED: <reason in at most 20 words>`, or `UNCLEAR: <what is missing, at most 20 words>`.")


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def source_files(roots: list[Path], seen: list[Path] | None = None) -> list[Path]:
    """The files evidence may come from: the ones the run opened (seen) when given, else every source file under the roots."""
    if seen is not None:
        return [p for p in seen if p.suffix in SOURCE_EXT and p.is_file()]
    return [p for r in roots for ext in SOURCE_EXT for p in r.rglob("*" + ext) if p.is_file()]


def cited_context(evidence: str, files: list[Path], radius: int = 4, cap: int = 40) -> str:
    """The lines a `path:line` in the evidence points to (only when the path names exactly one of the files)."""
    out = []
    for m in re.finditer(r"([\w./-]+\.(?:c|h|inc|cc|cpp))[:`\s]*L?(\d+)(?:\s*[-\u2013]\s*(\d+))?", evidence):
        name, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        hits = [p for p in files if str(p).endswith(name)]
        if len(hits) != 1:
            continue
        ls = hits[0].read_text(encoding="utf-8", errors="replace").split("\n")
        lo, hi = max(1, a - radius), min(len(ls), b + radius)
        out.append(f"--- {name} lines {lo}-{hi}\n" + "\n".join(f"{n}: {ls[n - 1]}" for n in range(lo, min(hi, lo + cap) + 1)))
    return "\n".join(out[:3])


def quote_context(evidence: str, files: list[Path], radius: int = 2, cap: int = 3) -> str:
    """Find each backticked code quote in the files and return the lines around it (for evidence that gives no usable path:line)."""
    quotes = [q for q in re.findall(r"`([^`]{12,})`", evidence) if not re.fullmatch(r"[\w./~-]+(:\d+(-\d+)?)?", q)]
    out = []
    for q in quotes[:cap]:
        key = norm(q)[:80]
        for p in files:
            try:
                ls = p.read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                continue
            hit = next((i for i, l in enumerate(ls) if key in norm(l)), None)
            if hit is not None:
                lo, hi = max(0, hit - radius), min(len(ls), hit + radius + 1)
                out.append(f"--- {p.name} lines {lo + 1}-{hi}\n" + "\n".join(f"{n + 1}: {ls[n].rstrip()}" for n in range(lo, hi)))
                break
    return "\n".join(out)


def ask(user: str, model: str = MODEL, think: bool = False) -> str:
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], "max_tokens": 6000 if think else 100,
            "temperature": 0.1, "chat_template_kwargs": {"enable_thinking": think}}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Authorization": "Bearer " + KEY.read_text().strip(), "Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=900))
    return " ".join((resp["choices"][0]["message"].get("content") or "").split())


def ask_think(user: str, model: str = MODEL) -> str:
    return ask(user, model, think=True)


def verdict(text: str) -> tuple[str, str]:
    t = text.strip().strip("`")
    m = re.match(r"(NOT_SUPPORTED|UNCLEAR|SUPPORTED)\b[:\s-]*(.*)", t, re.I)
    if not m:
        return "UNCLEAR", "no verdict in the answer: " + t[:80]
    return m.group(1).upper(), m.group(2).strip()


def build_prompt(row: dict, files: list[Path]) -> str:
    ev = row.get("evidence", "")
    ctx = cited_context(ev, files) or quote_context(ev, files)
    return (f"Claim: {row.get('claim', '')}\nEvidence the executor quoted: {ev}\n" + (f"Source lines at that evidence (read by the script):\n{ctx}\n" if ctx else "No source lines could be found for this evidence.\n") +
            "Question: read the evidence literally. Does it directly show what the claim says? If the claim needs an assumption, a value that is not shown, or a step the evidence does not make, "
            "answer UNCLEAR. If the evidence is about something else or contradicts the claim, answer NOT_SUPPORTED.")


def judge(row: dict, files: list[Path], fast=ask, slow=ask_think) -> tuple[str, str, tuple | None]:
    """Stage 1: fast call, thinking off, every verified row. Stage 2: only a row stage 1 did not call SUPPORTED is asked again with thinking on, and stage 2 decides
    (measured on 35 hand-labelled rows: thinking off alone flagged 6 of 13 correct rows, thinking on alone is 13x slower; the two together flagged 2 of 13)."""
    ev = row.get("evidence", "")
    if not (cited_context(ev, files) or quote_context(ev, files)):
        return "NO_SOURCE", "no source lines found for this evidence in the files the run opened, so the quote could not be checked", None
    prompt = build_prompt(row, files)
    v, why = verdict(fast(prompt))
    if v == "SUPPORTED" or slow is None:
        return v, why, None
    v2, why2 = verdict(slow(prompt))
    return v2, why2, (v, why)


def check_rows(rows: list[dict], roots: list[Path], fast=ask, slow=ask_think, seen: list[Path] | None = None, budget: float | None = None) -> list[dict]:
    """budget: seconds after which stage 2 (thinking) is skipped; a flagged row then keeps its stage 1 verdict, marked as not re-checked."""
    budget = float(os.environ.get("SEMCHECK_BUDGET", "420")) if budget is None else budget
    files = source_files(roots, seen)
    t0 = time.time()
    out = []
    for i, r in enumerate(rows, 1):
        if not r.get("basis", "").lower().replace("`", "").startswith("verified"):
            continue
        use_slow = slow if os.environ.get("SEMCHECK_STAGE2") != "off" else None
        skipped = False
        if use_slow is not None and time.time() - t0 > budget:
            use_slow, skipped = None, True
        v, why, first = judge(r, files, fast, use_slow)
        if skipped and v not in ("SUPPORTED", "NO_SOURCE"):
            why += " [stage 2 skipped: time budget used up, not re-checked with thinking]"
        out.append({"row": i, "claim": r.get("claim", "")[:90], "verdict": v, "why": why, "first": first})
    return out


def report(results: list[dict]) -> str:
    lines = []
    for x in results:
        if x["verdict"] != "SUPPORTED":
            lines.append(f"[SEMCHECK] row {x['row']} \"{x['claim'][:60]}\": {x['verdict']} {x['why']}".rstrip())
    n_ok = sum(x["verdict"] == "SUPPORTED" for x in results)
    n_src = sum(x["verdict"] == "NO_SOURCE" for x in results)
    lines.append(f"[SEMCHECK] {len(results)} verified rows: {n_ok} supported, {len(results) - n_ok - n_src} to confirm, {n_src} could not be checked (no source lines) - report only, nothing blocked")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", required=True)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--seen-file", help="a text file with one absolute path per line: the files the run opened; evidence is looked up only there")
    ap.add_argument("--json")
    a = ap.parse_args()
    _, rows = parse(Path(a.answer).read_text(encoding="utf-8"))
    t0 = time.time()
    seen = [Path(l.strip()) for l in Path(a.seen_file).read_text(encoding="utf-8").splitlines() if l.strip()] if a.seen_file else None
    res = check_rows(rows, [Path(r) for r in a.root], seen=seen)
    print(report(res) + f"  ({time.time() - t0:.1f}s)")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
