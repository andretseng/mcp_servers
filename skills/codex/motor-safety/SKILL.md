---
name: motor-safety
description: Enforce motor-bench startup, fault, phase-entry, interruption, and resume safety gates. Use for any physical motor, inverter, ESC, FOC, open-loop, closed-loop, motor-identification, current-loop, speed-loop, or rotating test, including CCS/debugger control, motor enable, setpoint changes, captures, fault handling, and resumed tests. Load it together with realtime-safety and every required equipment-operation skill.
---

# Motor Bench Safety

Apply `realtime-safety` first. Use the relevant operation skill for each device;
this skill owns motor-domain authorization and stop conditions, not tool-call
mechanics or control-theory decisions.

## Gate each phase

Before `PRECHECK` and every phase that can enable or rotate the motor:

1. Verify that the previous phase result and the bench's safe state are durably
   recorded. Do not infer completion from chat memory or an old checkbox.
2. Put the bench in the plan's quiescent/de-energized state. Ask the human to
   inspect wiring, mechanical restraint, supply/current limits, emergency stop,
   safe-stop readiness, and the phase-specific setup. Wait for fresh
   confirmation.
3. While the bench remains safe and quiescent, ask the human to run `/compact`.
4. After compaction, reload `realtime-safety`, this skill, the approved plan,
   committed run state/events/phase receipt, and the required operation skills.
   Rediscover tool endpoints, devices, and sessions; re-read critical state
   before any write or motor enable.

Require a human-usable de-energization method that does not depend on debugger
halt or firmware cooperation.

## Enforce safe motor startup

For the TI drone firmware that defines
`motorVars_M1.inputCtrl.startupMode`, before every rotating test:

- use `ccs-operate` to set and read back
  `motorVars_M1.inputCtrl.startupMode = 0` (`STARTUP_ALIGN`) before motor enable;
- for approved open-loop work, set and read back
  `flagEnableContAlign=0`, `flagEnableAlignDebug=0`, and
  `flagEnableOpenLoopDebug=1`; and
- use IPD only when the current test explicitly tests IPD and a fresh human
  check confirms the rotor is stationary. Residual coast, a retry, or a resumed
  run is not evidence of standstill.

For other motor firmware, do not invent equivalent symbols or values. Require
the approved plan to define a safe startup mode and its exact readback gate.

## Bound the known TI drone first-enable fault

For the same TI drone firmware, classify
`motorVars_M1.outVars.faultMtrNow == moduleOverCurrent (16)` as the known benign
first-enable transient only when every condition is observed and recorded:

- it is the first motor-enable/current ramp of the phase;
- the fault value is exactly `16`;
- measured current is in the known 1–2 A case and strictly below 3 A;
- no additional fault or unsafe physical symptom is present;
- the fault clears through the plan's authorized non-halting firmware path; and
- it does not recur during the same run.

Record the event and current evidence before clearing it, then verify the
cleared fault state before continuing. If current evidence is missing, current
is at least 3 A, the event recurs, another fault is present, or it cannot be
cleared, do not apply the exception: execute the independent safe stop, record
`ABORT`, and require human inspection before another attempt.

For every other motor or firmware, treat overcurrent as a fault unless its own
approved motor-safety rule defines a separately evidenced exception.

## Record, interrupt, and resume safely

After each phase, first reach and verify its safe state. Then durably record raw
artifact identities, safety and fault observations, analysis identity, phase
result, and the next authorized phase. If execution breaks or any hardware,
firmware, session, or run state is uncertain, independently de-energize and
record the interruption. Resume only from the highest complete, integrity-valid
phase receipt after repeating the entire phase gate; never resume directly from
conversation memory or a partially written result.

Stop without enabling the motor when a required human check, safety readback,
safe-stop path, exact target identity, or committed resume state is missing.
Never ask an adviser or operation skill to waive these gates.
