---
name: am13-serial
description: Supply the AM13E230X motor/drone UART telemetry profile for serial-op. Use when the target firmware emits the AM13 AA55 float32 telemetry stream; this skill owns the port, baud, framing, channel mapping, and bandwidth limits.
---

# AM13 Serial Profile

This skill owns the AM13 device-specific serial contract. Use it together with `serial-op`; do not put these settings into the shared generic operation skill.

## Contract

- Decoder profile: `aa55-float32x2`.
- Serial format: 4,000,000 baud, 8 data bits, no parity, 1 stop bit (8N1).
- Physical signal: the project UART serial-plot output, normally UART1/PA0 through the configured USB-UART path.
- Frame: `AA 55`, followed by two little-endian IEEE-754 float32 values; 10 bytes total; no checksum.
- Current validated default channel labels: `focDacDelta_rad`, `angleObs_rad`.
- Nominal decoded rate: about 25 ksample/s at 4 Mbaud.
- Measurement scope: slow telemetry and steady-state behavior. Do not use this profile for sub-millisecond electrical transients; request Logic2 or oscilloscope capture.

The physical port path is not fixed by this skill. Resolve it from the active bench/project configuration and work order. Do not guess `/dev/ttyACM*` or silently substitute another UART. If the work order explicitly repoints the firmware output pointers, its channel labels may replace the defaults while retaining this same frame profile.

## Preconditions

Before calling the serial MCP:

1. Read the active project's hardware-safety instructions.
2. Confirm firmware/SysConfig still emits this exact frame and that the two channel pointers have the stated assignments.
3. Confirm the target, USB-UART path, and port path from the work order.
4. Confirm that no other process owns the physical port.
5. Apply `motor-safety` and `realtime-safety` for any live motor or controller operation.

If any precondition is missing or differs, return `NEED-SPEC`; do not alter baud, channel labels, or framing to make lock succeed.

## Handoff to serial-op

Pass this explicit contract to `serial-op`:

```text
profile=aa55-float32x2
port=<resolved physical port>
baud=4000000
format=8N1
channel1=<default focDacDelta_rad or explicit runtime channel 1>
channel2=<default angleObs_rad or explicit runtime channel 2>
```

`serial-op` must first call `mcp__serial__get_profiles` and confirm `aa55-float32x2` is implemented, then call `mcp__serial__attach` with the explicit profile, baud, port, and labels. It must verify `attached`, `profile`, `frame_locked`, and `buffered_samples` before capture.

For a step or ramp, attach and validate before the authorized control event, then use `capture` with the work-order lookback. Save the raw CSV before requesting stats or a plot. Follow the requested repetition count exactly.

## Failure handling

- No lock or no data: report the observed port, baud, profile, and status; do not try another baud or profile without a new work order.
- A disconnected port: stop safely, release the session, and follow the work-order reconnect limit.
- A hardware fault or real-time interruption: apply the safety skill's stop/recovery procedure and report `ABORT`.
