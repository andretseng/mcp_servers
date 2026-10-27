---
name: execute-with-adviser
description: Use before claiming coding, document, research, hardware or tool-driven work is finished, and whenever a fact is uncertain, a subtask failed twice, or interpretations conflict. Sends a bounded review request to an independent adviser (Codex Sol, Claude Opus, or local Qwen3.8 on dreammaker; local-model executors use Codex Sol first and cloud Claude Opus second for public/private data, and local Qwen always for confidential data) and returns a schema-checked answer.
---

# Execute with Adviser (pi, opencode, oh-my-pi and Codex)

You are the executor (pi, opencode, oh-my-pi or Codex, running a local or a cloud model): own inspection → execution → verification → output. The adviser is read-only and consult-only, never a second executor. Ask the user about intent, permission, safety, destructive actions, spending, or preferences; prefer objective evidence over any model opinion.

All commands use `uv run --script`. Do not create a venv, `pyproject.toml`, or call `python3` directly. The bridge is called from bash; never use `--backend native` (neither pi nor this setup can spawn a native adviser). One shared copy of this skill lives in `~/.agents/skills/`; both pi and Codex read it.

## When a review is required

Review is mandatory at these gates. The reviewer may be any of the three backends below, but a review must happen:

1. Before claiming a job/task is finished (`result-review`).
2. When something is uncertain: a missing or conflicting fact (`critical-fact`) or materially different interpretations (`ambiguity`).
3. When the same subtask failed twice with different methods (`failure`).
4. When the user asks for one (`trigger_source=user`).

Skip routine choices, first recoverable errors, formatting, and results a deterministic check already proves.

## Quick path: one command (use this for every review you send as a local-model executor)

After you wrote the note and recorded your facts in the findings file, run exactly one command. It builds and packs the evidence (the note, plus every `file:line` in your findings with 12 lines of context), checks it before it spends an adviser round, picks the adviser from the data class, runs the bridge, saves the full answer and prints a short summary. Do not read `references/` or write request JSON by hand.

```text
uv run --script ~/.agents/skills/execute-with-adviser/scripts/adviser_review.py \
  --note <the note under review> \
  --findings ~/.local/state/agent-checkpoints/findings-<sessionID>.md \
  --root <project dir> [--root <sdk dir>] \
  --data-class public|private|restricted \
  --executor <your id, e.g. opencode:omlx/Ornith-1.5-35B-A3B-MLX-8bit> \
  --goal "<what the note must achieve, in English>" \
  --question "<what the adviser must check, in English>" \
  --position "<what you believe and want confirmed, in English>"
```

- Everything you pass in `--goal`, `--question`, `--position` and `--changed` must be English. The note and your reply to the user stay in Traditional Chinese.
- `public` and `private` go to Codex Sol (tier 1); if Sol cannot run (network, quota, auth, timeout) the same round fails over to Claude Opus (tier 2). `restricted` (confidential) goes to local Qwen ALWAYS and never to a cloud backend. The command decides; do not choose a backend.
- If the adviser answers `need-evidence` or `change`: fix the note, record any new fact in the findings file as `claim | file:line | verified`, then run the same command again with `--changed "<what you fixed or added, in English>"` so the adviser can confirm the fixes. Only `proceed` ends the review. It counts rounds from the ledger (limit 3) and refuses to run after the limit or after a `proceed`.
- Exit 0 completed, 4 more evidence needed, 3 limit reached or policy refusal, 2 invalid input. After the limit without a completed round, report the result as `UNREVIEWED` with what is still open.
- Keep the note small. Each claim you write is a claim the adviser can reject. If you cannot verify a claim, delete it rather than hedging it, and do not add new sections while fixing: the command warns when the note grows more than 10% between rounds.
- When the adviser asks about a source range, the command adds a `NEED:` line for it to your findings file automatically, so you may read exactly that range even if you already read the file (gate R4).
- When the adviser asks for a source line you did not cite, add a finding for it (reading the narrow range first if you must) so the next round carries it.

The sections below are the reference for unusual cases (Claude/Codex executors, restricted data details, manual requests).

