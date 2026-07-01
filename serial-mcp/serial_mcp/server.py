"""FastMCP server exposing the serial broker to Claude.

Workflow:
    attach(port)            -> claim the port, start draining, expose the PTY mirror
    capture(duration_s)     -> snapshot a time window into "last capture"
    stats() / plot()        -> analyze / render the last capture
    detach()                -> release the port

The serialplot mirror (DEFAULT_MIRROR_LINK) is brought up at startup and stays up
for the whole server lifetime, so the virtual port is always openable -- it simply
carries no data while detached. start_mirror(link=...) re-points it to a different
path; stop_mirror() tears it down explicitly.
"""

import atexit
import os
import time

import matplotlib

matplotlib.use("Agg")  # headless: render PNGs, never open a window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from .broker import Broker  # noqa: E402
from .parser import DEFAULT_CHANNELS  # noqa: E402

mcp = FastMCP("serial-mcp")
_broker = Broker()

# Mutable session state
_labels: tuple[str, str] = DEFAULT_CHANNELS
_last: dict | None = None  # {"t": np, "ch1": np, "ch2": np, "rate": float}

DEFAULT_BAUD = 4_000_000
# Neutral, project-agnostic default (this tool is shared across motor + drone).
# Override per call via start_mirror(link=...), or set a per-project default in
# .mcp.json with "env": {"SERIAL_MCP_MIRROR": "/tmp/ttyMOTOR0"}.
DEFAULT_MIRROR_LINK = os.environ.get("SERIAL_MCP_MIRROR", "/tmp/ttySERIAL0")


def _require_attached() -> None:
    if not _broker.attached:
        raise RuntimeError("not attached to a serial port; call attach(port) first")


@mcp.tool()
def attach(port: str, baud: int = DEFAULT_BAUD,
           channel1: str = DEFAULT_CHANNELS[0], channel2: str = DEFAULT_CHANNELS[1]) -> dict:
    """Claim a serial port and start draining it continuously.

    channel1/channel2 are just labels for plots/stats; set them to whatever the
    firmware currently points pDacCtrl1/2->pDacOutAddr at this session.
    """
    global _labels
    _broker.attach(port, baud)
    _labels = (channel1, channel2)
    # Always expose the serialplot mirror while attached, so the virtual port is
    # available the moment we're connected (no separate start_mirror() needed).
    link = _broker.start_mirror(DEFAULT_MIRROR_LINK)
    return {"attached": True, "port": port, "baud": baud,
            "channels": list(_labels), "mirror_link": link}


@mcp.tool()
def detach() -> dict:
    """Release the serial port and stop the reader/mirror."""
    _broker.detach()
    return {"attached": False}


@mcp.tool()
def status() -> dict:
    """Current attach/lock/mirror state and buffered sample count."""
    return {
        "attached": _broker.attached,
        "frame_locked": _broker.locked,
        "channels": list(_labels),
        "mirror_link": _broker._mirror_link,
        "have_capture": _last is not None,
    }


