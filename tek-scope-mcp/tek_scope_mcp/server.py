"""FastMCP server exposing a Tektronix TDS3054B oscilloscope to Claude.

Two transport paths, chosen per Step-0 capability probe (see the vault note
"TDS3054B AI Loop Testing via MCP"):

  * Control + measurements -> VXI-11 over Ethernet via PyVISA (@py backend).
    Persistent connection (the 3000-series VXI-11 server is buggy when you
    open/close every call).

  * Screenshots -> e*Scope built-in webserver, GET http://<ip>/Image.png.
    On this unit `HARDCopy:PORT GPIB` + read_raw returns 0 bytes, so the VISA
    session screenshot path is dead; the HTTP path returns a clean 640x480 PNG.
    The webserver occasionally closes the socket early, so we retry.

IP is taken from the TEK_SCOPE_IP env var (default 192.168.33.38).
"""

import datetime as dt
import http.client
import os
import sys
import time
import urllib.request

import pyvisa
from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("tek-scope-mcp")

SCOPE_IP = os.environ.get("TEK_SCOPE_IP", "192.168.87.1")
VISA_TIMEOUT_MS = int(os.environ.get("TEK_SCOPE_TIMEOUT_MS", "10000"))

# --- persistent VXI-11 connection -----------------------------------------
_rm = pyvisa.ResourceManager("@py")
_scope = None


def _conn():
    """Return the live VISA session, opening it once and reusing it."""
    global _scope
    if _scope is None:
        s = _rm.open_resource(f"TCPIP::{SCOPE_IP}::INSTR")
        s.timeout = VISA_TIMEOUT_MS
        s.write("HEADER OFF")  # bare numeric replies, no command echo prefix
        _scope = s
    return _scope


def _drop_conn():
    """Close and forget the VISA session (e.g. after the IP changes)."""
    global _scope
    if _scope is not None:
        try:
            _scope.close()
        except Exception:  # noqa: BLE001
            pass
        _scope = None


_PNG_HEAD = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"\x49\x45\x4e\x44\xae\x42\x60\x82"


def _fetch_png(retries: int = 4, timeout: float = 15.0) -> bytes:
    """GET the e*Scope screen PNG.

    The e*Scope webserver sends a bogus oversized Content-Length and then closes
    after the real PNG, so urllib raises IncompleteRead; the partial payload is
    the complete image. We accept it as long as it has the PNG magic header and
    ends on the IEND chunk.
    """
    url = f"http://{SCOPE_IP}/Image.png"
    last = None
    for _ in range(retries):
        data = b""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = r.read()
        except http.client.IncompleteRead as e:
            data = e.partial
        except Exception as e:  # noqa: BLE001 - report after retries
            last = e
            time.sleep(0.6)
            continue
        if data[:8] == _PNG_HEAD and data.rstrip().endswith(_PNG_IEND):
            return data
        last = RuntimeError(f"incomplete/non-PNG payload ({len(data)} bytes)")
        time.sleep(0.6)
    raise RuntimeError(f"e*Scope screenshot failed after {retries} tries: {last!r}")


# --- connection / raw SCPI -------------------------------------------------
@mcp.tool()
def check_connection() -> str:
    """Self-check: return *IDN? (model / firmware). Call this first in a loop."""
    return _conn().query("*IDN?").strip()


def _sync_clock() -> str:
    """Set the scope RTC to the host clock. Raises if the scope is unreachable."""
    s = _conn()
    before = f'{s.query("DATE?").strip().strip(chr(34))} {s.query("TIME?").strip().strip(chr(34))}'
    now = dt.datetime.now()
    s.write(f'DATE "{now.strftime("%Y-%m-%d")}"')
    s.write(f'TIME "{now.strftime("%H:%M:%S")}"')
    after = f'{s.query("DATE?").strip().strip(chr(34))} {s.query("TIME?").strip().strip(chr(34))}'
    return f"scope clock: {before} -> {after}"


@mcp.tool()
def sync_clock() -> str:
    """Set the scope's date/time to this host's clock (no NTP on the scope)."""
    return _sync_clock()


@mcp.tool()
def get_scope_ip() -> str:
    """Return the IP address this server is currently talking to."""
    return SCOPE_IP


@mcp.tool()
def set_scope_ip(ip: str) -> str:
    """Point the server at a different scope IP for the rest of this session.

    Reopens the VXI-11 session and screenshot path at <ip>, then runs *IDN? to
    confirm. Use this if the scope moved; for a permanent default, set
    TEK_SCOPE_IP in the .mcp.json env block instead.
    """
    global SCOPE_IP
    SCOPE_IP = ip.strip()
    _drop_conn()
    try:
        return f"scope IP set to {SCOPE_IP}; *IDN? -> {_conn().query('*IDN?').strip()}"
    except Exception as e:  # noqa: BLE001
        return f"scope IP set to {SCOPE_IP}, but connection failed: {e!r}"


