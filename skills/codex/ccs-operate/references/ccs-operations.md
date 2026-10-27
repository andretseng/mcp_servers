# CCS Operations Reference

Use this reference to validate and execute CCS MCP work orders. Treat the currently loaded MCP schemas and the project's vendor `CCS.md` as authoritative when historical tool names differ.

Enforce lookup order: vault → project root → installed SDK/CCS → internet last. Do not use an internet result until the three local tiers were searched and found insufficient; report the tiers used.

## Provenance and evidence levels

Adapt these rules from:

- `/home/andre/.claude/agents/ccs-operator.md`, reviewed 2026-08-04;
- `/home/andre/llm-wiki/.claude/playbook/50-ccs-mcp-efficiency.md`, whose CCS MCP observations were recorded on 2026-07-28.

Interpret evidence labels as follows:

- **Verified**: apply by default; the source playbook records an observed CCS MCP result.
- **Vendor-documented**: follow conservatively, and verify returned target state before reading dependent values.
- **Unverified**: do not apply by default or present as established behavior.

## Tool ownership

- Open and change `.syscfg` files only through `ccs-sysconfig`. Never edit them with filesystem tools. On a SysConfig version mismatch, stop for clarification rather than upgrading automatically.
- Change `.project`, `.ccsproject`, `.cproject`, and `.settings/` only through `ccs-project`. Never edit them directly.
- Build an existing CCS project only with `ccs-project` `buildProject`. Do not invoke `make` or `gmake` through a shell. Use a shell build only when the work order explicitly states that the target has no CCS project.
- Use `ccs-debug` for permitted program loading, run control, debugger reads/writes, variables, and breakpoints. The absolute no-halt rule below overrides this general tool ownership.
- Use `ccs-serial` for CCS serial-console operations. Prefer a combined request/response call over separate write and read calls when the loaded schema provides one.

## Project and build calls

- **Verified**: filter `getProjectDescriptors` `properties` to only the required fields. Requesting only `name` preserved the same project names and order while greatly reducing output in the recorded test.
- **Verified**: leave `buildProject.outputMode` unset so its `auto` behavior can return small logs inline and fall back to a file for larger output.
- **Unverified**: reading tool flags before setting them may avoid duplicates. Do not make this an extra call unless the work order needs flag modification.

After a build, bind later load/debug steps to the `.out` path or build artifact returned for the resolved project and configuration. Do not infer an artifact from an unrelated session.

## Debug-session discipline

- **Absolute no-halt rule**: once a power-, motor-, or energized closed-loop test is running, do not halt, suspend, pause, break, step, reset, or perform a watch/variable/register/memory operation that may stop the control program. Use only a proven non-intrusive observation path. If none exists, obtain the value only after the authorized safe shutdown/de-energization path, or report it unavailable.
- Probe `getDebugSessions` before the first debug operation unless liveness was already confirmed in the current session. An empty list means the server is reachable with no active debug session; a tool-call error means the server is unavailable.
- Operate only one debug session at a time. If multiple sessions exist and the target is not unambiguous from the plan or project context, request clarification.
- Use `buildProject` -> `loadProgram` with the newly built `.out` -> `continue` for rebuild-and-rerun. `restart` resets the program counter for the currently loaded binary; it does not load the new build. Derive the correct reload sequence yourself.
- **Vendor-documented**: outside a running power or motor test, poll `getTargetState` after `continue`, `stepOver`, or `stepOut` before reading halt-dependent values. Treat step-into according to the loaded vendor documentation. This rule never permits stepping or halt-dependent reads during a no-halt test.
- Tie every debug result to the selected session and target core. Stop if the response cannot be attributed to them.

## Indexed and array variables

- **Verified correctness rule**: never call `getVariables` on an indexed or array `variablesReference` without an explicit `count`. A bare call silently returned only about ten elements in the recorded test and provided no truncation flag.
- First call `evaluate` on the array expression and read its `indexedVariables` count. Then call `getVariables` with `start: 0` and `count: <indexedVariables>`.
- Confirm that the returned element count matches `indexedVariables`. Report a mismatch as an executor/tool issue; do not analyze the array contents.
- **Verified**: use the top-level locals-only `getVariables` form when only local variables are requested instead of expanding unrelated arrays.

## SysConfig and serial caution

- SysConfig filtering suggestions in the source playbook are **Unverified** because `openFile` timed out in all recorded attempts. Do not introduce filtering options as mandatory behavior.
- Prefer a combined serial command-and-response tool when the loaded schema supports it.
- Do not rely on a default serial configuration unless it matches the work order or SysConfig. Use explicit baud rate, data bits, parity, and stop bits when they differ; a mismatch may produce plausible-looking garbage rather than an obvious error.

## Stop conditions

Stop with `NEED-CLARIFICATION` for a missing engineering or safety choice. Resolve CCS mechanics yourself. Stop with `ABORT` for a CCS tool error after execution begins. In both cases, return completed calls and raw evidence already obtained; do not retry because a value or capture merely looks wrong.
