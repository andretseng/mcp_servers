---
name: serial-op
description: Operate the shared standalone serial-mcp server using an explicit device-specific serial contract. Use for attach, decoder validation, capture, CSV/plot output, mirror control, and release. Do not use this skill to choose baud, port, framing, channel mapping, or hardware configuration.
---

# Serial Ops

This is the transport-operation layer for the shared `serial` MCP server. A device/project-specific profile skill must supply the serial contract; this skill validates that contract and executes the capture workflow.

Do not replace MCP calls with shell access to `/dev/tty*`. Do not infer or change hardware settings.

## Scope and companions

- Use standalone `mcp__serial__*` tools: `status`, `get_profiles`, `attach`, `capture`, `stats`, `plot`, `save_csv`, `detach`, and mirror control.
- For CCS serial-console commands, use `ccs-operate` instead.
- For live motor, inverter, converter, or real-time-controller work, also apply `motor-safety` and `realtime-safety` plus the applicable debugger/instrument skill.
- If the required serial MCP is unavailable, return `TOOLING_BLOCKED`; a skill cannot create missing tools.

## Required serial contract

Before the first hardware MCP call, the caller must provide a device-specific contract containing:

- profile name/version and proof that the running server implements it;
- physical port;
- baud and serial format;
- framing, decoder, checksum/validation rules, and channel count;
- channel names and firmware assignment;
- nominal sample rate and trustworthy bandwidth;
- capture duration, lookback, repetition, and output rules; and
- hardware safety envelope and stop conditions.

If any item is missing, return `NEED-SPEC`. This skill must not supply defaults for them. The profile skill owns hardware configuration; `serial-op` only passes the explicit values to the MCP and checks the returned state.

## Capture workflow

1. Confirm project and hardware preconditions using the profile skill and the project safety instructions. Confirm the target is emitting the selected profile and no other process owns the physical port.

2. Call `mcp__serial__status`. If an old session is attached, follow the profile/work-order reconnect rule; do not silently take it over.

3. Call `mcp__serial__get_profiles` and confirm the contract's profile is implemented by the running server. If it is absent, return `TOOLING_BLOCKED`; do not attach and reinterpret bytes.

4. Call `mcp__serial__attach` with the contract's explicit `port`, `baud`, `profile`, `channel1`, and `channel2`. The server must remain the sole physical-port reader. The PTY mirror is optional and is not evidence storage.

5. Call `mcp__serial__status` after data should have arrived. Require `attached: true`, the expected decoder profile, `frame_locked: true`, and a nonzero `buffered_samples` when those fields are provided by the active server. If lock is false or no samples arrive, stop and report the observed state.

6. For an event-driven test, attach and validate first, issue the explicitly authorized event through the appropriate control tool, then call `mcp__serial__capture(duration_s=<window>, lookback_s=<pre-event>)`. Preserve the specified repetition count. `lookback_s` protects against MCP round-trip latency; it does not create a hardware trigger.

7. Save the raw capture immediately with `mcp__serial__save_csv`. Use the explicit profile/work-order path. The CSV is the primary artifact.

8. Call `mcp__serial__stats` or `mcp__serial__plot` only when requested or needed for tool-health evidence. They do not replace the raw CSV.

9. Call `mcp__serial__detach` at the end of the work order or when the port must be released.

## Integrity and limits

- Apply the selected profile's decoder and bandwidth rules. Never reinterpret bytes using another profile because values look plausible.
- Check `samples`, `duration_s`, and `est_rate_hz` against the contract. A short window or implausible rate is an acquisition anomaly to report.
- Do not rerun a capture because the waveform looks unattractive. Repeat only when the SOP requires it or a hardware/tool fault requires recovery.
- If the profile says serial bandwidth is insufficient, stop and request Logic2/scope capture. Do not substitute serial telemetry for a faster measurement.
- Do not infer channel identity from numeric values; use the profile's explicit assignment.

## Recovery and report

- Wrong settings, no decoder lock, no data, or a disconnected port: stop, report the observed state, and follow the profile's explicit reconnect limit. Do not change port, baud, profile, or framing opportunistically.
- Hardware faults, overcurrent, overspeed, loss of control, or debugger interruption take precedence over data collection. Apply the applicable safety skill's stop procedure and report `ABORT` with recovery state.
- Never delete or overwrite a capture artifact to make a result look cleaner. Use a new path for each authorized repetition.

```text
Test: <one-line work order>
Profile: <profile name/version>
Serial: <port>, <baud>, <format>
Channels: <profile channels>
Capture: <samples> samples, <duration_s> s, <est_rate_hz> Hz, lock=<true/false>
Status: COMPLETE / ABORT / NEED-SPEC / TOOLING_BLOCKED
Log: <CSV path>
Plot: <PNG path, if requested>
Notes: <tool or hardware anomalies only>
```
