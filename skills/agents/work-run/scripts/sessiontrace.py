#!/usr/bin/env python3
"""Trace of the opencode sessions of one workdir, from `opencode session export`.

The gate log records tool inputs only. The export also holds the tool outputs, the reasoning with its timing, the
tokens of every model call and the finish reason (`length` = the answer was cut at the output limit). The raw exports are
copied to <workdir>/trace/ so they survive a cleanup of the opencode session store.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

def output_limit(model_id: str = "Ornith-1.5-35B-A3B-MLX-8bit") -> int | None:
    """The output limit opencode is configured with for the model (limit.output in the global config)."""
    try:
        d = json.loads((Path.home() / ".config/opencode/opencode.json").read_text(encoding="utf-8"))
        for prov in (d.get("provider") or d.get("providers") or {}).values():
            m = (prov.get("models") or {}).get(model_id)
            if m:
                return (m.get("limit") or {}).get("output")
    except (OSError, json.JSONDecodeError):
        pass
    return None


DOUBT = re.compile(r"wait[,.…—]|reconsider|hmm|actually,|let me (?:re-?check|double check|re-?examine)", re.I)


def _text(parts) -> str:
    return "".join(x.get("text", "") for x in (parts or []) if isinstance(x, dict))


def prompt_tokens(m: dict) -> int:
    """The context of a call: the new input plus what came from the cache (opencode reports them apart)."""
    t = m.get("tokens", {})
    c = t.get("cache") or {}
    return t.get("input", 0) + c.get("read", 0) + c.get("write", 0)


def summarize(export: dict) -> dict:
    info = export.get("info", {})
    msgs = [m for m in export.get("messages", []) if m.get("type") == "assistant"]
    finish = Counter(m.get("finish") for m in msgs)
    tools, out_bytes, errors = Counter(), Counter(), Counter()
    reasoning_ms, doubt = 0, 0
    for m in msgs:
        for c in m.get("content") or []:
            if c.get("type") == "reasoning":
                t = c.get("time") or {}
                if t.get("completed") and t.get("created"):
                    reasoning_ms += t["completed"] - t["created"]
                doubt += sum(1 for line in (c.get("text") or "").split("\n") if DOUBT.search(line))
            elif c.get("type") == "tool":
                name, st = c.get("name", "?"), c.get("state") or {}
                tools[name] += 1
                body = _text(st.get("content"))
                out_bytes[name] += len(body)
                if st.get("status") == "error":
                    errors[name] += 1
    t = info.get("time") or {}
    wall = (t.get("updated", 0) - t.get("created", 0)) / 1000 if t else 0
    # compaction: a message of a compaction/summary kind, or the context (input tokens) falling sharply from one call to the next
    types = Counter(m.get("type") for m in export.get("messages", []))
    markers = sum(n for k, n in types.items() if k and re.search(r"compact|summar", str(k), re.I))
    markers += sum(1 for m in msgs for c in (m.get("content") or []) if re.search(r"compact|summar", str(c.get("type", "")), re.I))
    inputs = [prompt_tokens(m) for m in msgs]
    drops = [[i, inputs[i - 1], inputs[i]] for i in range(1, len(inputs)) if inputs[i - 1] >= 20000 and inputs[i] < 0.6 * inputs[i - 1]]
    limit = output_limit()
    out_max = max((m.get("tokens", {}).get("output", 0) for m in msgs), default=0)
    return {
        "message_types": dict(types), "compaction_markers": markers, "context_drops": drops, "output_limit": limit,
        "output_limit_hit": bool(finish.get("length", 0)), "output_max_pct": round(100 * out_max / limit) if limit else None,
        "title": info.get("title"), "outcome": info.get("outcome"), "wall_s": round(wall), "calls": len(msgs),
        "finish": dict(finish), "truncated": finish.get("length", 0),
        "context_max": max((prompt_tokens(m) for m in msgs), default=0),
        "output_max": max((m.get("tokens", {}).get("output", 0) for m in msgs), default=0),
        "reasoning_s": round(reasoning_ms / 1000), "reasoning_share": round(reasoning_ms / 1000 / wall, 2) if wall else None,
        "doubt_lines": doubt, "tools": dict(tools), "tool_errors": dict(errors),
        "tool_output_bytes": dict(out_bytes),
    }


def collect(workdir: Path, opencode: str = "opencode") -> list[dict]:
    """Export every session the private server of this workdir knows; keep the raw exports under <workdir>/trace/."""
    ids = []
    for d in [workdir] + ([workdir / "work"] if (workdir / "work").is_dir() else []):  # the planner and the work session run in their own directories
        p = subprocess.run([opencode, "session", "list", "--standalone"], cwd=d, env=dict(os.environ, PWD=str(d)), capture_output=True, text=True)
        ids += [line.split()[0] for line in p.stdout.splitlines() if line.startswith("ses_")]
    out_dir = workdir / "trace"
    out_dir.mkdir(exist_ok=True)
    ids += [f.stem for f in out_dir.glob("ses_*.json") if f.stem not in ids]  # sessions that were deleted since keep their saved export
    rows = []
    for sid in ids:
        raw = out_dir / f"{sid}.json"
        # always export again: a session that was still running when it was exported last time has grown since
        e = subprocess.run([opencode, "session", "export", sid, "--standalone"], cwd=workdir, env=dict(os.environ, PWD=str(workdir)), capture_output=True, text=True)
        if e.stdout.strip():
            raw.write_text(e.stdout, encoding="utf-8")
        elif not raw.is_file():
            continue
        try:
            s = summarize(json.loads(raw.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        s["session"] = sid
        s["db_time_compacting"] = db_compacting(sid)
        s["created"] = json.loads(raw.read_text(encoding="utf-8")).get("info", {}).get("time", {}).get("created", 0)
        rows.append(s)
    rows.sort(key=lambda r: r["created"])
    (workdir / "trace.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def db_compacting(sid: str):
    """opencode's own record: the time the session was compacting, or None (read-only)."""
    try:
        import sqlite3
        c = sqlite3.connect("file:" + str(Path.home() / ".local/share/opencode/opencode.db") + "?mode=ro", uri=True)
        row = c.execute("select time_compacting from session_v2 where id = ?", (sid,)).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def handoff_view(export: dict, steps: int = 4) -> list[str]:
    """What the model saw and did around a compaction: the summary message (when there is one) and its next steps, to judge the handoff by eye."""
    msgs = export.get("messages", [])
    idx = next((i for i, m in enumerate(msgs) if re.search(r"compact|summar", str(m.get("type", "")), re.I)), None)
    asst = [i for i, m in enumerate(msgs) if m.get("type") == "assistant"]
    if idx is None:
        ins = [prompt_tokens(msgs[i]) for i in asst]
        drop = next((k for k in range(1, len(ins)) if ins[k - 1] >= 20000 and ins[k] < 0.6 * ins[k - 1]), None)
        if drop is None:
            return ["no compaction found"]
        idx = asst[drop]
    out = [f"around message {idx} of {len(msgs)}:"]
    for m in msgs[max(0, idx - 1): idx + steps + 1]:
        body = m.get("text") or _text([c for c in (m.get("content") or []) if c.get("type") == "text"])
        calls = [f"{c.get('name')} {json.dumps((c.get('state') or {}).get('input', {}), ensure_ascii=False)[:100]}" for c in (m.get("content") or []) if c.get("type") == "tool"]
        out.append(f"[{m.get('type')}] {body[:600]!r}" + ("".join("\n    -> " + x for x in calls)))
    return out