@mcp.tool()
def capture(duration_s: float = 2.0, lookback_s: float = 0.0) -> dict:
    """Record a time window of decoded frames into the 'last capture'.

    The reader thread drains the port continuously from attach() onward, so
    frames from before this call are already sitting in the ring buffer.
    Set lookback_s > 0 to pull that pre-call history instead of only waiting
    forward -- e.g. issue a step write, THEN call capture(duration_s=0.5,
    lookback_s=0.5) and the transient is already buffered, regardless of any
    MCP round-trip latency between the step and this call.

    Returns a summary + a small downsampled preview (NOT the full arrays, to
    keep the context small). Use stats()/plot() for detail.
    """
    global _last
    _require_attached()
    if duration_s <= 0 or duration_s > 60:
        raise ValueError("duration_s must be in (0, 60]")
    if lookback_s < 0 or lookback_s > duration_s:
        raise ValueError("lookback_s must be in [0, duration_s]")

    t0 = _broker.now() - lookback_s
    remaining = duration_s - lookback_s
    if remaining > 0:
        time.sleep(remaining)
    rows = _broker.samples_since(t0)
    if not rows:
        raise RuntimeError(
            "no frames captured. Frame locked: "
            f"{_broker.locked}. Check the board is powered, emitting, and the "
            "baud matches (XDS110 at 4 Mbaud is the usual suspect)."
        )

    t = np.array([r[0] for r in rows]) - rows[0][0]
    ch1 = np.array([r[1] for r in rows], dtype=float)
    ch2 = np.array([r[2] for r in rows], dtype=float)
    span = float(t[-1]) if len(t) > 1 else duration_s
    rate = (len(t) - 1) / span if span > 0 else 0.0
    _last = {"t": t, "ch1": ch1, "ch2": ch2, "rate": rate}

    step = max(1, len(t) // 20)
    return {
        "samples": len(rows),
        "duration_s": round(span, 4),
        "est_rate_hz": round(rate, 1),
        "channels": list(_labels),
        _labels[0]: _channel_summary(ch1),
        _labels[1]: _channel_summary(ch2),
        "preview": {
            "t_s": [round(float(x), 5) for x in t[::step]],
            _labels[0]: [round(float(x), 5) for x in ch1[::step]],
            _labels[1]: [round(float(x), 5) for x in ch2[::step]],
        },
    }


@mcp.tool()
def stats() -> dict:
    """Per-channel min/max/mean/std and dominant FFT frequency of last capture."""
    if _last is None:
        raise RuntimeError("no capture yet; call capture() first")
    return {
        "samples": int(len(_last["t"])),
        "est_rate_hz": round(_last["rate"], 1),
        _labels[0]: _channel_summary(_last["ch1"], _last["rate"]),
        _labels[1]: _channel_summary(_last["ch2"], _last["rate"]),
    }


@mcp.tool()
def plot(out_path: str = "/tmp/serial_capture.png") -> dict:
    """Render the last capture to a PNG (both channels vs time) and return its path."""
    if _last is None:
        raise RuntimeError("no capture yet; call capture() first")
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
    for ax, key, label in zip(axes, ("ch1", "ch2"), _labels):
        ax.plot(_last["t"], _last[key], linewidth=0.8)
        ax.set_ylabel(label)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel("time (s)")
    fig.suptitle(f"serial capture — {len(_last['t'])} samples @ {_last['rate']:.0f} Hz")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return {"path": out_path, "samples": int(len(_last["t"]))}


@mcp.tool()
def save_csv(out_path: str | None = None) -> dict:
    """Write the last capture to CSV at full resolution: time_s + both channels.

    Default location is <project-root>/csv/<timestamp>_serial_capture.csv, where
    the project root is the server's cwd (the directory Claude Code launched in,
    e.g. .../drone or .../motor). The csv/ dir is created if missing. Pass an
    explicit out_path to override.
    """
    if _last is None:
        raise RuntimeError("no capture yet; call capture() first")
    if out_path is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(os.getcwd(), "csv", f"{stamp}_serial_capture.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    rows = np.column_stack([_last["t"], _last["ch1"], _last["ch2"]])
    header = f"time_s,{_labels[0]},{_labels[1]}"
    np.savetxt(out_path, rows, delimiter=",", header=header, comments="", fmt="%.9g")
    return {"path": os.path.abspath(out_path), "rows": int(len(rows)),
            "columns": ["time_s", _labels[0], _labels[1]]}


@mcp.tool()
def start_mirror(link: str = DEFAULT_MIRROR_LINK) -> dict:
    """Opt-in: expose the live stream on a virtual serial port for serialplot.

    Point serialplot at the returned 'link' path (any baud; it's a PTY). The
    real port stays owned by this server, so capture() and the live view run
    off the same stream simultaneously.
    """
    _require_attached()
    path = _broker.start_mirror(link)
    return {"mirror_link": path, "hint": f"open '{path}' in serialplot (AA55 + 2 LE float32)"}


@mcp.tool()
def stop_mirror() -> dict:
    """Tear down the serialplot mirror (capture continues)."""
    _broker.stop_mirror()
    return {"mirror_link": None}


def _channel_summary(x: np.ndarray, rate: float | None = None) -> dict:
    out = {
        "min": round(float(np.min(x)), 6),
        "max": round(float(np.max(x)), 6),
        "mean": round(float(np.mean(x)), 6),
        "std": round(float(np.std(x)), 6),
    }
    if rate and rate > 0 and len(x) >= 8:
        out["dominant_hz"] = round(_dominant_freq(x, rate), 3)
    return out


def _dominant_freq(x: np.ndarray, rate: float) -> float:
    x = x - np.mean(x)  # drop DC so it doesn't dominate the peak
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), d=1.0 / rate)
    return float(freqs[int(np.argmax(spec))])


def main() -> None:
    # Bring the virtual port up immediately and keep it for the whole process
    # lifetime, so /tmp/ttySERIAL0 exists as long as serial-mcp is running --
    # whether or not we're currently attached to a physical port.
    _broker.start_mirror(DEFAULT_MIRROR_LINK)
    atexit.register(_broker.stop_mirror)
    mcp.run()


if __name__ == "__main__":
    main()
