#!/usr/bin/env python3
"""How does one loaded oMLX model behave when several sessions use it at once? Streams N parallel chat requests (each with its own unique prompt, so no prefix-cache hit
between them) and reports per-stream time to first token, per-stream decode tok/s and the aggregate tok/s; also free system memory before / lowest during / after.
Usage: omlx_concurrency.py [--model ID] [--ctx 2000,15000] [--n 1,2,4,8] [--max-tokens 200]
       omlx_concurrency.py --interference   # 2 Ornith decode streams while Qwen prefills a long prompt (does one model's prefill stall the other?)
It occupies the model for a few minutes: run it when nobody is using dreammaker. Nothing is written except stdout."""
import argparse, json, pathlib, threading, time, urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor

BASE = "http://dreammaker.tail1f5a73.ts.net:8000"
KEY = pathlib.Path.home().joinpath(".config/omlx/key").read_text().strip()
SRC = sorted(pathlib.Path("/home/andre/workspace_ccstheia/drone/escape32_msp/src").glob("*.c")) + sorted(pathlib.Path("/home/andre/workspace_ccstheia/drone/escape32_msp/src").glob("*.h"))


def prompt(i: int, tokens: int) -> str:
    """A unique prompt of about `tokens` tokens made of source code (about 3.3 characters per token), different for every stream."""
    out, n, k = [f"[nonce {uuid.uuid4()} stream {i}]\n"], 0, i
    while n < tokens * 3.3:
        f = SRC[k % len(SRC)]
        text = f.read_text(errors="replace")
        part = text[(k // len(SRC)) * 1500:][: int(tokens * 3.3) - n] or text[:1000]
        out.append(f"// ---- {f.name}\n{part}\n"); n += len(part); k += 1
    return "".join(out) + "\nExplain in detail, function by function and at least 500 words, what the code above does."


def stream(model: str, text: str, max_tokens: int, think: bool = False) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": think}}
    req = urllib.request.Request(BASE + "/v1/chat/completions", json.dumps(body).encode(), {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0, first, last, toks, ptoks, chunks = time.time(), None, None, None, None, 0
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                toks, ptoks = d["usage"].get("completion_tokens"), d["usage"].get("prompt_tokens")
            for c in d.get("choices") or []:
                dl = c.get("delta") or {}
                if dl.get("content") or dl.get("reasoning_content") or dl.get("reasoning"):
                    chunks += 1; last = time.time()
                    first = first or last
    toks = toks or chunks
    dec = (toks - 1) / (last - first) if first and last and last > first and toks > 1 else None
    return {"ttft": round((first or time.time()) - t0, 2), "dec_tps": round(dec, 1) if dec else None, "tokens": toks, "prompt": ptoks, "total": round(time.time() - t0, 1)}


def free_gb() -> float:
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request(BASE + "/admin/api/login", json.dumps({"api_key": KEY}).encode(), {"Content-Type": "application/json"}), timeout=10).read()
    d = json.load(op.open(BASE + "/admin/api/global-settings", timeout=10))
    return round(d["system"]["available_memory_bytes"] / 1e9, 1)


def run(model: str, n: int, ctx: int, max_tokens: int) -> None:
    lows, stop = [free_gb()], False
    def mon():
        while not stop:
            try: lows.append(free_gb())
            except Exception: pass
            time.sleep(2)
    th = threading.Thread(target=mon, daemon=True); th.start()
    t0 = time.time()
    with ThreadPoolExecutor(n) as ex:
        res = list(ex.map(lambda i: stream(model, prompt(i, ctx), max_tokens), range(n)))
    wall = time.time() - t0
    stop = True; th.join(3)
    tt = sum(r["tokens"] or 0 for r in res)
    dec = [r["dec_tps"] for r in res if r["dec_tps"]]
    print(f"ctx~{ctx:6d} n={n}: prompt_tok {res[0]['prompt']}  ttft {min(r['ttft'] for r in res):5.1f}-{max(r['ttft'] for r in res):5.1f}s  "
          f"per-stream decode {min(dec):5.1f}-{max(dec):5.1f} tok/s  aggregate {tt / wall:6.1f} tok/s  wall {wall:5.1f}s  free RAM {lows[0]} -> low {min(lows)} GB", flush=True)


def interference(ornith: str, qwen: str, max_tokens: int) -> None:
    out = {}
    def q():
        out["qwen"] = stream(qwen, prompt(99, 30000), 20)
    base = [stream(ornith, prompt(i, 2000), 300) for i in range(1)]
    print(f"baseline  : 1 Ornith stream alone, ctx~2000: decode {base[0]['dec_tps']} tok/s, ttft {base[0]['ttft']}s", flush=True)
    t = threading.Thread(target=q); t.start(); time.sleep(4)
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda i: stream(ornith, prompt(i + 10, 2000), 300), range(2)))
    t.join()
    print(f"during    : 2 Ornith streams while Qwen prefills ~30K tokens: decode {[r['dec_tps'] for r in res]} tok/s, ttft {[r['ttft'] for r in res]}s; "
          f"Qwen request finished in {out['qwen']['total']}s (prompt {out['qwen']['prompt']} tokens)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Ornith-1.5-35B-A3B-MLX-8bit")
    ap.add_argument("--qwen", default="Qwen3.8-27B-4bit-MTP")
    ap.add_argument("--ctx", default="2000,15000")
    ap.add_argument("--n", default="1,2,4,8")
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--interference", action="store_true")
    a = ap.parse_args()
    if a.interference:
        interference(a.model, a.qwen, a.max_tokens)
    else:
        for c in [int(x) for x in a.ctx.split(",")]:
            for n in [int(x) for x in a.n.split(",")]:
                run(a.model, n, c, a.max_tokens); time.sleep(5)