def render(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        lines.append(f"{(r['title'] or '?')[:20]:20} {r['wall_s']:5}s  calls {r['calls']:3}  ctx_max {r['context_max']:7}  out_max {r['output_max']:6}"
                     f"  think {r['reasoning_s']:4}s ({int((r['reasoning_share'] or 0) * 100)}%)  doubt {r['doubt_lines']:3}"
                     f"  tool_err {sum(r['tool_errors'].values())}"
                     + f"  out_max {r.get('output_max_pct')}% of {r.get('output_limit')}"
                     + (f"  OUTPUT-LIMIT HIT x{r['truncated']}" if r["truncated"] else "")
                     + (f"  COMPACTION markers={r['compaction_markers']} drops={len(r['context_drops'])} db={r.get('db_time_compacting')}" if r.get("compaction_markers") or r.get("context_drops") or r.get("db_time_compacting") else ""))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--handoff", action="store_true", help="print what the model saw and did around a compaction")
    a = ap.parse_args()
    rows = collect(Path(a.workdir).expanduser())
    print(render(rows) or "no sessions found")
    if a.handoff:
        for r in rows:
            raw = Path(a.workdir).expanduser() / "trace" / f"{r['session']}.json"
            print(f"\n== {r['title']} ({r['session']})")
            print("\n".join(handoff_view(json.loads(raw.read_text(encoding='utf-8')))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
