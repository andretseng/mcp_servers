---
name: realtime-safety
description: Prevent unsafe interruption of any live real-time controller. Use whenever planning, executing, diagnosing, measuring, or reviewing debugger, run-control, watch, memory, trace, telemetry, or instrument operations on an active motor drive, power converter, servo, robot, PLC, industrial controller, real-time communication target, or other system whose outputs depend on uninterrupted control timing. Load it together with the applicable domain-safety and equipment-operation skills.
---

# Real-Time Control Safety

Apply one universal invariant: **never halt a live real-time control program**.
Treat the target as live whenever control timing affects physical outputs,
stored energy, process state, communication deadlines, or protective behavior.
If live state is uncertain, classify it as live.

## Enforce the invariant

- While the target is live or its controlled process may still be active, never
  pause, halt, suspend, break, single-step, reset, or invoke any debugger action
  that can stop or delay the control program. A debugger halt is not a shutdown
  method.
- Do not use a watch, expression evaluation, register read, memory read, or
  trace operation during live control unless that exact operation is already
  verified and documented as non-halting for the current tool, target, and
  session. Treat uncertain behavior as halt-dependent and forbidden.
- Observe live state only through proven non-halting telemetry, trace, capture,
  or real-time debug access. If required evidence is available only through a
  halt-dependent path, first use the system's authorized independent safe stop
  and verify that the controlled process and hazardous energy are safe.
- Require an independent, human-usable safe-stop or de-energization path before
  starting an operation that could make the target live. Do not rely on the
  debugger connection or control program as the only stop path.

## Compose skills without weakening safety

- Load the applicable equipment-operation skill to choose and sequence tool
  calls. Load a domain skill such as `motor-safety`, `power-safety`, or
  `realtime-comm-safety` when one exists for the system under test.
- Let this skill decide whether a potentially halting action is permitted; let
  the operation skill decide how to perform an allowed action. Apply the
  strictest instruction when skills overlap.
- Do not infer that a tool advertises "real-time", "live watch", or
  "non-intrusive" behavior. Require locally verified behavior for the exact
  access path.
- Do not add motor-, converter-, or protocol-specific thresholds here. Keep
  domain fault classification, startup state, current/voltage limits, and
  recovery rules in their domain skills or approved test plans.

## Stop fail-closed

Before a forbidden or uncertain access, decline the call and name the safe
observation or shutdown prerequisite. If a tool unexpectedly interrupts or
loses contact with a live target, invoke the independent safe stop, preserve
the event evidence, and report `ABORT`; never reconnect and resume on an
assumption that the controller or process state survived.
