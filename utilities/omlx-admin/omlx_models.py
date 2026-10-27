#!/usr/bin/env python3
"""Load, unload and switch the two resident models (Ornith and Qwen3.8) on the oMLX server of dreammaker, on demand. Standard library only (python 3.9+).

  status                 what the server says for both models (loaded, context, max output, sampling, mode)
  load [field|batch]     make both models loaded with their settings; Qwen in the given mode (default field). Changes only what differs.
  mode field|batch       switch Qwen's mode: settings, then RESTART the oMLX server (dreammaker only), then load both models, then the client files
  restart                restart the oMLX server and load both models (launchctl kickstart, like the weekly restart job)
  exp122                 EXPERIMENT profile: unload Ornith 35B and Qwen, load the Ornith 122B alone (ctx 262144, out 65536, 0.6/0.95/20). Refuses while local.omlx.preload runs
                         (it would load the field models again; pause it first). `load field` unloads the 122B again.
  unload ornith|qwen|all unload; the preload job (local.omlx.preload, every 60 s) loads them again unless it is paused

Qwen modes (both use the sampling of the Qwen3.8-27B model card, thinking mode):
  field  context 262144, max output 65536   resident next to Ornith (speed vs deep think, not used at the same time: a long Qwen prefill makes Ornith crawl, 100 -> 2-3 tok/s); vision, deep checks
  batch  context 262144, max output 65536   deep PDF analysis that runs for hours or days (initial test value; the model card suggests more)
Ornith: temperature 0.3, top_p 0.95, top_k 20, context 262144, max output 32768.

A switch of Qwen's mode (load or mode, when Qwen is not already in that mode) restarts the oMLX server unless --no-restart is given (then only Qwen is reloaded).
The restart needs launchctl, so it only works when the script runs ON dreammaker; from crossroads use --no-restart or run the script there.
Run it on dreammaker (it then talks to 127.0.0.1) or on crossroads (it then uses the tailscale name). OMLX_URL and OMLX_KEY_FILE override. Do not switch
while a request is running: the restart drops every running request, Ornith's too. Pi and opencode files that name the two models' limits and sampling are kept in step when they exist on the machine
(a copy of each is saved next to it once per day as *.omlx-models-YYYYMMDD)."""
from __future__ import annotations

import datetime
import http.cookiejar
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

HOME = pathlib.Path.home()
DREAM_KEY = pathlib.Path("/Users/andre/inference/omlx/.api-key")
ON_DREAMMAKER = DREAM_KEY.exists()
BASE = os.environ.get("OMLX_URL") or ("http://127.0.0.1:8000" if ON_DREAMMAKER else "http://dreammaker.tail1f5a73.ts.net:8000")
KEY_FILE = pathlib.Path(os.environ.get("OMLX_KEY_FILE") or (DREAM_KEY if ON_DREAMMAKER else HOME / ".config/omlx/key"))
ORNITH = "Ornith-1.5-35B-A3B-MLX-8bit"
QWEN = "Qwen3.8-27B-4bit-MTP"
ORNITH_SET = {"max_context_window": 262144, "max_tokens": 32768, "temperature": 0.3, "top_p": 0.95, "top_k": 20}
ORNITH122 = "Ornith-1.5-122B-MLX-4bit"   # experiment profile (user 2026-10-24): alone in memory (73 GB weights), precise-coding sampling, ctx 262144, out 65536
EXP122_SET = {"max_context_window": 262144, "max_tokens": 65536, "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0}
QWEN_SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0}
QWEN_MODES = {"field": (262144, 65536), "batch": (262144, 65536)}   # user 2026-10-24: Ornith = speed, Qwen = deep think, never used together, so no 32K cap on Qwen
CLIENTS = [HOME / ".pi/agent/models.json", HOME / ".agents/skills/work-run/pi-agent/models.json", HOME / ".config/opencode/opencode.json"]
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def key() -> str:
    return KEY_FILE.read_text().strip()


def req(method: str, path: str, body=None, auth: bool = False, timeout: int = 300):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Bearer " + key()
    r = urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None, headers, method=method)
    return json.load(_opener.open(r, timeout=timeout))


def login() -> None:
    req("POST", "/admin/api/login", {"api_key": key()}, timeout=15)


def state() -> dict:
    """model id -> {loaded, settings} (settings are the admin settings; None means the server default)."""
    login()
    out = {}
    for m in req("GET", "/admin/api/models", timeout=30)["models"]:
        out[m["id"]] = {"loaded": bool(m.get("loaded")), "settings": m.get("settings") or {}}
    return out


def qwen_target(mode: str) -> dict:
    ctx, out = QWEN_MODES[mode]
    return {"max_context_window": ctx, "max_tokens": out, **QWEN_SAMPLING, "mtp_enabled": True}   # MTP head of the bluehawana model: ~2.2x decode (T24, 2026-10-24)


