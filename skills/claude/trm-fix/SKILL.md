---
name: trm-fix
description: Apply a correction to an llm-wiki 2.0 TRM module note (Markdown and JSON together) or record a pipeline bug fix, from the user's plain-language indication. Use when the user says a sentence, statement, formula, diagram or SDK line in a TRM note is wrong, or after fixing a pipeline bug. The agent fills the record; the user never maintains a table.
---

# trm-fix

The user only says what is wrong, in plain words. You do everything else: find the item, check the PDF, fill the request, apply it, rebuild Markdown and JSON, record it, and report in two or three lines. Never ask the user to fill a table or a line format.

Runs on dreammaker (`~/utilities/llm-wiki2-v2/`); tool source on crossroads `~/tools/utilities/trm-pilot/llm-wiki2-trial/fix.py` (`scp -P 9588` after editing). Read-only commands (`find`, `show`, `check`, `report`) may use `ssh -p 9588 dreammaker`. Commands that change files (`add`, `build`, `bug`) go through the Orca pane with `orca_send.sh` (one-line commands; write the request JSON on crossroads, `scp` it to `/tmp/` on dreammaker, then run).

## Steps for a content correction
1. Work out the module and the run directory (`data/runs/<module>/<tag>`, normally the newest `noteN`; the final-formatted.md the user reads says which). Ask only if it is ambiguous.
2. `fix.py find <note_dir> "<words from the user's sentence>"` to get the key; `fix.py show <note_dir> <key>` to see the text, the cited statements with page and quote, and the judge history.
3. Check the claim against the PDF text yourself (`data/pdf_pages.json`, the cited statements, `statements.jsonl`). If the user gave the right text, find the page and a verbatim quote that supports it. If you cannot find evidence, the action is `flag`; say so.
4. Fill the request JSON: `key`, `action` (replace|delete|add|flag), `type` (WRONG_LOGIC, ADDS, OUTSIDE, WORDING, SOURCE_WRONG, MISSING, UNSURE), `stage` (extract, generate, note, formula, diagram, sdk, plan, judge, repair, render), `text`, `refs` (statement ids), `page`, `quote`, `reason`. Choose type and stage yourself from what the user said and what you found.
5. `fix.py add <note_dir> <request.json>`. It records the correction in `data/runs/<module>/corrections.jsonl`, updates `statements.jsonl` / `note_model.json`, marks sentences that cite a corrected statement as stale (they are repaired and re-judged in the next run), and rebuilds `final.md` and `final-formatted.md`.
6. Report to the user in Traditional Chinese, at most three lines: correction id, what changed (old to new, short), how many dependents are stale, any warnings. If stale items exist, say the next repair round will rewrite them.
7. After a regeneration of the module, run `fix.py check` and re-apply corrections that no longer hold.

## Steps for a pipeline bug fix
After you fix code or config, write a request with `stage`, `symptom`, `cause`, `fix`, `files`, `modules` and run `fix.py bug <note_dir> <request.json>` (appends to `data/runs/bugfix_log.jsonl`). If the fix changes the flow, add one line to `AI學習/llm-wiki2.0/llm-wiki-2.0-trm-analysis-flow.md` (newest first at the top).

## Rules
- Do not invent a quote; copy it verbatim from the PDF text. A statement change without a verified quote is downgraded to `flag` by the tool.
- The user is the authority on content. `claim_check` warnings are shown, not blocking.
- Do not delete statements (citations would break); replace or flag them.
- When five or more corrections share a type and stage (`fix.py report`), tell the user and propose one prompt rule; change a prompt only after the user agrees.
- Fine-tuning data is not built from these records until the user decides; `label_source` for them is `human`.
- No commit or push unless asked. Replies in Traditional Chinese, short, answer first.
