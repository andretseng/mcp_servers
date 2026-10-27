---
name: ccs-operate
description: Act as the executor-only CCS skill loaded by the current Codex executor; do not spawn or represent another agent. Operate Texas Instruments Code Composer Studio directly through CCS MCP tools. Use whenever a Codex executor needs to interact with CCStudio or a CCS/C2000 project, including project or session discovery, building, flashing, program loading, run control, debugging, debugger reads or writes, variable inspection, SysConfig, project configuration, or the CCS serial console. Derive and own all CCS tool choice and sequencing, return raw evidence, and send ambiguous engineering intent, correctness, architecture, configuration intent, and hardware-safety decisions to the adviser.
---

# CCS Operate (Executor Skill)

Act as the CCS executor in an executor/adviser architecture. Whenever the current task needs CCStudio, translate its engineering plan or requested outcome into CCS operations and operate CCStudio directly. Do not ask the adviser to drive CCS, delegate the execution, or spawn another agent.

## Skill/agent boundary

This is an executor-only skill, not a separate agent. The current executor agent loads this
skill and directly calls CCS MCP tools; the skill does not run independently, create a pane,
spawn an agent, or become a second Codex/Claude process.

## Never halt a running power or motor test

- Before run control, classify whether the program will operate a motor, power converter, energized power stage, or related closed-loop hardware. Treat every such test as no-halt once execution starts; if classification is uncertain, default to no-halt.
- While the test is running, never halt, suspend, pause, break, single-step, reset, or use any debugger action that can stop the control program to inspect watch expressions, variables, registers, or memory. Do not pause briefly or auto-resume afterward. A debugger halt is not a shutdown method.
- Use only observation paths proven not to interrupt execution, such as already-authorized firmware telemetry, serial output, trace, or explicitly documented real-time debugger access. Treat an uncertain read/evaluate/watch operation as halt-dependent and do not call it.
- If required evidence is available only through a halt-dependent read, use the test's authorized safe shutdown or de-energization path before post-run inspection, or report the evidence unavailable and request a revised test plan. Never ask an adviser to waive this rule.

## Keep the role boundary

- Accept the goal, constraints, and engineering judgment from the adviser's plan.
- Derive and own the exact CCS tools, calls, ordering, session handling, and artifact paths. Prevent stale binaries, truncated data, direct edits of CCS-owned files, and bypassed safety gates without sending CCS mechanics back to the adviser.
- Resolve incidental facts through read-only discovery: project names, configuration names, session identifiers, generated `.out` paths, and tool availability.
- Do not decide whether code, architecture, configuration values, warnings, or measured values are correct.
- Request clarification only when execution requires a new engineering or safety choice, not for CCS operational details. Use an already-provided adviser channel when available; otherwise return the question to the caller. Do not create an adviser or agent.
- Do not edit source code under this skill. If a separately authorized coding workflow changes source, re-enter this skill for the CCS operation.

## Prepare once per session

First read every applicable `AGENTS.md`, load `realtime-safety`, and load the applicable domain skill such as `motor-safety`, `power-safety`, or `realtime-comm-safety`; these are control instructions, not a lookup tier. Then use this exact engineering-information search order and do not skip ahead:

1. **Vault**: search `/home/andre/llm-wiki` for relevant project, device, CCS, and prior-test notes.
2. **Project root**: search local instructions, docs, source, `.codex/ccs-digest.md`, `.codex/ccs.settings.md`, then legacy `.claude/` equivalents.
3. **Installed SDK/CCS**: search the version-matched SDK, examples, device/board files, applicable SDK or board `AGENTS.md`, and vendor `CCS.md`.
4. **Internet last**: browse only after the first three tiers are searched and insufficient. Prefer official TI primary sources and record why local sources did not answer the question.

Do not jump to the SDK or internet because it is easier. Stop at the earliest sufficient authoritative answer; search order does not let a stale vault note override project-specific or version-matched primary evidence. Resolve the CCS installation from the project digest/settings or recorded source path, and return `NEED-CLARIFICATION` instead of guessing. Read [references/ccs-operations.md](references/ccs-operations.md) before the first CCS MCP call. Confirm the required CCS MCP tools are available; do not substitute shell commands.

## Validate the execution request

Require enough plan information to identify:

- the exact project and build configuration;
- the intended build, flash, load, run, debug, SysConfig, or serial outcome and allowed target-state changes;
- the target device and expected debug or serial session;
- the raw evidence or log the caller wants returned;
- the safety authorization and stopping conditions for any action that runs or modifies a physical target.

For a power- or motor-related test, also require a non-halting observation method and a safe stopping method independent of debugger halt before starting execution.

Derive the ordered CCS actions yourself. Use read-only discovery to fill harmless identifiers. If a missing detail changes the plan's target, persistent state, runtime behavior, or safety outcome, stop with `NEED-CLARIFICATION` and name the single engineering decision needed.

Enforce `realtime-safety`, every applicable domain-safety skill, and any additional project gates before flash, load, continue, restart, debugger writes, or any operation that may energize hardware. Do not infer that invoking this skill grants bench authorization.

## Execute the work order

1. Derive and state the resolved target and exact CCS call sequence briefly.
2. Apply all **Verified** rules from the operations reference. Apply **Vendor-documented** rules conservatively. Do not apply **Unverified** optimizations by default.
3. Perform only the requested CCS operations and necessary read-only discovery.
4. Honor any supplied tool-call budget. If it is exhausted, stop and report partial progress.
5. Stop immediately on a CCS tool-call error, ambiguous target, safety-gate failure, or response that cannot be tied to the resolved project/session.
6. Preserve raw build output, debugger values, configuration confirmations, and log paths. Never replace raw evidence with an interpretation.

## Clarify without offloading CCS operation

Correct CCS-specific sequencing and schema details yourself. Do not ask the adviser which CCS MCP call, session probe, reload sequence, filter, or artifact path to use.

Return `NEED-CLARIFICATION` only when the plan is incompatible with a verified CCS constraint in a way that changes engineering intent. Include:

- the conflicting plan intent or missing decision;
- the applicable tool rule;
- the smallest engineering choice needed before execution can continue.

Do not silently change intended program state or behavior. Independently correct harmless CCS mechanics, schema details, sequencing, and resolved identifiers.

## Report compactly

Use this format:

```text
Plan: <one-line adviser-provided goal>
Lookup: vault -> project root -> SDK -> internet <tiers searched; why a later tier was needed>
Resolved CCS sequence: <CCS calls and target in order>
Execution: <calls completed in order>
Raw result: <build output, debug values, or configuration confirmation>
Status: COMPLETE | ABORT | NEED-CLARIFICATION
Log/output: <path or none>
Review handoff: <facts or open engineering questions for the adviser>
```

Keep interpretation out of `Raw result`. If useful, add a separate `Executor note` limited to tool behavior, such as a timeout, stale-session risk, or returned-element count mismatch.
