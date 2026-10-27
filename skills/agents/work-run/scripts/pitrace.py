#!/usr/bin/env python3
"""Trace of a pi work session, from the session JSONL pi keeps in <workdir>/pi-sessions (the counterpart of sessiontrace.py for opencode).
One line per session: wall time, tool calls, the largest context, the largest single output against the output limit, the time the model spent
generating (pi reports the duration of each model call; opencode's trace separates thinking, so the two are not the same column), compactions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def output_limit(model_id: str) -> int | None:
    try:
        d = json.loads((Path(__file__).resolve().parent.parent / "pi-agent" / "models.json").read_text(encoding="utf-8"))
        for p in d["providers"].values():
            for m in p["models"]:
                if m["id"] == model_id:
                    return m.get("maxTokens")
    except (OSError, ValueError, KeyError):
        pass
    return None


def summarize(rows: list[dict]) -> dict:
    msgs = [r["message"] for r in rows if r.get("type") == "message"]
    asst = [m for m in msgs if m.get("role") == "assistant"]
    stamps = [m.get("timestamp") for m in msgs if isinstance(m.get("timestamp"), (int, float))]
    wall = (max(stamps) - min(stamps)) / 1000 if stamps else 0.0
    calls = sum(1 for m in asst for c in m.get("content", []) if c.get("type") == "toolCall")
    errs = sum(1 for m in msgs if m.get("role") == "toolResult" and m.get("isError"))
    ctx = [(m.get("usage") or {}).get("input", 0) + (m.get("usage") or {}).get("cacheRead", 0) for m in asst]
    out = [(m.get("usage") or {}).get("output", 0) for m in asst]
    gen = sum(m.get("durationMs", 0) for m in asst) / 1000
    model = next((m.get("model") for m in asst if m.get("model")), "")
    lim = output_limit(model)
    return {"model": model, "wall_s": round(wall), "model_s": round(gen), "calls": calls, "tool_err": errs, "messages": len(asst),
            "ctx_max": max(ctx, default=0), "out_max": max(out, default=0), "out_limit": lim,
            "out_pct": round(100 * max(out, default=0) / lim) if lim else None,
            "compactions": sum(1 for r in rows if r.get("type") == "compaction"),
            "truncated": sum(1 for m in asst if m.get("stopReason") == "length")}


def render(s: dict) -> str:
    pct = f"{s['out_max']} ({s['out_pct']}% of {s['out_limit']})" if s["out_pct"] is not None else str(s["out_max"])
    share = f" ({round(100 * s['model_s'] / s['wall_s'])}%)" if s["wall_s"] else ""
    return (f"work  {s['wall_s']}s  calls {s['calls']}  ctx_max {s['ctx_max']}  out_max {pct}  model {s['model_s']}s{share}  tool_err {s['tool_err']}  "
            f"compactions {s['compactions']}  cut-off-by-length {s['truncated']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args()
    wd = Path(a.workdir).expanduser()
    files = sorted((wd / "pi-sessions").glob("*.jsonl"))
    if not files:
        print("no pi session in " + str(wd / "pi-sessions"))
        return 1
    rows = [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    s = summarize(rows)
    (wd / "trace").mkdir(exist_ok=True)
    (wd / "trace" / "pi-summary.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    for f in files:
        (wd / "trace" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    print(render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
