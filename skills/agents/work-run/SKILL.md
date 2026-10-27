---
name: work-run
description: Run an "analyze X and write a note" job on a local model in ONE gated opencode session with a thin harness. The model gets the user's request, the research path (vault, project, SDK) and the note format; the harness blocks only on order and scope; by default nothing leaves the machine (Codex editing or review is opt-in). Use for any such job, whatever the topic.
---

# work-run (thin)

No planner session, no scope file, no stages, no index. Earlier designs (task cards, staged plan with index and triggers) are archived in `~/.agents/.archive/task-cards-20261021` and `~/.agents/.archive/work-run-plan-20261021`; the reasons are in the vault note `AI學習/local_llm/階段式計畫（staged plan）提案 …`.

## Flow
1. `run`: ONE work session (Ornith) gets the request word for word, the research path and the note rules. It builds the draft `<workdir>/draft.md` with write and edit; nothing is checked while it writes. It may run the check command itself.
2. When the model stops, the harness checks the draft (frontmatter, required sections, Provenance table with its quotes, citations in the body, code blocks) and publishes it to the vault path. The check is a report, not a gate. If no draft exists, the same session gets a nudge (`--session`).
3. **Final edit (opt-in, `--final-editor codex`; the default is `none`):** Codex (`gpt-6-sol`, effort high, read-only sandbox, one call, `finaledit.py`) checks the note against the sources, corrects it and rewrites it in Traditional Chinese, adds a `## 編修紀錄` listing every change, and may not add a fact it has not confirmed in a source. The script then repairs what an editor rewrites without changing a fact (relative source paths become absolute, `前提：S1、S2` becomes `inference: S1, S2`) and runs the note check. If errors remain, Codex gets ONE repair round with the listed errors. An edit that still has errors is published only when it has fewer errors than the local note; otherwise the local note stays and `run.json` says why. The check result is appended as `## Harness check`. The local note is kept as `<workdir>/note.ornith.md`, Codex's as `note.edited.md`. If Codex fails, the local note stays. Restricted material is never sent to Codex (public and private only).
4. Review (opt-in, `--review`; off by default): with `--final-editor none --review` the adviser loop runs (up to 3 rounds, `--review-rounds N` changes it; the problems go back into the SAME work session). A point about missing evidence sends the executor to the sources first: it may mark a claim unverified only after it looked and found nothing, and the reviewer is told to say where to look instead of asking for a downgrade. With the final edit on, the loop is skipped; `--review-after-edit` adds one read-only adviser round whose problems become open items. **Field flow (decided 2026-10-23): no flag at all, so nothing goes to Codex; Claude/Codex (public data) or Qwen (confidential) are called by the user on demand.**
5. `sessiontrace.py` keeps the full trace (also compaction and output-limit hits; `--handoff` shows what the model saw and did after a compaction); `audit.py` re-checks the order from the gate log.

Sampling: the work session runs in `<workdir>/work/`, whose project `opencode.json` (key `providers.<p>.models.<m>.body.temperature`, written by `run`, default 0.3) lowers the temperature; thinking is `#low`. opencode reads the project directory from `$PWD`, so `run_agent` sets it.

## Commands
```bash
W=~/.agents/skills/work-run/scripts
GATE_THINKING=1 python3 $W/workrun.py run --question "<user request, word for word>" --note <vault path>.md \
  --root <project> --root <sdk> --root /home/andre/llm-wiki --deny <excluded source> --workdir <workdir>
python3 $W/audit.py --workdir <workdir>
python3 $W/sessiontrace.py --workdir <workdir>
```
`GATE_THINKING=1` writes the model's thinking to `<workdir>/logs/live.log` (follow it with `tail -F`). Tests in `scripts/`: `python3 -m unittest test_notecheck test_workrun`, `node test_gate.mjs`, and `test_sampling_integration.py` (needs opencode; a fake local server checks the temperature).

## The gate (opencode plugin `research-gate.js`, env `GATE_FILE`)
Blocks only: the guidelines (`karpathy-guidelines`) not read first; a project or SDK file before any vault access; a read outside the roots or of an excluded source (`--deny`); a write or edit of anything but the draft; dangerous shell (redirections, command substitution, in-place edits, `find -exec`), any `python3` except the check command, and any command outside the read-only list  Shell commands are split like the shell does (a `|` inside quotes does not split); `cd` into an allowed root is fine.
Reminder only: when a call names a module (COMP, TIM, ADC, DMA, UART, SPI, I2C, GPIO, OPA, DAC) the harness finds its vault note (`專業領域/<chip>/<module>…模組分析….md`), lists it in the next ledger until the model has read or searched it, and logs `advice: module-note:<module>` in `gate.jsonl`.

## The note format (checked by `notecheck.py`, as a report)
Frontmatter `title`, `date`, `tags`; a Summary; one section per part of the answer; "Known limits and open questions"; "References"; the last section is `## Provenance`, a table `| id | status | source | quote | claim |`. `verified`: source `<absolute path>:<line>` and the exact text of that line as quote (backticks around it or around each line, and `…` between pieces, are tolerated); `inferred`: source `inference: S1, S2`, or a `<absolute path>:<line>` with its quote (checked like `verified`); `unverified`: free. Aim for 80% of the body sentences citing a claim like `[S3]`; this is only a warning in the report after the session (the model's own `check-note` does not show it). Other warnings in the report: a `verified` claim whose source is a note (.md; a note is a claim, not evidence), an unpaired code fence, review point ids (`R1-2`) left in the text, Simplified Chinese characters; code blocks marked ` ```c src=<path>:<a>-<b> ` must equal those lines.

## Not done
The shell whitelist review; the module table covers TRM notes only (project notes such as a pinmux note are found by the vault grep the prompt asks for); pi and Codex ports; macro (`#define`, `##`) lookup; `inferred` premises are checked for existence only; Codex as the last editor when the result is not good enough (decided, not built).
