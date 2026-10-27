# Executor–Adviser Protocol 0.2

Use this A2A-inspired contract for one known executor and one known adviser. Consultation and validation are stateless: one JSON request enters through a file or stdin, and one JSON response leaves on stdout. Native preparation is the sole exception; it emits the exact adviser prompt for the executor to hand off.

Run the bridge and tests with `uv run --script`. The scripts declare Python requirements inline and have no third-party dependencies; do not create a `pyproject.toml`, virtual environment, or lockfile for this pilot.

### Live backend runtime requirement

`--backend fake` and `--backend native` validation are local plumbing operations and may run in a restricted sandbox. Live `--backend claude` and `--backend codex` consultations are different: the child CLI must have outbound DNS/HTTPS access to its provider and a writable runtime/config area. A restricted executor sandbox can therefore produce `Claude adviser exited 1`, a timeout with zero stdout, or a Codex read-only-filesystem failure even when the CLI is installed and authenticated locally. In Codex, run live consultations in a network-enabled, writable execution context (request `sandbox_permissions=require_escalated` with a concise justification when required), and use `--claude-health-check` before the first real Claude review. `claude --version`, `claude doctor`, and `claude auth status` are installation/auth checks, not API reachability checks.

Tool scope is also enforced by review scope: `summary-only` launches Claude with no tools, so it cannot turn a compact review into a workspace search. `evidence-read` launches only `Read`, plus `WebFetch` when an exact listed HTTP(S) URI exists; the prompt requires the smallest bounded read around each locator and forbids broad discovery or whole large/binary artifacts. The bridge still owns the whole-process timeout; if a bounded read cannot finish, the adviser must return `input-required` or the executor must fail closed rather than expanding the tool set or retrying an unbounded search.

### Bounded evidence packing

The preferred path for local or bulky evidence is executor-owned packing before the adviser is launched:

```text
uv run --script <skill-dir>/scripts/evidence_pack.py \
  --request <request.json> --output <packed-request.json> --pretty
uv run --script <skill-dir>/scripts/adviser_bridge.py \
  --request <packed-request.json> --claude-health-check --timeout 300
```

`evidence_pack.py` validates the request, accepts explicit local line/range locators, and reads each range in a separate killable worker. It enforces a maximum line span, excerpt bytes, total excerpt characters, per-file timeout, and whole-pack timeout, then emits the same request with `review_scope=summary-only`. Claude therefore receives compact executor-captured evidence and no file tools. Existing excerpts are reused without touching their URI. URLs, binary artifacts, and vague section/name/timestamp locators require an executor-supplied decoded excerpt; the packer never fetches or searches them. Exit code 4 means evidence is unavailable within the bound and the consultation must not start; gather a narrower excerpt or remain fail-closed. A direct `evidence-read` handoff is only for small text with an exact locator or an exact listed URL handled by the bridge's allowlisted `WebFetch`.

## Request

Validate requests against `request.schema.json`. Required fields are:

- `protocol`: `executor-adviser/0.2`
- `id`: unique request identifier
- `context_id`: stable task identifier shared by related consultations
- `state`: `submitted`
- `trigger`: `critical-fact`, `failure`, `ambiguity`, or `result-review`
- `trigger_event`: immutable `advice-triggered` event with a unique ID, matching trigger, source (`automatic`, `user`, or `pilot`), condition, blocking flag, and evidence-readiness flag
- `review_scope`: `summary-only` or `evidence-read`
- `goal`: task outcome and acceptance criterion
- `question`: one decision for the adviser
- `evidence`: locators and optional compact excerpts or SHA-256 digests; require at least one item and `trigger_event.evidence_ready=true` for `critical-fact` and `result-review`
- `attempts`: failure trail; require at least two for `failure`
- `constraints`: plan, safety, privacy, and scope constraints
- `executor_position`: current answer and uncertainty
- `fallback`: reversible action or `STOP`
- `data_classification`: `public`, `private`, or `restricted`
- `consult_index` / `consult_limit`: one-based semantic-round counter; limit is at most three

Reference shared artifacts by path, URL, page, line, or timestamp. Do not copy an entire document or transcript when a locator and small excerpt suffice.

