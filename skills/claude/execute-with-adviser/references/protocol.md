# Executor–Adviser Protocol 0.2 (Claude Code fork)

Use this A2A-inspired contract for one known executor (Claude, this session) and one known adviser. Consultation and validation are stateless: one JSON request enters through a file or stdin, and one JSON response leaves on stdout. Native preparation is the sole exception; it emits the exact adviser prompt for the executor to hand off.

Run the bridge and tests with `uv run --script`. The scripts declare Python requirements inline and have no third-party dependencies; do not create a `pyproject.toml`, virtual environment, or lockfile for this pilot.

### Live backend runtime requirement

`--backend fake` and `--backend native` validation are local plumbing operations and may run in a restricted sandbox. Live `--backend codex` and `--backend claude` consultations are different: the child CLI must have outbound DNS/HTTPS access to its provider and a writable runtime/config area. If Claude Code's default Bash sandbox blocks that, rerun the bridge invocation with `dangerouslyDisableSandbox: true`. Use `--claude-health-check` before the first real Claude fallback review; `claude --version`, `claude doctor`, and `claude auth status` are installation/auth checks, not API reachability checks. Codex has no separate preflight phase in this bridge — a bad Codex environment surfaces as a normal backend failure on the first live attempt, which still triggers Claude fallback.

Tool scope is also enforced by review scope: `summary-only` launches Claude with no tools, so it cannot turn a compact review into a workspace search. `evidence-read` launches only `Read`, plus `WebFetch` when an exact listed HTTP(S) URI exists; the prompt requires the smallest bounded read around each locator and forbids broad discovery or whole large/binary artifacts. The bridge still owns the whole-process timeout; if a bounded read cannot finish, the adviser must return `input-required` or the executor must fail closed rather than expanding the tool set or retrying an unbounded search.

A live consultation shells out to a separate `codex` or `claude` CLI process. Prefer running the bridge invocation via Bash with `run_in_background: true` when a live backend may be used — a nested `claude -p` call has been observed to run long in some environments, and backgrounding avoids tying up the turn while the bridge's own per-attempt timeout (default 300s, capped at 600s) does its job.

### Bounded evidence packing

The preferred path for local or bulky evidence is executor-owned packing before the adviser is launched:

```text
uv run --script <skill-dir>/scripts/evidence_pack.py \
  --request <request.json> --output <packed-request.json> --pretty
uv run --script <skill-dir>/scripts/adviser_bridge.py \
  --request <packed-request.json> --claude-health-check --timeout 300
```

`evidence_pack.py` validates the request, accepts explicit local line/range locators, and reads each range in a separate killable worker. It enforces a maximum line span, excerpt bytes, total excerpt characters, per-file timeout, and whole-pack timeout, then emits the same request with `review_scope=summary-only`. The adviser therefore receives compact executor-captured evidence and no file tools. Existing excerpts are reused without touching their URI. URLs, binary artifacts, and vague section/name/timestamp locators require an executor-supplied decoded excerpt; the packer never fetches or searches them. Exit code 4 means evidence is unavailable within the bound and the consultation must not start; gather a narrower excerpt or remain fail-closed. A direct `evidence-read` handoff is only for small text with an exact locator or an exact listed URL handled by the bridge's allowlisted `WebFetch`.

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

The default bridge backend is Codex: it runs a schema-constrained Codex CLI adviser with model `gpt-6-sol`, first-attempt `xhigh` effort, in a read-only ephemeral sandbox, and a 300-second per-attempt timeout. Codex is tried first because the executor here is Claude — an independent model is a more useful adviser than the same model reviewing itself. If Codex ends in `failed` or `timeout` after its bounded transport retry, the default flow automatically invokes Claude (`opus`, `xhigh`) as a same-round backend failover. Claude remains available explicitly with `--backend claude` as well, in which case Codex becomes the failover.

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
4. A Codex transport/process/timeout/malformed-or-schema-invalid-response failure after bounded retry triggers Claude backend failover at the same `consult_index`; it is not semantic agreement or a new round.
5. Challenge a valid adviser recommendation only with new evidence and never beyond the three-round consultation cap.
6. Return unresolved engineering judgment to the adviser and unresolved user intent or permission to the user.

## Two-adviser arbitration

The normal flow has at most three semantic rounds: Codex at `consult_index=1`; Claude at `consult_index=2` only when Codex returned a valid recommendation and the executor has a reasoned disagreement with Codex or an objective result contradicts Codex's expectation; and, only if still unresolved, one final challenge at `consult_index=3` to the adviser whose position is being challenged. Separately, a Codex transport/process/timeout/malformed-or-schema-invalid-response failure automatically invokes Claude as same-round backend failover, preserving the same request `id`, `context_id`, and `consult_index`; this failover does not create a semantic round. Every semantic follow-up request must use a new request `id`, the same `context_id`, `consult_limit=3`, only the new evidence delta, and its own `advice-triggered` event. Use an applicable existing trigger (`ambiguity`, `result-review`, or a qualifying `failure`).

Agreement means materially compatible `decision` values and critical constraints, not identical prose. If the advisers agree and objective evidence supports them, the executor may adopt the shared recommendation and report each response. If they still disagree after round 3, do not average the responses or start a fourth consultation: stop and ask the user for judgment, listing `agreed_items` and `disagreed_items`, each adviser's decision, and the objective evidence. Objective evidence overrides both advisers.

## Failure fallback

Retry only transport errors, nonzero adviser-process exits, timeouts, or malformed/schema-invalid responses, and retry at most once per backend. Codex and Claude reuse the executor-selected effort on the retry. For malformed/schema-invalid responses only, the bridge prepends the specific rejection reason to the next prompt so the adviser can correct it; it never includes transport, process-exit, or timeout details there. After Codex exhausts its retry, invoke Claude automatically as the same consultation's backend failover. Never retry a valid but unwanted recommendation. If both backends are unavailable, use the declared fallback only when it is reversible and low-risk; otherwise stop.

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