@mcp.tool()
def scpi_query(command: str) -> str:
    """Send a raw SCPI query (must contain '?') and return the reply.

    Escape hatch for anything the convenience tools don't cover, e.g.
    'CH1:SCALe?', 'TRIGger:A:LEVel?', 'ACQuire:STATE?'.
    """
    return _conn().query(command).strip()


@mcp.tool()
def scpi_write(command: str) -> str:
    """Send a raw SCPI command (no reply), e.g. 'AUTOSet EXECute', 'ACQuire:STATE RUN'.

    Returns *ESR? + ALLEV? so you can see if the scope accepted the command.
    """
    s = _conn()
    s.write(command)
    esr = s.query("*ESR?").strip()
    allev = s.query("ALLEV?").strip()
    return f"sent: {command} | ESR={esr} | ALLEV={allev}"


# --- setup -----------------------------------------------------------------
@mcp.tool()
def configure_channel(channel: int, volts_div: float, position_div: float | None = None) -> str:
    """Set a channel's vertical scale (V/div) and optionally its position (divisions)."""
    s = _conn()
    s.write(f"SELect:CH{channel} ON")
    s.write(f"CH{channel}:SCALe {volts_div}")
    if position_div is not None:
        s.write(f"CH{channel}:POSition {position_div}")
    rd = s.query(f"CH{channel}:SCALe?").strip()
    return f"CH{channel} scale set, readback {rd} V/div"


@mcp.tool()
def configure_timebase(time_div: float) -> str:
    """Set the horizontal main timebase (s/div)."""
    s = _conn()
    s.write(f"HORizontal:MAIn:SCALe {time_div}")
    return f"timebase readback {s.query('HORizontal:MAIn:SCALe?').strip()} s/div"


@mcp.tool()
def set_trigger(channel: int, level: float, slope: str = "RISe") -> str:
    """Edge trigger on CH<channel> at <level> volts, slope RISe or FALL."""
    s = _conn()
    s.write("TRIGger:A:TYPe EDGE")
    s.write(f"TRIGger:A:EDGE:SOUrce CH{channel}")
    s.write(f"TRIGger:A:EDGE:SLOpe {slope}")
    s.write(f"TRIGger:A:LEVel {level}")
    return (f"trigger CH{channel} {slope} @ {s.query('TRIGger:A:LEVel?').strip()} V, "
            f"state {s.query('TRIGger:STATE?').strip()}")


@mcp.tool()
def autoset() -> str:
    """Run AUTOSet so the scope auto-scales to the current signal."""
    s = _conn()
    s.write("AUTOSet EXECute")
    time.sleep(2.0)  # autoset needs a moment to settle
    return f"autoset done, trigger state {s.query('TRIGger:STATE?').strip()}"


@mcp.tool()
def acquire(state: str = "RUN") -> str:
    """Set acquisition state: RUN (continuous) or STOP (freeze)."""
    s = _conn()
    s.write(f"ACQuire:STATE {state}")
    return f"acquire state {s.query('ACQuire:STATE?').strip()}"


@mcp.tool()
def single_shot_acquire(timeout_s: float = 10.0) -> str:
    """Arm a single-sequence acquisition and wait for the trigger to fire.

    Sets ACQuire:STOPAfter SEQuence then ACQuire:STATE RUN, polling
    ACQuire:STATE? until it drops to 0 (acquisition complete) or timeout_s
    elapses. Leaves STOPAfter at SEQuence; call acquire("RUN") afterward to
    go back to continuous mode.
    """
    s = _conn()
    s.write("ACQuire:STOPAfter SEQuence")
    s.write("ACQuire:STATE RUN")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if s.query("ACQuire:STATE?").strip() == "0":
            return f"single-shot captured, trigger state {s.query('TRIGger:STATE?').strip()}"
        time.sleep(0.1)
    return f"timed out after {timeout_s}s waiting for trigger, ACQuire:STATE={s.query('ACQuire:STATE?').strip()}"


# --- measurements (numbers, not eyeballing the picture) --------------------
def _measure_source(channel: int | str) -> str:
    """Format a measurement source: int -> 'CH<n>', str passed through (e.g. 'MATH')."""
    return f"CH{channel}" if isinstance(channel, int) else channel


@mcp.tool()
def measure(channel: int | str, meas_type: str = "PK2pk") -> str:
    """Read a numeric measurement off a channel.

    channel: channel number (e.g. 1) or a source string like "MATH" (FFT).
    meas_type examples: PK2pk, AMPlitude, MEAN, RMS, FREQuency, PERIod,
    OVErshoot, RISe, FALL, PWIdth, NWIdth, MINImum, MAXImum.
    """
    s = _conn()
    src = _measure_source(channel)
    s.write(f"MEASUrement:IMMed:SOURCE {src}")
    s.write(f"MEASUrement:IMMed:TYPe {meas_type}")
    val = s.query("MEASUrement:IMMed:VALue?").strip()
    return f"{src} {meas_type} = {val}"