def differs(current: dict, target: dict) -> dict:
    return {k: v for k, v in target.items() if current.get(k) is None or abs(float(current[k]) - float(v)) > 1e-9}


def wait_loaded(mid: str, want: bool, timeout: int = 900) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if state().get(mid, {}).get("loaded") == want:
            return True
        time.sleep(3)
    return False


def load(mid: str) -> None:
    if not state()[mid]["loaded"]:
        req("POST", f"/v1/models/{mid}/load", auth=True, timeout=900)
        if not wait_loaded(mid, True):
            raise SystemExit(f"{mid} did not load")


def unload(mid: str) -> None:
    if state()[mid]["loaded"]:
        req("POST", f"/v1/models/{mid}/unload", auth=True, timeout=600)
        wait_loaded(mid, False)


def preload_running() -> bool:
    """True if the launchd job local.omlx.preload is loaded on dreammaker (read-only check; over ssh when run from crossroads)."""
    cmd = ["launchctl", "list"] if ON_DREAMMAKER else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-p", "9588", "andre@dreammaker", "launchctl list"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise SystemExit("cannot read the launchd job list: " + (r.stderr or r.stdout).strip()[:200])
    return "local.omlx.preload" in r.stdout


def restart_server() -> None:
    """Restart the oMLX server the way weekly-restart.sh does (launchctl kickstart -k), then wait until its API answers."""
    if not ON_DREAMMAKER:
        raise SystemExit("restarting the oMLX server needs launchctl, so run this script on dreammaker (or use --no-restart)")
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/local.omlx.serve"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("launchctl kickstart failed: " + (r.stderr or r.stdout).strip())
    time.sleep(3)
    for _ in range(60):
        try:
            req("GET", "/v1/models/status", auth=True, timeout=5)
            return
        except Exception:
            time.sleep(2)
    raise SystemExit("the oMLX API did not come back within 2 minutes")


def apply(mid: str, target: dict, reload_if_ctx_changes: bool) -> str:
    """Write what differs; if the context or output changed on a loaded model, reload it (the server restart replaces this when it runs)."""
    st = state()[mid]
    diff = differs(st["settings"], target)
    if diff:
        req("PUT", f"/admin/api/models/{mid}/settings", diff, timeout=30)
    if st["loaded"] and reload_if_ctx_changes and ("max_context_window" in diff or "max_tokens" in diff):
        unload(mid)
    load(mid)
    return ("changed " + ", ".join(sorted(diff))) if diff else "already as wanted"


def switch(mode: str, with_ornith: bool, restart: bool) -> None:
    """Put Qwen in `mode` (and Ornith on its settings). A real switch of Qwen's mode restarts the server first, then both models are loaded."""
    switching = bool(differs(state()[QWEN]["settings"], qwen_target(mode)))
    if switching and restart and not ON_DREAMMAKER:  # refuse before anything is written
        raise SystemExit("switching restarts the oMLX server, which needs launchctl: run this script on dreammaker (or use --no-restart to only reload Qwen)")
    if switching and restart:
        for mid, target in ((QWEN, qwen_target(mode)),) + (((ORNITH, ORNITH_SET),) if with_ornith else ()):
            diff = differs(state()[mid]["settings"], target)
            if diff:
                req("PUT", f"/admin/api/models/{mid}/settings", diff, timeout=30)
        print("restarting the oMLX server ...")
        restart_server()
        login()
        print("ornith:", apply(ORNITH, ORNITH_SET, False), "(loaded again after the server restart)")  # a restart unloads both; both are loaded again whichever command ran
        print("qwen:", apply(QWEN, qwen_target(mode), False), f"(mode {mode}, after the server restart)")
        return
    if with_ornith:
        print("ornith:", apply(ORNITH, ORNITH_SET, False))
    print("qwen:", apply(QWEN, qwen_target(mode), True), f"(mode {mode}" + ("" if switching else ", no switch needed") + ")")


