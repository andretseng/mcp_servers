---
name: execute-with-adviser
description: Independently execute and verify coding, document, research, PDF/video, hardware, and tool-driven work; consult an adviser only at bounded decision, failure, ambiguity, or weak-oracle gates. Codex fork of the Claude-side adviser bridge — Codex is consulted first, Claude opus is same-round failover.
---

# Execute with Adviser

Own inspection → execution → verification → output. The adviser is read-only and consult-only, never a second executor. Ask the user about intent, permission, safety, destructive actions, spending, or preferences; prefer objective or authoritative evidence.

## Gates

Per `~/.claude/CLAUDE.md` "Adviser consult (all projects)", consult — do not skip — whenever any of these apply:

1. Before claiming a job/task is finished (`result-review`).
2. When something is uncertain (`ambiguity`, or `critical-fact` if a specific fact is missing/conflicting).
3. When you hit an issue after retrying twice (`failure`).
4. When the user explicitly asks for one (any trigger, `trigger_source=user`).

Include the question, acceptance criteria, constraints, evidence, attempts, and fallback. Emit `advice-triggered` first (`automatic|user|pilot`; a pilot exercises exactly one gate). Skip routine choices/discovery, first recoverable errors, formatting, and strong-oracle results.

## Protocol

Read [protocol](references/protocol.md) and validate [schema](references/request.schema.json). Use `summary-only` or allowlisted `evidence-read`; never use `--allow-restricted` without explicit authorization. Run the bridge/tests with `uv run --script`; Codex is the default (`gpt-6-sol`, `xhigh`), with Claude (`opus`, `xhigh`) as same-round backend failover.

Live `claude` and `codex` backends require a network-enabled, writable runtime: the adviser CLI must resolve/reach its provider API and must be able to use its runtime/config directory. A restricted Bash sandbox can produce a Codex or Claude backend failure even when the CLI is installed and authenticated — rerun with `dangerouslyDisableSandbox: true` if that happens. Do not infer Claude API health from `claude --version`, `claude doctor`, or `claude auth status`; pass `--claude-health-check` before a real Claude fallback review. Summary-only reviews must run with no Claude tools; evidence-read reviews use only bounded `Read` (and `WebFetch` for exact listed URLs), never broad `Grep`/`Glob` discovery. If the preflight or consultation fails for DNS, auth, filesystem, provider transport, or an unbounded evidence read, report that raw class and stop/fail over according to the protocol; do not silently retry the full review in the same blocked environment. Prefer `run_in_background: true` on the Bash call for a live consultation — a nested `claude -p` fallback call can run long, and the bridge's own timeout (default 300s) should be what ends it, not the turn.

For local or bulky evidence, pack it before consulting so the adviser never owns a potentially hanging read:

```text
uv run --script <skill-dir>/scripts/evidence_pack.py \
  --request <request.json> --output <packed-request.json> --pretty
uv run --script <skill-dir>/scripts/adviser_bridge.py \
  --request <packed-request.json> --claude-health-check --timeout 300
```

The packer accepts explicit local line ranges only, reads each range in a killable child with byte, line, per-file, and total deadlines, and changes the handoff to `summary-only` with compact excerpts. Supply an executor-created excerpt for URLs, binary artifacts, or section/name/timestamp locators. Exit code 4 means evidence could not be bounded; gather narrower evidence or stop. Do not send the original large artifact to the adviser as a workaround. Direct `evidence-read` is reserved for a small text artifact with a precise locator or an exact listed URL that the bridge can fetch.

One retry is allowed for a transport error, process exit, timeout, or malformed/schema-invalid response. Only malformed/schema-invalid responses feed the rejection reason into the retry prompt; transport, process-exit, and timeout failures do not. After Codex fails or times out, automatically fail over to Claude (`opus`, `xhigh`) with the same request ID, context ID, and semantic index, emitting `backend-failover`; this is not a semantic round. If both fail, remain fail-closed. For URL evidence, Claude may `WebFetch` only exact listed URLs and the bridge needs a network-enabled context. Native advisers require exact `--backend native` preparation and `--response` validation; after a malformed/schema-invalid native response, regenerate the handoff prompt with `--retry-reason` set to the returned validation reason, then revalidate. Prose is not success.

## Arbitration

A valid Codex result plus executor disagreement or objective contradiction may start semantic round 2 with Claude: new ID, same context, index 2. Allow at most round 3 for a final challenge; then ask the user. Send only new evidence; do not rubber-stamp, average, or loop. Objective evidence wins.

`proceed`/`change`: apply and verify. `stop`: block high-risk work. `need-evidence`/`input-required`: gather the requested evidence. `rejected`: do not bypass. After both backends fail, use the declared reversible fallback only for low-risk work and mark `UNREVIEWED`; otherwise stop/user.

## Report

Record the trigger, question, decision/adoption, primary → fallback path, and evidence. Keep raw execution facts separate from adviser judgment.