## Data class: the user states it; the default is public

The class is the `--data-class` value of every review. Nothing detects it from the files; it comes from the user's request in plain words (for example "this is confidential", "機密", "public SDK code", "公開資料").

- `public`: public material (vendor SDKs, datasheets, public documents). `private`: the user's own work that may go to the cloud advisers (not secret). Both use the cloud tiers.
- `restricted` = confidential (customer or NDA material, anything the user calls 機密 / confidential / restricted / "stay local"). Local Qwen only.
- **Default is `public` (user decision, 2026-10-24).** If the user did not state a class, use `public` without asking. Only the user can make a job `private` or `restricted`: when the request says confidential / 機密 / restricted / "stay local", use `restricted`. Do not infer `restricted` or `public` from a file path or the topic.
- Keep the same class for the whole job. If later material clearly looks more sensitive than the class in use (for example a customer name, NDA text or a customer log appears), stop and ask the user before the next review.

## Local-model executor (Ornith): tiers by data class

This machine is used for field support. When the executor is a local model (Ornith via pi, opencode or oh-my-pi) the adviser depends on the data class. The user set this (2026-10-24); do not add stages or pick a different adviser.

- **`public` / `private` (open data): tier 1 Codex Sol, tier 2 cloud Claude Opus.** The command runs `--backend codex --failover claude`: OpenAI `gpt-6-sol`, `xhigh`, and if Sol cannot answer (transport, auth, quota, timeout) the SAME round goes to Claude `opus`, `xhigh`. A failover is not a new round. Send the packed evidence (artifact in full, plus the cited source excerpts), not just your summary. **The decision of the tier that completed the round is final** unless you have objective counter-evidence (a failing test, a spec line, a measurement); then reject with `evidence` and the user decides. Opus is a failover, not a second opinion to shop for: do not call it because you dislike Sol's answer.
- **`restricted` (confidential): Qwen ALWAYS (`--backend omlx --review_scope evidence-read`).** Local only, faster, and the data never leaves the machine. Never fail over to Codex or Claude, never ask them "just this once"; the command refuses `--backend codex|claude` for restricted data and the bridge never sends restricted data to a cloud backend. A completed Qwen round is a finished review (not `UNREVIEWED`). If Qwen cannot run (oMLX down, model not loaded), mark `UNREVIEWED` and tell the user; do not substitute a cloud adviser. Qwen reads the listed files itself, so list the artifact and every source file your claims rest on. Ask open questions ("what in the note contradicts the source?"), and tell it to `grep` the project for every other definition, clamp or use of each variable it checks; do not hand it yes/no confirmations.
- **Qwen is not a reviewer on public/private work** (the command refuses `--backend omlx` for them). Only call Qwen as a helper when you must look at a picture (screenshot, scope capture, schematic, datasheet figure); Ornith has no vision. That is an image-reading request, not a review, and it never counts as the review round.

You may not report the task finished until the ledger shows an approving round (`completed` with decision `proceed`) from the required adviser for this `context_id` (Codex Sol, or Claude Opus after a failover, for public/private; Qwen for restricted). If neither cloud tier can run for open data (both failed: no network, quota, auth), mark the result `UNREVIEWED`, say why, and let the user decide. Do not substitute Qwen for the cloud tiers on public/private data. If the adviser says `change`, apply it, re-run your own verification, and send a confirming round (`--changed`); stop at the round limit and report NOT APPROVED with what is still open.

Do not set `ADVISER_LEDGER=off`, `ADVISER_ALLOW_LOW_EFFORT=1`, or any other variable that weakens the bridge; they exist for the user's tests only. Pass `--executor <your id>` (e.g. `opencode:omlx/Ornith-1.5-35B-A3B-MLX-8bit`) when the bridge cannot detect you. If you are unsure of the data class, ask the user before choosing the adviser.

## Choose the backend