@mcp.tool()
def measure_many(channel: int | str, types: list[str]) -> dict:
    """Read several measurements off one channel/source in a single call.

    channel: channel number (e.g. 1) or a source string like "MATH" (FFT).
    Pass e.g. ["PK2pk", "FREQuency", "OVErshoot"]; returns {type: value}.
    """
    s = _conn()
    s.write(f"MEASUrement:IMMed:SOURCE {_measure_source(channel)}")
    out: dict[str, str] = {}
    for t in types:
        s.write(f"MEASUrement:IMMed:TYPe {t}")
        out[t] = s.query("MEASUrement:IMMed:VALue?").strip()
    return out


# --- cursors -----------------------------------------------------------
def _read_cursors(s) -> dict:
    """Read back current cursor mode, positions, and delta between them."""
    mode = s.query("CURSor:FUNCtion?").strip()
    if mode not in ("HBArs", "VBArs"):
        return {"function": mode}
    pos1 = s.query(f"CURSor:{mode}:POSITION1?").strip()
    pos2 = s.query(f"CURSor:{mode}:POSITION2?").strip()
    delta_cmd = "VDELTA" if mode == "HBArs" else "HDELTA"
    delta = s.query(f"CURSor:{mode}:{delta_cmd}?").strip()
    return {"function": mode, "position1": pos1, "position2": pos2, "delta": delta}


@mcp.tool()
def set_cursors(mode: str, source: str, pos1: float, pos2: float) -> dict:
    """Place cursors and return their readback + delta.

    mode: "HBArs" (horizontal bars, measure voltage) or "VBArs" (vertical
    bars, measure time). source: e.g. "CH1", "MATH". pos1/pos2: cursor
    positions in volts (HBArs) or seconds (VBArs).
    """
    s = _conn()
    s.write(f"CURSor:FUNCtion {mode}")
    s.write(f"CURSor:SOUrce {source}")
    s.write(f"CURSor:{mode}:POSITION1 {pos1}")
    s.write(f"CURSor:{mode}:POSITION2 {pos2}")
    return _read_cursors(s)


@mcp.tool()
def read_cursors() -> dict:
    """Read back current cursor mode, positions, and delta between them."""
    return _read_cursors(_conn())


# --- FFT -----------------------------------------------------------------
@mcp.tool()
def configure_fft(channel: int, window: str = "HANNing", vertical_scale: str = "DB") -> str:
    """Set the MATH channel to FFT(CH<channel>) and turn it on.

    window: RECTangular, HAMMing, HANNing, or BLACkmanharris.
    vertical_scale: LINEAr or DB.
    After this, measure(channel="MATH", meas_type=...) reads off the FFT trace.
    """
    s = _conn()
    s.write(f'MATH:DEFine "FFT(CH{channel})"')
    s.write(f"MATH:FFT:WINdow {window}")
    s.write(f"MATH:FFT:VERTicalscale {vertical_scale}")
    s.write("SELect:MATH ON")
    return (f"FFT(CH{channel}) window={s.query('MATH:FFT:WINdow?').strip()} "
            f"vscale={s.query('MATH:FFT:VERTicalscale?').strip()}")


# --- screenshots (via e*Scope HTTP) ----------------------------------------
@mcp.tool()
def get_screenshot() -> Image:
    """Capture the scope screen and return the PNG inline for visual analysis.

    Uses the e*Scope webserver (http://<ip>/Image.png) -- the VISA-session
    hardcopy path is dead on this unit. Returns the live 640x480 display.
    """
    return Image(data=_fetch_png(), format="png")


@mcp.tool()
def save_screenshot(out_path: str | None = None) -> dict:
    """Save the scope screen PNG to disk for the test-log record.

    Default: <cwd>/scope_shots/<timestamp>.png, where cwd is the project dir
    Claude Code launched in (e.g. .../drone or .../motor). Returns the path.
    """
    data = _fetch_png()
    if out_path is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(os.getcwd(), "scope_shots", f"{stamp}_scope.png")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return {"path": os.path.abspath(out_path), "bytes": len(data)}


def main() -> None:
    # Sync the scope RTC to this host on startup (it has no NTP and drifts far
    # off). Best-effort: if the scope is unreachable, log and serve anyway.
    try:
        print(f"[tek-scope] {_sync_clock()}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[tek-scope] clock sync skipped ({SCOPE_IP} unreachable): {e!r}",
              file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