def edit_clients(mode: str) -> list:
    ctx, out = QWEN_MODES[mode]
    stamp = datetime.date.today().strftime("%Y%m%d")
    done = []
    for p in CLIENTS:
        if not p.is_file():
            continue
        raw = p.read_text(encoding="utf-8")
        d = json.loads(raw)
        touched = False
        if "providers" in d:  # pi
            for prov in d["providers"].values():
                for m in prov.get("models", []):
                    if m.get("id") == QWEN:
                        m.update(contextWindow=ctx, maxTokens=out, samplingParams=dict(QWEN_SAMPLING)); touched = True
                    elif m.get("id") == ORNITH:
                        m.update(contextWindow=ORNITH_SET["max_context_window"], maxTokens=ORNITH_SET["max_tokens"], samplingParams={k: ORNITH_SET[k] for k in ("temperature", "top_p", "top_k")}); touched = True
        elif QWEN in d.get("provider", {}).get("omlx", {}).get("models", {}):  # opencode
            ms = d["provider"]["omlx"]["models"]
            ms[QWEN]["limit"] = {"context": ctx, "output": out}; ms[QWEN]["options"] = dict(QWEN_SAMPLING); touched = True
            if ORNITH in ms:
                ms[ORNITH]["limit"] = {"context": ORNITH_SET["max_context_window"], "output": ORNITH_SET["max_tokens"]}
                ms[ORNITH]["options"] = {k: ORNITH_SET[k] for k in ("temperature", "top_p", "top_k")}
        if touched:
            bak = p.with_name(p.name + ".omlx-models-" + stamp)
            if not bak.exists():
                shutil.copy2(p, bak)
            p.write_text(json.dumps(d, ensure_ascii=False, indent=4 if raw.startswith('{\n    "') else 2) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
            done.append(str(p))
    return done


def mode_of(settings: dict) -> str:
    for name in QWEN_MODES:
        if not differs(settings, qwen_target(name)):
            return name
    return "custom"


def status() -> bool:
    st = state()
    ok = True
    for mid, label in ((ORNITH, "ornith"), (QWEN, "qwen")):
        s = st.get(mid)
        if s is None:
            print(f"{label}: NOT FOUND on the server"); ok = False; continue
        c = s["settings"]
        extra = f" mode={mode_of(c)}" if mid == QWEN else (" as wanted" if not differs(c, ORNITH_SET) else " differs from the wanted settings")
        print(f"{label}: {'loaded' if s['loaded'] else 'NOT loaded'} context={c.get('max_context_window')} max_output={c.get('max_tokens')} "
              f"temperature={c.get('temperature')} top_p={c.get('top_p')} top_k={c.get('top_k')}{extra}")
        ok = ok and s["loaded"]
    return ok


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--no-restart"]
    restart = "--no-restart" not in sys.argv[1:]
    cmd = args[0] if args else "status"
    arg = args[1] if len(args) > 1 else None
    print(f"server {BASE}")
    if cmd == "status":
        return 0 if status() else 1
    if cmd == "load":
        mode = arg or "field"
        if mode not in QWEN_MODES:
            raise SystemExit("mode must be field or batch")
        if state().get(ORNITH122, {}).get("loaded"):
            unload(ORNITH122)
            print("unloaded the 122B experiment model")
        switch(mode, True, restart)
        print("client files:", edit_clients(mode) or "none on this machine")
    elif cmd == "exp122":
        if ORNITH122 not in state():
            raise SystemExit(f"{ORNITH122} is not known to the server")
        if preload_running():
            raise SystemExit("local.omlx.preload is running: it would load Ornith 35B and Qwen again (about 129 GB with the 122B). Pause it first (launchctl bootout gui/$(id -u)/local.omlx.preload, through the Orca pane).")
        unload(ORNITH); unload(QWEN)
        diff = differs(state()[ORNITH122]["settings"], EXP122_SET)
        if diff:
            req("PUT", f"/admin/api/models/{ORNITH122}/settings", diff, timeout=30)
        load(ORNITH122)
        st = state()[ORNITH122]
        ok = st["loaded"] and not differs(st["settings"], EXP122_SET)
        print("122B", "loaded" if st["loaded"] else "NOT loaded", {k: st["settings"].get(k) for k in EXP122_SET}, "READBACK OK" if ok else "READBACK MISMATCH")
        print("back to the field profile: omlx_models.py load field --no-restart, then resume the preload job (Orca pane)")
        return 0 if ok else 1
    elif cmd == "mode":
        if arg not in QWEN_MODES:
            raise SystemExit("usage: mode field|batch")
        switch(arg, False, restart)
        print("client files:", edit_clients(arg) or "none on this machine")
    elif cmd == "restart":
        restart_server(); login()
        print("ornith:", apply(ORNITH, ORNITH_SET, False))
        print("qwen:", apply(QWEN, qwen_target(mode_of(state()[QWEN]["settings"]) if mode_of(state()[QWEN]["settings"]) in QWEN_MODES else "field"), False))
    elif cmd == "unload":
        targets = {"ornith": [ORNITH], "qwen": [QWEN], "all": [QWEN, ORNITH]}.get(arg or "")
        if not targets:
            raise SystemExit("usage: unload ornith|qwen|all")
        for mid in targets:
            unload(mid)
        print("unloaded", arg, "(local.omlx.preload loads them again within about a minute unless it is paused)")
    else:
        raise SystemExit(__doc__)
    ok = status()
    if cmd in ("load", "mode"):
        mode = arg or "field"
        ok = ok and not differs(state()[QWEN]["settings"], qwen_target(mode))
        if cmd == "load":
            ok = ok and not differs(state()[ORNITH]["settings"], ORNITH_SET)
        print("READBACK OK" if ok else "READBACK MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
