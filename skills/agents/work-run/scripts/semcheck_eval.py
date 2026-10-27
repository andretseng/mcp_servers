#!/usr/bin/env python3
"""Measure semcheck.py on semcheck_labeled.json (hand-labelled claim/evidence pairs from real executor answers, plus mismatched pairs).
Usage: semcheck_eval.py [--model ID] [--root DIR]   Prints per-row verdicts and: false alarms on supported rows, catch rate on wrong rows."""
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import semcheck as sc

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=sc.MODEL)
ap.add_argument("--root", action="append", default=["/home/andre/workspace_ccstheia/drone/escape32_msp"])
ap.add_argument("--out")
ap.add_argument("--mode", choices=["fast", "think", "hybrid"], default="hybrid")
a = ap.parse_args()
data = json.load(open(Path(__file__).resolve().parent / "semcheck_labeled.json"))
roots = [Path(r) for r in a.root]
files = sc.source_files(roots)
res, t0 = [], time.time()
for d in data:
    fast = (lambda p: sc.ask(p, a.model)) if a.mode != "think" else (lambda p: sc.ask(p, a.model, think=True))
    slow = (lambda p: sc.ask(p, a.model, think=True)) if a.mode == "hybrid" else None
    v, why, _first = sc.judge(d, files, fast, slow)
    res.append({**d, "verdict": v, "why": why})
    print(f"{d['id']:9s} {d['kind']:10s} want {str(d['label']):13s} got {v:13s} {why[:80]}", flush=True)
sup = [r for r in res if r["kind"] == "supported"]
wrong = [r for r in res if r["label"] == "NOT_SUPPORTED"]
print(f"\nsupported rows: {sum(r['verdict'] == 'SUPPORTED' for r in sup)}/{len(sup)} passed; flagged (false alarm) {sum(r['verdict'] != 'SUPPORTED' for r in sup)}")
print(f"wrong rows: NOT_SUPPORTED {sum(r['verdict'] == 'NOT_SUPPORTED' for r in wrong)}, UNCLEAR {sum(r['verdict'] == 'UNCLEAR' for r in wrong)}, "
      f"missed (SUPPORTED) {sum(r['verdict'] == 'SUPPORTED' for r in wrong)} of {len(wrong)}")
for kind in ("real-wrong", "mismatch"):
    w = [r for r in wrong if r["kind"] == kind]
    print(f"  {kind}: flagged {sum(r['verdict'] != 'SUPPORTED' for r in w)}/{len(w)}")
print(f"{time.time() - t0:.0f}s for {len(res)} rows")
if a.out:
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
