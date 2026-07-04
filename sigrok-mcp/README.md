# sigrok-mcp

MCP server that wraps `sigrok-cli` to drive a sigrok-supported USB logic
analyzer (default driver: `fx2lafw`, e.g. the `1d50:608c` OpenMoko Fx2lafw
clone) for Claude — scan, batch capture, stats, plot, CSV export, and
protocol decode.

## Why wrap sigrok-cli instead of libsigrok bindings

libsigrok's Python bindings aren't pip-installable (they need building from
source with SWIG). `sigrok-cli` already gives a stable, scriptable interface
for everything needed here. Unlike `serial-mcp`'s continuous UART stream,
a capture is a single `sigrok-cli` subprocess call that runs to completion
and exits — there's no persistent connection to broker, so the device is
only claimed for the duration of each `capture()`/`decode()` call.

## Tools

| tool | purpose |
|------|---------|
| `scan()` | find the logic analyzer, return its `conn=` string + channels |
| `capture(samplerate="8MHz", channels="D0..D7", samples=100_000, time_ms=None, triggers=None)` | batch capture into "last capture" |
| `stats()` | per-channel duty cycle %, toggle count, estimated frequency |
| `plot(out_path=/tmp/sigrok_capture.png)` | stacked digital waveform PNG |
| `save_csv(out_path=None)` | full-resolution CSV export. Default `<project-root>/csv/<timestamp>_sigrok_capture.csv` |
| `decode(protocol, ..., pin_map=None, options=None)` | fresh capture through a sigrok protocol decoder (i2c/spi/uart/...), returns annotation lines |

`conn=` is re-resolved via `scan()` on every `capture()`/`decode()` call
since it can shift after a USB replug — never hardcoded.

## Device busy

If PulseView or another `sigrok-cli` session already has the USB interface
claimed, calls fail fast with a clear error telling you to close it first —
this server does not try to force-claim the device.

## Run

```bash
uv run sigrok-mcp          # stdio MCP server
```

## Register with Claude Code

Add to a project's `.mcp.json` (see `mcp.snippet.json`):

```json
{
  "mcpServers": {
    "sigrok": {
      "command": "uv",
      "args": ["run", "--project", "/home/andre/tools/sigrok-mcp", "sigrok-mcp"]
    }
  }
}
```

## Caveats

- On-demand batch captures, not a live scope view — `capture()` claims the
  device, runs to completion, and releases it.
- `decode()` re-captures fresh each call; it does not replay the last
  `capture()` buffer.
- Requires `sigrok-cli` on `$PATH` (Ubuntu: `apt install sigrok-cli`).