Use `trigger_source=pilot` when the task explicitly tests the workflow. A pilot requires exactly one consultation at an applicable gate; normal execution may legitimately use zero. An explicit user request uses `trigger_source=user` and does not add a fifth trigger type.

The default bridge backend is Claude: it runs a schema-constrained Claude CLI adviser with model `opus`, first-attempt `xhigh` effort, and a 300-second per-attempt timeout. The bridge omits a named Claude agent by default so the executor-adviser response schema remains authoritative. If Claude ends in `failed` or `timeout` after its bounded transport retry, the default flow automatically invokes Codex (`gpt-6-sol`／`xhigh`) as a same-round backend failover. Codex remains available explicitly with `--backend codex` as well.

## Response

The bridge validates responses against `response.schema.json` and preserves `id`, `context_id`, and `review_scope`. `evidence_checked` lists only request evidence the adviser actually opened. It must be empty for `summary-only`; a completed `evidence-read` review must list at least one URI.

The remaining required content fields are:

- `recommendation`: the adviser's actionable answer to the request question
- `reason`: the rationale tied to the supplied evidence and constraints
- `change_evidence`: concrete evidence that, if observed, would change the recommendation; if none realistically would, say so explicitly rather than leaving it empty or generic
- `executor_next_step`: the executor's immediate action given the decision

States:

```text
completed | input-required | rejected | failed | timeout
```

Decisions:

```text
proceed | change | stop | need-evidence
```

`input-required` must pair with `need-evidence`. `rejected`, `failed`, and `timeout` must pair with `stop`.

## Exit codes

```text
0  completed
1  backend failed or timed out
2  invalid request or bridge usage
3  rejected by policy, including restricted data without explicit allowance
4  adviser needs more evidence
```

## Arbitration

1. Prefer objective evidence over model opinion.
2. Preserve adviser-defined goals and constraints.
3. Stop on high-risk `stop` decisions.
4. A Claude transport/process/timeout/malformed-or-schema-invalid-response failure after bounded retry triggers Codex backend failover at the same `consult_index`; it is not semantic agreement or a new round.
5. Challenge a valid adviser recommendation only with new evidence and never beyond the three-round consultation cap.
6. Return unresolved engineering judgment to the adviser and unresolved user intent or permission to the user.

## Two-adviser arbitration

The normal flow has at most three semantic rounds: Claude at `consult_index=1`; Codex at `consult_index=2` only when Claude returned a valid recommendation and the executor has a reasoned disagreement with Claude or an objective result contradicts Claude's expectation; and, only if still unresolved, one final challenge at `consult_index=3` to the adviser whose position is being challenged. Separately, a Claude transport/process/timeout/malformed-or-schema-invalid-response failure automatically invokes Codex as same-round backend failover, preserving the same request `id`, `context_id`, and `consult_index`; this failover does not create a semantic round. Every semantic follow-up request must use a new request `id`, the same `context_id`, `consult_limit=3`, only the new evidence delta, and its own `advice-triggered` event. Use an applicable existing trigger (`ambiguity`, `result-review`, or a qualifying `failure`).

Agreement means materially compatible `decision` values and critical constraints, not identical prose. If the advisers agree and objective evidence supports them, the executor may adopt the shared recommendation and report each response. If they still disagree after round 3, do not average the responses or start a fourth consultation: stop and ask the user for judgment, listing `agreed_items` and `disagreed_items`, each adviser's decision, and the objective evidence. Objective evidence overrides both advisers.

## Failure fallback

Retry only transport errors, nonzero adviser-process exits, timeouts, or malformed/schema-invalid responses, and retry at most once per backend. Codex and Claude reuse the executor-selected effort on the retry. For malformed/schema-invalid responses only, the bridge prepends the specific rejection reason to the next prompt so the adviser can correct it; it never includes transport, process-exit, or timeout details there. After Claude exhausts its retry, invoke Codex automatically as the same consultation's backend failover. Never retry a valid but unwanted recommendation. If both backends are unavailable, use the declared fallback only when it is reversible and low-risk; otherwise stop.

