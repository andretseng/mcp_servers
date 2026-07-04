"""FastMCP server exposing a sigrok-cli-driven USB logic analyzer to Claude.

Workflow:
    scan()       -> confirm the fx2lafw device is present, get its conn= string
    capture(...) -> run a batch sigrok-cli capture into "last capture"
    stats() / plot() / save_csv() -> analyze / render / export the last capture
    decode(protocol, ...) -> fresh capture piped through a sigrok protocol decoder

Unlike serial-mcp there is no persistent connection to broker: sigrok-cli only
claims the USB device for the duration of each capture/decode subprocess call,
so there's no reader thread to own between calls. If PulseView (or another
sigrok-cli instance) already has the device open, sigrok-cli fails with
"Unable to claim USB interface" -- surfaced here as a clear RuntimeError.
"""

import os
import re
import subprocess
import tempfile
import time

import matplotlib

matplotlib.use("Agg")  # headless: render PNGs, never open a window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("sigrok-mcp")

DRIVER = os.environ.get("SIGROK_MCP_DRIVER", "fx2lafw")
DEFAULT_CHANNELS = "D0,D1,D2,D3,D4,D5,D6,D7"
DEFAULT_SAMPLERATE = "8MHz"
MAX_SAMPLES = 10_000_000
MAX_TIME_MS = 60_000

_last: dict | None = None  # {"t": np, "channels": [...], "rate": float, "data": {ch: np.bool_[]}}

