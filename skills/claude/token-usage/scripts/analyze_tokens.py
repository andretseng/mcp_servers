#!/usr/bin/env python3
"""Analyze Claude Code session token usage per actor (main agent + subagents).

Usage:
  analyze_tokens.py <session-id-or-prefix-or-jsonl-path> [--json]
  analyze_tokens.py --latest [--project <path-slug-dir>] [--json]

Reads the session transcript (~/.claude/projects/<proj>/<session>.jsonl) and all
subagent transcripts (<session>/subagents/agent-*.jsonl + .meta.json), dedupes
API requests by requestId, and reports per-actor token/cost breakdown.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# USD per MTok (input, output). Source: claude-api skill, cached 2026-06-24.
# Cache multipliers on input price: read 0.1x, write 5m TTL 1.25x, write 1h TTL 2x.
PRICING = [
    ("fable", (10.0, 50.0)),
    ("mythos", (10.0, 50.0)),
    ("opus", (5.0, 25.0)),
    ("sonnet", (3.0, 15.0)),
    ("haiku", (1.0, 5.0)),
]
CACHE_READ_X, CACHE_W5M_X, CACHE_W1H_X = 0.1, 1.25, 2.0


def price_for(model):
    m = (model or "").lower()
    for key, p in PRICING:
        if key in m:
            return p
    return (5.0, 25.0)  # unknown -> assume opus-tier


def find_transcript(arg):
    if arg and os.path.isfile(arg):
        return arg
    pattern = f"{arg or ''}*.jsonl"
    hits = glob.glob(os.path.join(PROJECTS_DIR, "*", pattern))
    if not hits:
        sys.exit(f"error: no transcript matching '{arg}' under {PROJECTS_DIR}")
    if len(hits) > 1:
        sys.exit("error: ambiguous session prefix, matches:\n  " + "\n  ".join(hits))
    return hits[0]


def latest_transcript(project_dir=None):
    pattern = os.path.join(project_dir or os.path.join(PROJECTS_DIR, "*"), "*.jsonl")
    hits = glob.glob(pattern)
    if not hits:
        sys.exit("error: no transcripts found")
    return max(hits, key=os.path.getmtime)


def iter_requests(jsonl_path):
    """Yield one record per unique API request (deduped by requestId)."""
    seen = set()
    with open(jsonl_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            rid = d.get("requestId") or msg.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            yield {
                "model": msg.get("model", "?"),
                "skill": d.get("attributionSkill"),
                "usage": usage,
            }


def new_stats():
    return {
        "requests": 0, "input": 0, "cache_w5m": 0, "cache_w1h": 0,
        "cache_read": 0, "output": 0, "models": defaultdict(int),
        "cold_start": None, "last_ctx": 0, "cost": 0.0,
    }


def accumulate(stats, rec):
    u = rec["usage"]
    inp = u.get("input_tokens", 0)
    cw = u.get("cache_creation_input_tokens", 0)
    cr = u.get("cache_read_input_tokens", 0)
    out = u.get("output_tokens", 0)
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens", 0)
    w1 = cc.get("ephemeral_1h_input_tokens", 0)
    if not cc:
        w5 = cw  # older format: assume 5m
    stats["requests"] += 1
    stats["input"] += inp
    stats["cache_w5m"] += w5
    stats["cache_w1h"] += w1
    stats["cache_read"] += cr
    stats["output"] += out
    stats["models"][rec["model"]] += 1
    ctx = inp + cw + cr
    if stats["cold_start"] is None:
        stats["cold_start"] = ctx
    stats["last_ctx"] = max(stats["last_ctx"], ctx)
    pin, pout = price_for(rec["model"])
    stats["cost"] += (
        inp * pin + w5 * pin * CACHE_W5M_X + w1 * pin * CACHE_W1H_X
        + cr * pin * CACHE_READ_X + out * pout
    ) / 1e6


def fmt_k(n):
    if n is None:
        return "-"
    return f"{n/1000:.1f}K" if n >= 1000 else str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="session id/prefix or .jsonl path")
    ap.add_argument("--latest", action="store_true", help="most recently modified session")
    ap.add_argument("--project", help="project dir under ~/.claude/projects (with --latest)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.latest or not args.session:
        proj = os.path.join(PROJECTS_DIR, args.project) if args.project else None
        path = latest_transcript(proj)
    else:
        path = find_transcript(args.session)

    session_id = os.path.basename(path)[:-6]
    actors = {}

    main_stats = new_stats()
    skills = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    for rec in iter_requests(path):
        accumulate(main_stats, rec)
        if rec["skill"]:
            s = new_stats()
            accumulate(s, rec)
            skills[rec["skill"]]["tokens"] += s["input"] + s["cache_w5m"] + s["cache_w1h"] + s["output"]
            skills[rec["skill"]]["cost"] += s["cost"]
    actors["main (orchestrator)"] = main_stats

    sub_dir = os.path.join(os.path.dirname(path), session_id, "subagents")
    for sub in sorted(glob.glob(os.path.join(sub_dir, "agent-*.jsonl"))):
        meta_path = sub[:-6] + ".meta.json"
        meta = {}
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        agent_id = os.path.basename(sub)[6:-6]
        label = f"{meta.get('agentType', 'subagent')} [{agent_id[:7]}]"
        desc = meta.get("description", "")
        stats = new_stats()
        for rec in iter_requests(sub):
            accumulate(stats, rec)
        stats["description"] = desc
        actors[label] = stats

    if args.json:
        out = {"session": session_id, "actors": {}, "skills": dict(skills)}
        for name, s in actors.items():
            out["actors"][name] = {k: (dict(v) if isinstance(v, defaultdict) else v)
                                   for k, v in s.items()}
        print(json.dumps(out, indent=2))
        return

    total_cost = sum(s["cost"] for s in actors.values())
    total_new = sum(s["input"] + s["cache_w5m"] + s["cache_w1h"] + s["output"]
                    for s in actors.values())

    print(f"Session: {session_id}")
    print(f"Transcript: {path}")
    print()
    hdr = (f"{'actor':<34}{'reqs':>6}{'input':>9}{'cache-wr':>10}{'cache-rd':>10}"
           f"{'output':>9}{'coldstart':>10}{'max-ctx':>9}{'est-cost':>10} {'%':>5}")
    print(hdr)
    print("-" * len(hdr))
    for name, s in sorted(actors.items(), key=lambda kv: -kv[1]["cost"]):
        if s["requests"] == 0:
            continue
        cw = s["cache_w5m"] + s["cache_w1h"]
        pct = 100 * s["cost"] / total_cost if total_cost else 0
        print(f"{name:<34}{s['requests']:>6}{fmt_k(s['input']):>9}{fmt_k(cw):>10}"
              f"{fmt_k(s['cache_read']):>10}{fmt_k(s['output']):>9}"
              f"{fmt_k(s['cold_start']):>10}{fmt_k(s['last_ctx']):>9}"
              f"{'$%.4f' % s['cost']:>10} {'%.1f%%' % pct:>5}")
        models = ", ".join(f"{m}x{n}" for m, n in s["models"].items())
        line2 = f"    model: {models}"
        if s.get("description"):
            line2 += f" | {s['description'][:60]}"
        print(line2)
    print("-" * len(hdr))
    print(f"{'TOTAL':<34}{'':>6}{'':>9}{'':>10}{'':>10}{'':>9}{'':>10}{'':>9}"
          f"{'$%.4f' % total_cost:>10}")
    print()
    print(f"New tokens processed (input+cache-write+output): {fmt_k(total_new)}")
    all_read = sum(s["cache_read"] for s in actors.values())
    all_wr = sum(s["cache_w5m"] + s["cache_w1h"] for s in actors.values())
    all_in = sum(s["input"] for s in actors.values())
    denom = all_read + all_wr + all_in
    if denom:
        print(f"Cache hit rate (read / all prompt tokens): {100*all_read/denom:.1f}%")
    if skills:
        print("\nBy skill attribution (main agent only):")
        for name, v in sorted(skills.items(), key=lambda kv: -kv[1]["cost"]):
            print(f"  {name:<30} new-tokens={fmt_k(v['tokens'])}  est-cost=${v['cost']:.4f}")
    print("\nNotes: cost uses list prices (claude-api skill, cached 2026-06-24);")
    print("cache read=0.1x, write 5m=1.25x, 1h=2x of input price. Cold start =")
    print("first request's total prompt (system prompt + CLAUDE.md + memory + tools).")


if __name__ == "__main__":
    main()
