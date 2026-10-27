---
name: token-usage
description: Analyze a Claude Code session's token usage and cost per actor (orchestrator, adviser, worker, researcher, bulk-reader and other subagents), including cache read/write economics, cold-start size, and skill attribution. Trigger /token-usage [session-id]. Use when the user asks who consumed the most tokens, why a session was expensive, or how to reduce token cost.
---

# /token-usage — per-session token & cost breakdown

Read-only analysis of Claude Code transcripts under `~/.claude/projects/`.
One session = the main transcript (`<session-id>.jsonl`) + its subagent
transcripts (`<session-id>/subagents/agent-*.jsonl` with `.meta.json`).

## Step 1 — pick the target session

- User named a session id/prefix or path → pass it through.
- "current session" (or nothing said) → the current session UUID is the last
  path component before `/scratchpad` in the scratchpad directory listed in
  the system prompt. Use that as the session argument.
- "latest/last session" → use `--latest` (optionally
  `--project <slug-dir>` where slug = project path with `/`→`-`).

Note: analyzing the *current* session undercounts slightly — the transcript
is still being appended and the final turns are missing.

## Step 2 — run the analyzer

```bash
python3 ~/.claude/skills/token-usage/scripts/analyze_tokens.py <session|--latest> [--json]
```

The script dedupes API requests by `requestId` (one API call is logged as
multiple assistant entries), splits cache writes into 5m/1h TTL, and prices
each request by its own `message.model`. Paste the table verbatim.

## Step 3 — interpret (map findings to the collaboration playbook)

Give a ranked "where the tokens went" verdict, then at most 3 concrete
improvements. Heuristics, in the order they usually matter
(see `feedback_fleet_model_tier_token_economics.md` + playbook `10-model-dispatch.md`):

1. **Main-context sprawl** — orchestrator usually dominates (its context is
   re-sent every turn). If `max-ctx` is large (>150K) and request count is
   high → recommend `/clear` at task boundaries; check whether big file dumps
   entered the main context instead of a subagent (hard rule #1).
2. **Cache hit rate** — healthy sessions are >90%. Lower means >5-minute idle
   gaps (5m TTL expiry) or prefix invalidation (config/tool changes
   mid-session). `cache-wr` sums are money: writes cost 1.25×/2× input price.
3. **Cold start per spawn** — each subagent's `coldstart` column is the fixed
   overhead of that spawn (system prompt + agent def + CLAUDE.md subset +
   tool schemas). Flag spawns whose *total new tokens ≈ cold start* — the
   task was too small to justify a spawn (should have been inline or batched).
   Compare agent types: `general-purpose`/`claude` cold start (~20K) vs lean
   agents (bulk-reader/worker ~7-11K) — confirm the cheap agent was used.
4. **Model tier vs blast radius** — check each actor's model against the
   blast-radius rule (mechanical leaf=haiku, judgment=sonnet, consult=opus).
   An opus worker doing mechanical extraction, or a haiku verifier, is wrong
   in both directions.
5. **Skill attribution** — the per-skill table shows which skill invocations
   were expensive in the main context; heavy skills are candidates for
   dispatch-to-subagent instead of inline execution.

## Step 4 — persist a vault report

After presenting the analysis in the conversation, write (or update) a report
note in the vault so findings accumulate over time:

- Path: `$LLM_WIKI_PATH/AI學習/token-usage/token-usage-<session-8char-prefix>.md`
  — resolve `$LLM_WIKI_PATH` via Bash. If the env var is unset (running
  outside the vault setup), skip this step and tell the user why.
- One note per session: if the file already exists (re-analysis of the same
  session), update it in place — never create a duplicate.
- Frontmatter: `title: token-usage 報告 <session-prefix>`,
  `date: YYYY-MM-DD` (analysis date), `tags: [token-usage, session-report]`,
  `source: <full session id>`.
- Body, in order:
  1. the analyzer table **verbatim** in a code block,
  2. the ranked "where the tokens went" verdict,
  3. the ≤3 recommendations,
  4. anomalies worth tracking across sessions (e.g. near-empty spawns,
     cache-expiry losses),
  5. wikilinks to related notes ([[fleet-model-tier-token-economics]],
     [[token-usage]] skill mirror, and any session-specific notes).
- Keep the honesty rules below: dollar figures labeled as list-price estimates.

## Honesty rules

- Costs are **estimates at list price** (pricing table embedded in the
  script, sourced from the claude-api skill, cached 2026-06-24; cache
  read=0.1×, write 5m=1.25×, 1h=2× input). Actual billing (subscription,
  intro pricing, fallbacks) may differ — say so when quoting dollars.
- Numbers come from transcript `usage` fields — they are real measurements,
  not guesses. But "memory/CLAUDE.md share of cold start" cannot be separated
  from the transcript; if asked, estimate from file sizes and label it
  UNVERIFIED estimate.
- If the pricing table looks stale (unknown model names in the output),
  refresh it from the claude-api skill before quoting costs.
