#!/usr/bin/env python3
"""Readable live view of a pi work session: `tail -n +1 -F <workdir>/pi-sessions/*.jsonl | python3 pilive.py`.
One line per tool call (and a short line for the model's thinking and text), errors marked."""
import json
import sys
import time

T0 = time.time()


def short(x, n=160):
    x = " ".join(str(x).split())
    return x if len(x) <= n else x[:n] + "…"


for line in sys.stdin:
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("type") != "message":
        continue
    m = r["message"]
    role = m.get("role")
    stamp = time.strftime("%H:%M:%S")
    if role == "assistant":
        for c in m.get("content", []):
            if c.get("type") == "thinking" and c.get("thinking"):
                print(f"{stamp} think  {short(c['thinking'], 200)}", flush=True)
            elif c.get("type") == "text" and c.get("text"):
                print(f"{stamp} say    {short(c['text'], 200)}", flush=True)
            elif c.get("type") == "toolCall":
                a = c.get("arguments") or {}
                print(f"{stamp} call   {c.get('name')} {short(a.get('command') or a.get('path') or json.dumps(a, ensure_ascii=False))}", flush=True)
        u = m.get("usage") or {}
        print(f"{stamp}        ctx {u.get('input', 0) + u.get('cacheRead', 0)}  out {u.get('output', 0)}  {m.get('stopReason')}", flush=True)
    elif role == "toolResult" and m.get("isError"):
        txt = "".join(c.get("text", "") for c in m.get("content", []) if c.get("type") == "text")
        print(f"{stamp} ERROR  {short(txt, 200)}", flush=True)
