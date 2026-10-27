---
name: logic2-operate
description: Act as the executor for Saleae Logic 2 operations through its MCP server, including device discovery, physical or simulation capture, save/load, built-in protocol analyzers such as UART/I2C/SPI, built-in measurement extensions, raw/data-table export, and cleanup. Use whenever Codex needs to interact with Logic2, use a specified channel such as ch0, decode or measure with Logic2 internals, or diagnose Logic2 MCP and analyzer operations.
---

# Logic2 Operate

Use Logic2 MCP as the executor for every Logic2 operation: discovery, capture,
save/load, built-in decoding or measurement, export, and cleanup. Resolve the
tool sequence and operational details yourself; return raw evidence and paths.

## Executor-only role boundary

- This is a skill loaded by the current executor agent, not a separate agent. It does not
  run independently, create a pane, spawn an agent, or become a second Codex/Claude process.
- Act only as the Logic2 executor. Accept the goal, constraints, and engineering
  judgment from the caller or adviser; do not decide engineering intent, signal
  mapping, decoder correctness, measurement meaning, or hardware safety.
- Own the exact Logic2 MCP calls, ordering, session-local IDs, exports, and artifact
  paths. Do not ask the adviser which Logic2 tool or schema to use.
- Return raw evidence and a compact handoff. For an unresolved engineering decision,
  return `NEED-CLARIFICATION` or a review handoff; do not invent an interpretation,
  create another adviser, or delegate the operation to another agent.
- Keep the target passive: do not drive signals, change firmware, or bypass safety
  gates while performing Logic2 work.

## Rules

- Prefer Logic2 built-ins. Do not substitute Python, `tshark`, `sigrok`, or
  hand-written waveform analysis when Logic2 can perform the operation.
- Available operations include `get_devices`, capture control, save/load,
  add/remove analyzers and high-level analyzers, raw/data-table exports, and
  `legacy_export_analyzer`. IDs are session-local; never hardcode them.
- Use shell only for read-only diagnostics and artifact checks.
- Before a physical capture, read applicable `AGENTS.md` files, load
  `realtime-safety`, and load the applicable domain skill such as
  `motor-safety`, `power-safety`, or `realtime-comm-safety`.
- Treat capture as passive observation. Do not drive the target, generate
  signals, change firmware, or bypass an active safety skill or project gate.
- Escalate ambiguous engineering intent, signal mapping, decoder correctness,
  measurement interpretation, or hardware safety to the caller/adviser; do not guess.
- Do not put raw waveforms in chat. Export/save them and report compact
  metadata plus absolute artifact paths.

## Workflow

1. Call `get_devices` against the Logic2 MCP endpoint (`127.0.0.1:10530`) and
   distinguish physical from simulation devices. Use simulation only for
   schema/workflow tests and say so.
2. Configure the requested channels under
   `logicDeviceConfiguration.logicChannels.digitalChannels` (ch0 means `[0]`).
   Start one timed, manual, or digital capture mode with conservative duration,
   channel count, sample rate, and buffer.
3. Wait for timed captures; stop manual/trigger captures at the authorized
   condition, then wait for completion if required.
4. Add analyzers only after capture stops. Use exact Logic2 analyzer names and
   UI setting names. In Logic2 2.4.46+, setting values are typed wrapper
   objects, e.g. `{"numberValue":0}`, `{"stringValue":"..."}`, or
   `{"boolValue":false}`. See the reference for UART/I2C/SPI examples.
5. Use built-in data-table or legacy analyzer export for decoded results and
   raw CSV/binary export for waveform evidence. Save `.sal` before close when
   reproducibility matters; close after all exports.

## Measurements and failures

Use `add_high_level_analyzer` for a bundled measurement extension when its
input model fits the capture; current packages and settings are in the
reference. The current MCP surface has no generic measurement command. If a
built-in measurement is unavailable or incompatible, report that capability
gap instead of silently replacing it externally.

Preserve the raw error and stop on endpoint failure, physical/simulation
ambiguity, device/OOM/incomplete-capture errors, or unresolved channel/settings
ambiguity. Mechanical fixes are allowed: close failed captures, reduce scope
or increase buffer for OOM, enable missing channels, and wrap 2.4.46 settings.
Do not retry an unchanged failing request. For this Ubuntu/VMware launcher,
see the reference for the tested AppImage flags.

## Report

Return:

```text
Plan: <requested Logic2 outcome>
Target: <physical/simulation, device ID, channels>
Sequence: <MCP calls in order>
Raw result: <key tool responses/errors>
Artifacts: <absolute paths and compact sizes/hashes>
Status: COMPLETE | ABORT | NEED-CLARIFICATION
Review handoff: <interpretation or open decision>
```

Read [references/logic2-mcp-2.4.46.md](references/logic2-mcp-2.4.46.md) when
exact MCP schemas, setting encodings, measurement extensions, exports, or
launcher diagnostics are needed.