_SCAN_RE = re.compile(r"conn=(\S+).*with (\d+) channels:\s*(.+)")
_RATE_RE = re.compile(r"Samplerate:\s*([\d.]+)\s*(\w*)Hz")
_RATE_MULT = {"": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(["sigrok-cli", *args], capture_output=True, text=True, timeout=timeout)


def _check_busy(stderr: str) -> None:
    if "Unable to claim USB interface" in stderr:
        raise RuntimeError(
            "device busy: sigrok-cli could not claim the USB interface -- close "
            "PulseView (or any other sigrok-cli session) holding it and retry."
        )


@mcp.tool()
def scan() -> dict:
    """Scan for the logic analyzer and return its driver, conn= string, and channels."""
    proc = _run(["--scan"])
    _check_busy(proc.stderr)
    for line in proc.stdout.splitlines():
        if not line.strip().startswith(DRIVER):
            continue
        m = _SCAN_RE.search(line)
        if m:
            conn, _nchan, chans = m.groups()
            return {"driver": DRIVER, "conn": conn, "channels": chans.split()}
    raise RuntimeError(
        f"{DRIVER} not found (unplugged?). sigrok-cli --scan output:\n{proc.stdout}{proc.stderr}"
    )


def _resolve_conn() -> str:
    return scan()["conn"]


def _parse_rate(line: str) -> float | None:
    m = _RATE_RE.search(line)
    if not m:
        return None
    value, prefix = m.groups()
    return float(value) * _RATE_MULT.get(prefix, 1.0)


def _parse_csv(path: str, chan_list: list[str]) -> tuple[float, dict[str, np.ndarray]]:
    rate_hz = None
    header_seen = False
    cols: dict[str, list[int]] = {ch: [] for ch in chan_list}
    with open(path, newline="") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith(";"):
                rate = _parse_rate(line)
                if rate is not None:
                    rate_hz = rate
                continue
            if not header_seen:
                header_seen = True  # the "logic,logic,..." type row
                continue
            if not line:
                continue
            vals = line.split(",")
            for ch, v in zip(chan_list, vals):
                cols[ch].append(int(v))
    if rate_hz is None:
        raise RuntimeError("could not parse samplerate from sigrok-cli CSV header")
    return rate_hz, {ch: np.array(v, dtype=bool) for ch, v in cols.items()}


def _build_capture_args(conn: str, samplerate: str, channels: str,
                         samples: int | None, time_ms: float | None,
                         triggers: str | None) -> list[str]:
    if samples is not None and time_ms is not None:
        raise ValueError("specify samples or time_ms, not both")
    if samples is None and time_ms is None:
        samples = 100_000
    if samples is not None and not (0 < samples <= MAX_SAMPLES):
        raise ValueError(f"samples must be in (0, {MAX_SAMPLES}]")
    if time_ms is not None and not (0 < time_ms <= MAX_TIME_MS):
        raise ValueError(f"time_ms must be in (0, {MAX_TIME_MS}]")

    args = ["-d", f"{DRIVER}:conn={conn}", "-C", channels, "--config", f"samplerate={samplerate}"]
    if time_ms is not None:
        args += ["--time", str(int(time_ms))]
    else:
        args += ["--samples", str(samples)]
    if triggers:
        args += ["-t", triggers]
    return args


@mcp.tool()
def capture(samplerate: str = DEFAULT_SAMPLERATE, channels: str = DEFAULT_CHANNELS,
            samples: int | None = None, time_ms: float | None = None,
            triggers: str | None = None) -> dict:
    """Run a fresh capture on the logic analyzer into 'last capture'.

    Specify either samples (sample count, default 100_000) or time_ms
    (duration in ms) -- not both. triggers is a sigrok-cli -t spec, e.g.
    "D0=r" (trigger on D0 rising edge).
    """
    global _last
    conn = _resolve_conn()
    chan_list = [c.strip() for c in channels.split(",") if c.strip()]
    args = _build_capture_args(conn, samplerate, channels, samples, time_ms, triggers)

    fd, out_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        proc = _run(args + ["-O", "csv", "-o", out_path], timeout=max(30.0, (time_ms or 0) / 1000 + 10))
        _check_busy(proc.stderr)
        if proc.returncode != 0:
            raise RuntimeError(f"sigrok-cli capture failed: {proc.stderr.strip()}")
        rate_hz, data = _parse_csv(out_path, chan_list)
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    n = len(next(iter(data.values())))
    if n == 0:
        raise RuntimeError("capture returned zero samples")
    t = np.arange(n) / rate_hz
    _last = {"t": t, "channels": chan_list, "rate": rate_hz, "data": data}

    step = max(1, n // 20)
    return {
        "samples": n,
        "duration_s": round(n / rate_hz, 6),
        "samplerate_hz": rate_hz,
        "channels": chan_list,
        "stats": {ch: _channel_summary(data[ch], rate_hz) for ch in chan_list},
        "preview": {
            "t_s": [round(float(x), 6) for x in t[::step]],
            **{ch: [int(x) for x in data[ch][::step]] for ch in chan_list},
        },
    }


@mcp.tool()
def stats() -> dict:
    """Per-channel duty cycle %, toggle count, and estimated frequency of last capture."""
    if _last is None:
        raise RuntimeError("no capture yet; call capture() first")
    return {
        "samples": int(len(_last["t"])),
        "samplerate_hz": _last["rate"],
        **{ch: _channel_summary(_last["data"][ch], _last["rate"]) for ch in _last["channels"]},
    }


@mcp.tool()
def plot(out_path: str = "/tmp/sigrok_capture.png") -> dict:
    """Render the last capture as a stacked digital waveform PNG (one row per channel)."""
    if _last is None:
        raise RuntimeError("no capture yet; call capture() first")
    chans = _last["channels"]
    t = _last["t"]
    fig, ax = plt.subplots(figsize=(10, 1.2 * len(chans) + 1))
    for i, ch in enumerate(chans):
        y = _last["data"][ch].astype(float) + 2 * i
        ax.step(t, y, where="post", linewidth=0.8)
    ax.set_yticks([2 * i + 0.5 for i in range(len(chans))])
    ax.set_yticklabels(chans)
    ax.set_xlabel("time (s)")
    ax.set_title(f"sigrok capture — {len(t)} samples @ {_last['rate']:.0f} Hz")
    ax.grid(True, alpha=0.3, axis="x")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return {"path": out_path, "samples": int(len(t)), "channels": chans}


@mcp.tool()
def save_csv(out_path: str | None = None) -> dict:
    """Write the last capture to CSV at full resolution: time_s + one 0/1 column per channel.

    Default location is <project-root>/csv/<timestamp>_sigrok_capture.csv, where
    the project root is the server's cwd. The csv/ dir is created if missing.
    """
    if _last is None:
        raise RuntimeError("no capture yet; call capture() first")
    if out_path is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(os.getcwd(), "csv", f"{stamp}_sigrok_capture.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    chans = _last["channels"]
    rows = np.column_stack([_last["t"]] + [_last["data"][ch].astype(int) for ch in chans])
    header = "time_s," + ",".join(chans)
    np.savetxt(out_path, rows, delimiter=",", header=header, comments="", fmt="%.9g")
    return {"path": os.path.abspath(out_path), "rows": int(len(rows)), "columns": ["time_s", *chans]}


@mcp.tool()
def decode(protocol: str, samplerate: str = DEFAULT_SAMPLERATE, channels: str = DEFAULT_CHANNELS,
           samples: int | None = None, time_ms: float | None = None,
           pin_map: str | None = None, options: str | None = None) -> list[str]:
    """Run a fresh capture through a sigrok protocol decoder, return its annotation lines.

    protocol: sigrok decoder id, e.g. "i2c", "spi", "uart" (see `sigrok-cli -L`
    for the full list). pin_map/options: comma-separated k=v pairs appended to
    -P as "<protocol>:k=v,...", e.g. pin_map="scl=D0,sda=D1" for i2c, or
    options="baudrate=115200" for uart.
    """
    conn = _resolve_conn()
    args = _build_capture_args(conn, samplerate, channels, samples, time_ms, None)

    spec = protocol
    extra = ",".join(p for p in (pin_map, options) if p)
    if extra:
        spec = f"{protocol}:{extra}"
    args += ["-P", spec, "-A", protocol, "--protocol-decoder-samplenum"]

    proc = _run(args, timeout=max(30.0, (time_ms or 0) / 1000 + 10))
    _check_busy(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"sigrok-cli decode failed: {proc.stderr.strip()}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(
            f"decoder '{protocol}' produced no output (check pin_map/options); "
            f"stderr: {proc.stderr.strip()}"
        )
    return lines


def _channel_summary(x: np.ndarray, rate_hz: float) -> dict:
    n = len(x)
    high = int(np.sum(x))
    edges = np.flatnonzero(np.diff(x.astype(np.int8)) != 0)
    out = {
        "high_pct": round(100.0 * high / n, 2) if n else 0.0,
        "toggle_count": int(len(edges)),
    }
    if len(edges) >= 2:
        half_period_samples = float(np.mean(np.diff(edges)))
        if half_period_samples > 0:
            out["est_freq_hz"] = round(rate_hz / (2 * half_period_samples), 3)
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
