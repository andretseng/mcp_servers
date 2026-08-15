# serial-mcp

MCP server that owns one serial port, decodes the drone/motor telemetry frame,
and serves on-demand **capture / plot / stats** to Claude — while always
mirroring the live byte stream to a virtual serial port so **serialplot can show
the real-time waveform at the same time**, off the same physical port.

Built for the `3sh_sen_less_foc_drv8300drge_am13e230x_lp` project's UART
serial-plot output (`HAL_updateSerialPlot`).

## Wire format (fixed)

10-byte frame, repeating:

| byte | 0 | 1 | 2–5 | 6–9 |
|------|---|---|-----|-----|
| value | `0xAA` | `0x55` | `data1` (LE float32) | `data2` (LE float32) |

No checksum / length field. The decoder locks onto frame boundaries by requiring
two consecutive `AA 55` headers and re-locks if a byte is dropped. Because there
is no checksum, a single dropped byte can produce **one** mis-decoded frame
before re-lock — inherent to the format, not a bug.

The AM13 profile uses **4,000,000 baud, 8N1** (UART1 / PA0). The server does
not choose hardware settings; a device-specific profile must pass them to
`attach()` explicitly.

## Why a broker (you can use serialplot at the same time)

A serial port hands each byte to exactly one reader. This server is the sole
owner of the real port; a background thread drains it continuously (required at
4 Mbaud) and fans bytes to:

- an in-RAM ring buffer → Claude's `capture`/`plot`/`stats`
- a PTY mirror → serialplot's live view

```
 device PA0 ──USB-UART──► serial-mcp ─┬─ ring buffer → capture/plot/stats (Claude)
 (AA55+2float, 4Mbaud)                └─ PTY mirror   → serialplot (always, live)
```

The mirror PTY is set to **raw** mode so binary float bytes (incl. 0x0A/0x0D)
pass through untranslated.

## Tools

| tool | purpose |
|------|---------|
| `get_profiles()` | list decoder profiles implemented by this server build |
| `attach(port, baud, profile, channel1, channel2)` | claim port, start draining. Settings and labels come from the device-specific profile skill. |
| `detach()` | release port, stop reader (mirror stays up, idle) |
| `status()` | attached / profile / frame-locked / buffered-samples / mirror / have-capture |
| `capture(duration_s=2.0)` | snapshot a window → summary + small preview |
| `stats()` | min/max/mean/std + dominant FFT freq of last capture |
| `plot(out_path=/tmp/serial_capture.png)` | render both channels to PNG |
| `save_csv(out_path=None)` | write last capture to CSV (full res). Default `<project-root>/csv/<timestamp>_serial_capture.csv` |
| `start_mirror(link=/tmp/ttySERIAL0)` | re-point the serialplot feed to another path |
| `stop_mirror()` | tear down the mirror explicitly |

The mirror is brought up at server startup and stays up for the whole server
lifetime, so the virtual port (default `/tmp/ttySERIAL0`) is always openable in
serialplot — it simply carries no data while detached. `start_mirror(link=...)`
only matters if you want a different path; `stop_mirror()` removes it on demand.

## Using with serialplot (verified)

The mirror lives at a fixed PTY path (default `/tmp/ttySERIAL0`, override with
`SERIAL_MCP_MIRROR`). serialplot
enumerates only real tty devices, so the mirror never appears in its Port
**dropdown** — instead **type the path into the Port field** (its combobox is
editable) and click Open. Then set the Data Format tab:

- Reader: **Custom Frame**
- Frame start: `AA 55` (hex)
- Channels: `2`, Number type: `float` (4 bytes), Endianness: **Little**
- Checksum: None

serialplot logs repeated `Operation is not supported` — this is `QSerialPort`
polling modem/line-status ioctls a PTY doesn't implement. **Cosmetic: data still
plots normally.** The only way to remove it (and get the port into the dropdown)
is a real virtual-tty driver like the `tty0tty` kernel module.

## Run

```bash
uv run serial-mcp          # stdio MCP server
uv run pytest              # tests (no hardware needed)
```

## Register with Claude Code

Add to a project's `.mcp.json` (see `mcp.snippet.json`):

```json
{
  "mcpServers": {
    "serial": {
      "command": "uv",
      "args": ["run", "--project", "/home/andre/tools/serial-mcp", "serial-mcp"]
    }
  }
}
```

## Caveats

- **XDS110 backchannel UART** is more constrained than an FTDI; 4 Mbaud may be
  flaky. First hardware check: `attach` then `status` — if `frame_locked` is
  false or `capture` returns nothing, suspect the baud rate first.
- The current build implements only the `aa55-float32x2` decoder profile. A
  device-specific profile skill must select it explicitly; other wire formats
  require a new decoder profile before attachment.
- On-demand snapshots, not a 60fps scope. For continuous live viewing, open the
  mirror PTY in serialplot.
- Ring buffer default holds ~500k samples (seconds at the ISR frame rate).
