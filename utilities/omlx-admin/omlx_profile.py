#!/usr/bin/env python3
"""Switch between the two resident-model profiles of dreammaker's oMLX (user 2026-10-24). Standard library only (python 3.9+). Run it on crossroads or on dreammaker.

  status   what is loaded, the preload job, and the default model of pi and opencode
  field    FIELD profile: Ornith 35B (speed, ctx 262144 / out 32768 / 0.3) + Qwen3.8 MTP (deep think, vision, ctx 262144 / out 65536, MTP on); preload job running;
           pi defaultModel and opencode model = Ornith 35B
  exp122   EXPERIMENT profile: Ornith 1.5 122B 4-bit alone (ctx 262144 / out 65536, precise coding 0.6 / 0.95 / 20); preload job paused (it would load the field models again);
           pi defaultModel and opencode model = the 122B

Order matters and is kept: exp122 = pause preload -> unload the field models -> load the 122B; field = unload the 122B -> load the field models -> resume preload.
The model work is done by omlx_models.py (same folder); this script adds the launchd preload job and the pi / opencode defaults. Do not switch while a request is running.
launchctl runs directly on dreammaker, over ssh (BatchMode, port 9588) from crossroads, or through the Orca pane when OMLX_PANE_SEND (orca_send.sh path) and OMLX_PANE_HANDLE are set."""
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import omlx_models as m  # noqa: E402

HOME = pathlib.Path.home()
PLIST = "/Users/andre/Library/LaunchAgents/local.omlx.preload.plist"
PI_SETTINGS = HOME / ".pi/agent/settings.json"
OPENCODE = HOME / ".config/opencode/opencode.json"


def launchctl(args: str) -> str:
    """Run `launchctl <args>` on dreammaker; $(id -u) is expanded there."""
    cmd = f"launchctl {args}"
    send, handle = os.environ.get("OMLX_PANE_SEND"), os.environ.get("OMLX_PANE_HANDLE")
    if send and handle:
        r = subprocess.run([send, handle, cmd, "60"], capture_output=True, text=True, timeout=120)
    elif m.ON_DREAMMAKER:
        r = subprocess.run(["/bin/zsh", "-c", cmd], capture_output=True, text=True, timeout=60)
    else:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-p", "9588", "andre@dreammaker", cmd], capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).replace("\n", " ").strip()


def preload_listed() -> bool:
    return "local.omlx.preload" in launchctl("list | grep local.omlx.preload; true")   # grep on the remote side: the pane only returns the last screen lines


def pause_preload() -> None:
    if preload_listed():
        print("preload: bootout ->", launchctl("bootout gui/$(id -u)/local.omlx.preload")[:120] or "ok")
    if preload_listed():
        raise SystemExit("the preload job is still listed; stop here (it would load the field models again)")
    print("preload job paused")


def resume_preload() -> None:
    if not preload_listed():
        print("preload: bootstrap ->", launchctl(f"bootstrap gui/$(id -u) {PLIST}")[:120] or "ok")
    if not preload_listed():
        raise SystemExit("the preload job is not listed after bootstrap")
    print("preload job running")


def set_defaults(model: str) -> None:
    """pi defaultModel and opencode model (opencode keeps its small_model)."""
    if PI_SETTINGS.is_file():
        d = json.loads(PI_SETTINGS.read_text(encoding="utf-8")); d["defaultModel"] = model
        PI_SETTINGS.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if OPENCODE.is_file():
        raw = OPENCODE.read_text(encoding="utf-8"); d = json.loads(raw); d["model"] = "omlx/" + model
        OPENCODE.write_text(json.dumps(d, ensure_ascii=False, indent=4 if raw.startswith('{\n    "') else 2) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    print(f"pi defaultModel / opencode model -> {model}")


def models_py(*args: str) -> int:
    return subprocess.run([sys.executable, str(HERE / "omlx_models.py"), *args]).returncode


def status() -> int:
    st = m.state()
    print("loaded:", [i for i in (m.ORNITH, m.QWEN, m.ORNITH122) if st.get(i, {}).get("loaded")])
    print("preload job:", "running" if preload_listed() else "paused")
    pi = json.loads(PI_SETTINGS.read_text()).get("defaultModel") if PI_SETTINGS.is_file() else None
    oc = json.loads(OPENCODE.read_text()).get("model") if OPENCODE.is_file() else None
    print(f"pi defaultModel: {pi}; opencode model: {oc}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in ("status", "field", "exp122"):
        raise SystemExit(__doc__)
    print(f"server {m.BASE}")
    if cmd == "status":
        return status()
    m.login()
    if cmd == "exp122":
        pause_preload()
        rc = models_py("exp122")
        if rc == 0:
            set_defaults(m.ORNITH122)
        status()
        return rc
    rc = models_py("load", "field", "--no-restart")      # unloads the 122B first
    if rc == 0:
        resume_preload()
        set_defaults(m.ORNITH)
    status()
    return rc


if __name__ == "__main__":
    sys.exit(main())
