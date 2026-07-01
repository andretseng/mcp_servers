# tek-scope-mcp

MCP server that controls a **Tektronix TDS3054B** oscilloscope over Ethernet so
Claude can run a setup → capture → measure → analyze loop.

## Setting the scope IP

Two ways, in priority order:

1. **Per-session, no restart** — call the `set_scope_ip("192.168.x.y")` tool (and
   `get_scope_ip` to check). Reopens the connection immediately. Use when the scope moves.
2. **Persistent default** — edit the `env.TEK_SCOPE_IP` value in each project's
   `.mcp.json` (already present, set to `192.168.33.38`). Takes effect on next session.

Falls back to `192.168.87.1` if neither is set.

## Two transport paths (per Step-0 capability probe)

| Need | Path | Why |
|---|---|---|
| Setup + measurements | **VXI-11** via PyVISA `@py`, persistent session | `*IDN?`, `MEASUrement`, config all work over the VISA session |
| Screenshots | **e*Scope HTTP** `GET http://<ip>/Image.png` | `HARDCopy:PORT GPIB` + read returns **0 bytes** on this unit; the webserver returns a clean 640×480 PNG (retried — it sometimes drops the socket early) |

## Tools

- `check_connection` — `*IDN?` self-check (call first)
- `sync_clock` — set the scope RTC to this host's clock (also runs **automatically on
  server startup**, best-effort; the scope has no NTP and drifts years off)
- `get_scope_ip` / `set_scope_ip(ip)` — read / change target IP at runtime
- `scpi_query` / `scpi_write` — raw SCPI escape hatch
- `configure_channel(channel, volts_div, position_div?)`
- `configure_timebase(time_div)`
- `set_trigger(channel, level, slope)`
- `autoset`, `acquire(RUN|STOP)`
- `single_shot_acquire(timeout_s?)` — arm single-sequence, block until triggered or timeout
- `measure(channel, meas_type)` — single numeric measurement; channel can be an int or a source string like `"MATH"`
- `measure_many(channel, [types])` — several at once, same channel rule
- `set_cursors(mode, source, pos1, pos2)` / `read_cursors()` — HBArs (voltage) or VBArs (time) cursor pair + delta
- `configure_fft(channel, window?, vertical_scale?)` — set MATH channel to FFT(CH<n>), turn it on
- `get_screenshot` — returns the PNG **inline** for visual analysis
- `save_screenshot(out_path?)` — writes PNG to `<cwd>/scope_shots/` for the log

## Register (already added to drone + motor `.mcp.json`)

```json
{
  "mcpServers": {
    "tek-scope": {
      "command": "uv",
      "args": ["run", "--project", "/home/andre/tools/tek-scope-mcp", "tek-scope-mcp"]
    }
  }
}
```

## Background

Design + risk analysis: vault note *TDS3054B AI Loop Testing via MCP*.