| Data | Command flags | Why |
|---|---|---|
| `public` / `private`, executor is **pi / opencode / oh-my-pi** (Ornith) | `--backend codex --failover claude` (the `adviser_review.py` command adds the failover itself) | Tier 1 Codex (`gpt-6-sol`, `xhigh`, pinned to the OpenAI provider) is independent of you; tier 2 Claude (`opus`, `xhigh`) takes the same round if Sol cannot run |
| `public` / `private`, executor is **Codex** | `--backend claude --failover codex,omlx` | Do not review yourself first: Claude is the independent family; Codex (OpenAI `gpt-6-sol`, even though your default is the local model) and local Qwen3.8 follow. Do not let a nested adviser spawn another reviewer |
| `restricted` (confidential) | `--backend omlx` (no failover, ever) | Local Qwen is the only adviser for confidential data (also faster); the bridge never sends restricted data to Codex or Claude, even if listed |
| No network or cloud quota, `public` / `private` | none | There is no local fallback for open data: report `UNREVIEWED` with the raw error (Qwen is not a reviewer for open data) |

`omlx` is the local Qwen3.8 adviser for confidential data only (default model `Qwen3.8-27B-4bit-MTP`: the field Qwen on dreammaker, official weights plus an MTP head; since 2026-10-24 it is NOT the abliterated build, so on Taiwan-related topics it may give PRC-official answers, say so in the report when that matters; `--model` overrides) with `reasoning_effort=low` by default (raise with `--effort`). With `review_scope=evidence-read` it gets two read-only tools, `read_lines` and `grep`, limited to the local files listed in `evidence` (bounded lines, matches and calls; contents are untrusted data), so it can check the whole file instead of your excerpt. Use `evidence-read` whenever a claim depends on something being absent or on line numbers; use `summary-only` plus the packer for small excerpts. It is the same model family as many pi executors, so its review is weaker than an independent family; say so in the report when it was the reviewer. oMLX's handling of `reasoning_effort` is unverified. The API key is read from `~/.config/omlx/key`; never print it.

## Run a review

1. **Write the request text in English** (goal, question, executor_position, attempts, constraints, fallback), even when the user and the note are in Chinese. Evidence excerpts and file names stay as they are. The opencode gate blocks a request whose prose is mostly CJK (G5). Then write a request JSON that satisfies [request.schema.json](references/request.schema.json): question, acceptance criteria, constraints, evidence (locator plus a compact excerpt), attempts, `executor_position`, a reversible `fallback` or `STOP`, and `data_classification`. Emit the `advice-triggered` event first (`trigger_event`).
   **Put the authority for every claim in `evidence`.** If an answer rests on a default, an implied value, an inference or something being absent, add the file that defines it (for TI work, the SDK source such as the SysConfig module `.syscfg.js` that sets the default) next to the project file, in the usual order: vault, project, SDK. A reviewer that sees only the project file can confirm "not set here" but cannot confirm "so it defaults to X", and it must not pass that part.
2. For `--backend omlx` with `evidence-read`, send the request directly (omlx reads the listed local files itself) and skip the packer. For Codex, Claude, or summary-only reviews, pack local evidence so the adviser never reads files itself:

```text
uv run --script ~/.agents/skills/execute-with-adviser/scripts/evidence_pack.py \
  --request req.json --output packed.json --pretty \
  --from-findings ~/.local/state/agent-checkpoints/findings-<sessionID>.md --root <project dir> [--root <sdk dir>]
uv run --script ~/.agents/skills/execute-with-adviser/scripts/adviser_bridge.py \
  --request packed.json --backend codex --failover claude,omlx --timeout 300 --pretty
```

`--from-findings` turns every `file:line[-line]` reference in your findings file into an evidence item (nearby ranges merge, long ranges split, each file must resolve to exactly one path under the `--root` directories, otherwise it exits 4 and tells you to write a longer path such as `src/main.c:370`). Do not hand-pick evidence when you have findings: that is how earlier reviews came back `need-evidence` three times. Limits: 120 lines per item, 6000 bytes per item, 72000 characters in total, 24 items.

Exit codes: 0 completed, 1 backend failed, 2 invalid request, 3 rejected by policy, 4 needs more evidence (exit 4 from the packer: could not bound the evidence; gather narrower excerpts, never send the whole artifact).

