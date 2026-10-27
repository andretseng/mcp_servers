#!/usr/bin/env python3
"""Evidence audit by a local reviewer (Qwen on oMLX), one claims-table row at a time.

The executor's answer ends with a table `| claim | basis | how calculated | evidence | limit |`. This script does not solve anything: for each row it
gives the reviewer the row, the code lines the row cites (read by the script, so the reviewer opens nothing), and one narrow question. Thinking is off
(chat_template_kwargs enable_thinking=false), so each call takes a second or two. Rows whose basis is `external` (a specification or web page) are not
judged by the local model: its own memory of such facts is not evidence, so they are handed to the adviser (Codex).
Output, one line per item: [OK] / [CHECK] <action for the executor> (<gap>) / [EXTERNAL] ..., then `VERDICT: AGREE|CHALLENGE`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

import os
URL = os.environ.get("ROWAUDIT_URL", "http://dreammaker.tail1f5a73.ts.net:8000/v1/chat/completions")
MODEL = "Qwen3.8-27B-4bit-MTP"
KEY = Path(os.environ.get("ROWAUDIT_KEY", str(Path.home() / ".config/omlx/key")))
CONFIG = {"model": MODEL, "think": False, "in": 0, "out": 0}  # --model swaps the local reviewer (for example a Gemma build) without touching anything else
SYSTEM = ("You are an evidence auditor. You do not solve the question. A code comment, a variable or macro name, a note, or a document that restates a claim is a claim, "
          "not evidence. Evidence is executable code or a measurement. Answer with ONE line only, in exactly this format: `[OK]` or "
          "`[CHECK] <exact action for the executor> (<gap: reasoning | calculation | evidence | wrong-basis | overstated>)`. No other text.")


def ask(user: str, max_tokens: int = 160) -> str:
    body = {"model": CONFIG["model"], "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], "max_tokens": max_tokens * (25 if CONFIG["think"] else 1), "temperature": float(os.environ.get("ROWAUDIT_TEMP", "0.3")),
            "chat_template_kwargs": {"enable_thinking": bool(CONFIG["think"])}}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Authorization": "Bearer " + KEY.read_text().strip(), "Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=300))
    CONFIG["in"] += (resp.get("usage") or {}).get("prompt_tokens", 0); CONFIG["out"] += (resp.get("usage") or {}).get("completion_tokens", 0)
    msg = resp["choices"][0]["message"]
    return " ".join((msg.get("content") or "").split())


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(answer: str) -> tuple[str, list[dict]]:
    lines = answer.split("\n")
    for i in range(len(lines) - 1):
        if "|" in lines[i] and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            names = [c.lower() for c in cells(lines[i])]
            if any(n.startswith("claim") for n in names) and any(n.startswith("basis") for n in names):
                rows = []
                for l in lines[i + 2:]:
                    if "|" not in l:
                        break
                    c = cells(l)
                    rows.append({n.split()[0]: (c[j] if j < len(c) else "") for j, n in enumerate(names)})
                return "\n".join(lines[:i]).strip(), rows
    return answer.strip(), []


def code_context(evidence: str, roots: list[Path], radius: int = 4, cap: int = 40) -> str:
    out = []
    for m in re.finditer(r"([\w./-]+\.(?:c|h|inc|cc|cpp))[:`\s]*L?(\d+)(?:\s*[-–]\s*(\d+))?", evidence):
        name, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        hits = [p for r in roots for p in r.rglob(Path(name).name) if str(p).endswith(name)] if name else []
        if len(hits) != 1:
            continue
        ls = hits[0].read_text(encoding="utf-8", errors="replace").split("\n")
        lo, hi = max(1, a - radius), min(len(ls), b + radius)
        out.append(f"--- {name} lines {lo}-{hi}\n" + "\n".join(f"{n}: {ls[n - 1]}" for n in range(lo, min(hi, lo + cap) + 1)))
    return "\n".join(out[:3])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", required=True)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--out")
    ap.add_argument("--think", action="store_true", help="let the reviewer reason first (25x token budget); only for a fast model")
    ap.add_argument("--model", default=MODEL, help="the oMLX model id of the local reviewer")
    a = ap.parse_args()
    CONFIG["model"] = a.model
    CONFIG["think"] = a.think
    summary, rows = parse(Path(a.answer).read_text(encoding="utf-8"))
    roots = [Path(r) for r in a.root]
    if not rows:  # no usable claims table: say so, then audit the whole answer in one call (arithmetic, units, uncited or overstated statements)
        out = ["[CHECK] the answer has no usable claims table: write `| claim | basis | how calculated | evidence | limit |` with a header row, a separator row and one row per claim (gap: evidence)"]
        qp = Path(a.answer).parent / "question.md"
        wq = ("User's question:\n" + (qp.read_text(encoding="utf-8")[:1200] if qp.is_file() else "") + "\n\nThe executor's answer:\n" + summary[:5000] +
              "\n\nQuestion: list up to 5 problems with this answer, one per line, each `[CHECK] <what the executor must fix or show> (<gap: reasoning | calculation | evidence | wrong-basis | overstated>)`. "
              "Look for: arithmetic or unit mistakes (redo every number), a mechanism stated as certain without evidence, a code line or constant cited but not shown, "
              "a conclusion that rests on a comment, and a step that does not follow. If there are none, answer `[OK]`.")
        r = ask(wq, 500) or "[CHECK] no answer (evidence)"
        out.append(r)
        print("\n".join(out))
        print("VERDICT: CHALLENGE" if "[CHECK]" in r or True else "VERDICT: AGREE")
        if a.out:
            Path(a.out).write_text("\n".join(out) + "\nVERDICT: CHALLENGE\n", encoding="utf-8")
        return 1
    results, t0 = [], time.time()
    for r in rows:
        claim, basis = r.get("claim", ""), r.get("basis", "").lower()
        if "external" in basis or ("spec" in basis and "verified" not in basis):
            line = f"[EXTERNAL] {claim[:80]}: a specification or web claim, checked by the adviser, not by the local model"
        else:
            ctx = code_context(r.get("evidence", ""), roots)
            q = (f"Claim: {claim}\nBasis the executor gave: {r.get('basis', '')}\nHow calculated: {r.get('how', '')}\nEvidence the executor gave: {r.get('evidence', '')}\n"
                 f"Limit the executor gave: {r.get('limit', '')}\n" + (f"Code the evidence points to:\n{ctx}\n" if ctx else "") +
                 "Question: with only this, (1) does the reasoning follow, (2) does the arithmetic redo to the same result, (3) is the basis label right "
                 "(verified needs executable code; a comment or note is not enough; a mechanism no code can show must say inferred), (4) is the limit honest?")
            line = ask(q) or "[CHECK] no answer (gap: evidence)"
        results.append(line)
        print(f"{len(results)}. {line}", flush=True)
    sq = ("Summary the executor wrote for the user:\n" + summary[:2500] + "\n\nTable (claim | basis | limit):\n" +
          "\n".join(f"- {r.get('claim', '')[:90]} | {r.get('basis', '')[:60]} | {r.get('limit', '')[:120]}" for r in rows) +
          "\n\nQuestion: does the summary state as certain something that the table marks inferred or unverified, or that the table does not support? "
          "Answer `[OK]` or `[CHECK] <the sentence to soften or remove> (overstated)`.")
    s = ask(sq, 220) or "[CHECK] no answer (overstated)"
    results.append("summary: " + s)
    print("summary: " + s, flush=True)
    qp = Path(a.answer).parent / "question.md"
    if qp.is_file():
        mq = ("User's question:\n" + qp.read_text(encoding="utf-8")[:1500] + "\n\nClaims in the executor's table:\n" + "\n".join(f"- {r.get('claim', '')[:100]}" for r in rows) +
              "\n\nQuestion: the user asks WHY. Which claim that the explanation needs is missing from the table (for example how the wrong value came to be in the code, "
              "or whether a sibling code path has the same value)? Answer `[OK]` if none, else `[CHECK] <the missing claim the executor must establish> (evidence)`.")
        m_ = ask(mq, 220) or "[CHECK] no answer (evidence)"
        results.append("missing: " + m_)
        print("missing: " + m_, flush=True)
    bad = [x for x in results if "[CHECK]" in x]
    verdict = "VERDICT: CHALLENGE" if bad else "VERDICT: AGREE"
    print(f"{verdict}  ({round(time.time() - t0, 1)} s, {len(rows)} rows, tokens in {CONFIG['in']} / out {CONFIG['out']})")
    if a.out:
        Path(a.out).write_text("\n".join(results) + f"\n{verdict}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