`consult_index` counts semantic rounds. Transport retries or backend failover for the same request keep the same `id`, `context_id`, and `consult_index`. Stop failover after the first valid adviser response.

## Native code-agent handoff

Prepare an exact prompt for a native Sol or other code-agent adviser:

```text
uv run --script <skill-dir>/scripts/adviser_bridge.py --backend native --request <request.json>
```

Send that prompt unchanged through the authorized native agent mechanism. Require JSON-only output, save it, then validate it through the same contract:

```text
uv run --script <skill-dir>/scripts/adviser_bridge.py --backend native --request <request.json> --response <response.json>
```

This adapter does not spawn an agent itself. Native preparation plus schema validation is the thin protocol; an unconstrained prose handoff is not.

If native validation returns a malformed/schema-invalid failure, prepare the one allowed corrected prompt with the same request and the failure response's `reason`:

```text
uv run --script <skill-dir>/scripts/adviser_bridge.py --backend native --request <request.json> --retry-reason "<validation reason>"
```

Send the regenerated prompt once, save the replacement response, and validate it as above. Do not pass transport, process-exit, or timeout failures through `--retry-reason`.

## Event ledger

Pass `--event-log <path.jsonl>` to record the trigger, transport attempts, native handoff preparation, and response validation. Events inherit the request classification and omit evidence arrays/excerpts, but the trigger condition is recorded. Treat the ledger as protocol telemetry, not engineering evidence.

## Pilot metrics

For paired executor-only and executor-plus-adviser tasks, record objective-oracle success, first-pass success, retries, human intervention, total and adviser latency, consultation count, advice adoption, and whether adopted advice improved the oracle. Do not claim improvement from consultation count alone.

## oMLX backend and backend chain (pi variant)

`--backend omlx` posts the prompt to the local oMLX server (`--omlx-url`, default the dreammaker MagicDNS name; key from `--omlx-key-file`, default `~/.config/omlx/key`) with `model` Qwen3.8-27B-4bit-MTP (default; the abliterated build was retired on 2026-10-24), `reasoning_effort` low (default), and the response schema as `response_format`; a server that answers 400/422 to `response_format` is retried once without it. `summary-only` is one call. `evidence-read` runs a bounded tool loop (`read_lines`, `grep`; at most 8 calls, 200 lines, 40 matches, 12 KB per call) over the local files listed in `evidence` only; non-listed paths and symlink escapes are refused, and the bridge itself fills `evidence_checked` from what was actually opened. File contents are untrusted data. A cut-off (`finish_reason=length`) or non-JSON answer is a malformed response and goes through the normal single retry.

`--failover a,b` tries the listed backends in order after `--backend` fails (same request id, context id and consult index). For `restricted` data the chain is filtered to local backends (`omlx`) and never sends data elsewhere; `--allow-restricted` still does not open the cloud backends. Without `--failover`, `--backend claude` keeps the original Claude → Codex failover.

## pi variant: discussion, then Codex-final arbitration

The executor records a disposition after every completed review (`review_disposition` tool from `~/.pi/agent/extensions/review-gate/`): `adopt` or `reject` with a reason. Flow: round 1 reviewer (omlx `low`) → if the executor rejects, round 2 is a challenge to the **same reviewer** (new request id, same `context_id`, `consult_index` 2, only new evidence, the disagreement as `executor_position`; omlx `xhigh`) → if there is still no agreement after two reviewer rounds, round 3 escalates to Codex (`--backend codex`, no `--failover`). **Codex's decision is final** unless the executor has objective counter-evidence (failing test, spec line, measurement); with it the matter goes to the user. Objective evidence overrides any adviser. Restricted data never goes to Codex: the user decides. Agreement (`adopt`, except after `need-evidence`) ends the review: no further round. At most 3 rounds. The extension also blocks, per tool call, file changes and new rounds before a disposition is recorded, rounds out of order, and rounds after agreement. It checks only that a disposition exists, that `change` was followed by an edit, that the challenge and escalation rounds ran, and that the rounds stop at the cap; it cannot judge whether a fix is correct. With a `--tools` allowlist the tool is hidden ("Tool not found"), so add `review_disposition` to the list. The executor must never author or edit a reviewer's response.