The bridge enforces two rules: `--effort` below `xhigh` is ignored for `codex` and `claude` (do not pass `--effort` for them; to allow it set `ADVISER_ALLOW_LOW_EFFORT=1`), and a request whose `id` + `consult_index` the ledger already shows as answered is refused with exit 3 (this check reads the ledger, so `ADVISER_LEDGER=off` turns it off). After `need-evidence` or a disagreement, send a NEW request (new id, `consult_index` + 1), not the same one with reshaped evidence; the 3-round limit then applies.

Live `codex` and `claude` backends need network and a writable home. If a call fails for DNS, auth or filesystem reasons, report that raw error; do not retry the same call unchanged. One retry per backend is built in, and failover keeps the same request id and consult index.

## Use the answer

After every completed review record your disposition: `adopt` or `reject`, with a reason. In **pi** the review-gate extension provides a `review_disposition` tool and enforces it (a run that changed files cannot finish without it). In **Codex** there is no such tool or enforcement: write one plain line `Disposition: adopt|reject — <reason>` before the next step, and follow the rules below yourself, because nothing will stop you.

- `proceed`: adopt, report. `change`: adopt means apply it (write/edit), re-run your verification, then report. `need-evidence` (`input-required`): gather the evidence and run a new round. `stop`: block high-risk work. `rejected`: do not bypass the policy.
- **Reject = challenge, then escalate.** Round 2 goes to the SAME reviewer backend: a NEW request (new id, same `context_id`, `consult_index` 2, only the new evidence, your reasoned disagreement as `executor_position`); omlx runs round 2 at `xhigh` (round 1 is `low`). If the reviewer is persuaded, adopt its new decision. If you still disagree after two reviewer rounds, escalate (round 3) to Codex: `--backend codex`, no `--failover`. **Codex's decision is final** unless you have objective counter-evidence (a failing test, a spec line, a measurement) — then reject with `evidence` and the user decides. Objective evidence always beats any adviser. Do not write to or edit a reviewer's response or canned/answer files; a review that you authored yourself is not a review.
- **Only `proceed` ends the review.** After `change` or `need-evidence`, fix and confirm in the next round (3 rounds in total); once the adviser says `proceed`, apply nothing more and report.
- At most 3 rounds in total (reviewer, reviewer challenge, Codex). If still unresolved, or the data is `restricted` (Codex must not see it), stop and ask the user, listing agreed items, disagreed items, each adviser's decision and the evidence. Do not average advisers.
- If every backend fails, use the declared reversible fallback only for low-risk work and mark the result `UNREVIEWED`; otherwise stop and tell the user.

## Review ledger (dry-run failure statistics)

Every review round is appended to `~/.local/state/adviser-ledger/reviews.jsonl` by the bridge (backend, model, effort, decision, latency, retries, failures; no recommendation text for `restricted` data; mode 600). In pi the review-gate extension also logs each disposition and run end to `dispositions.jsonl`, and tells the bridge who the executor is. In **Codex CLI** the bridge works out the executor itself (it reads `CODEX_THREAD_ID` from the shell environment and takes the model of the latest turn from the session log under `~/.codex/sessions`, giving e.g. `codex:omlx/Ornith-1.5-35B-A3B-MLX-8bit`); there is no disposition tool, so the report infers adopt/reject from the round sequence. `--executor <id>` overrides detection. Codex runs the bridge in a sandbox that cannot reach the network or `~/.local/state`, so run it with escalation; a failed ledger write only warns. `ADVISER_LEDGER=off` disables it, `ADVISER_LEDGER_DIR` moves it. Test backends (`scripted`, `fake`) are flagged and left out of the statistics. Report with:

```text
uv run --script ~/.agents/skills/execute-with-adviser/scripts/ledger_report.py   # --json, --include-test, --dir
```

It counts Codex-confirmed executor errors, reviewer false alarms, flags accepted without arbitration (unverified), reviewer failures and retries, and lists every Codex round. Do not read a single run as a rate.

## Report

Record the trigger, question, decision and whether you adopted it, the backend path (primary → failover), and the evidence. Keep raw execution facts separate from adviser judgment. Details: [protocol](references/protocol.md).
