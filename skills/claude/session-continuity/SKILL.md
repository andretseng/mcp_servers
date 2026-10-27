---
name: session-continuity
description: Use when a task runs long (context near full, after a compaction, many tool calls), when you resume work from an earlier session, or when you hand the task to another agent (pi, opencode, Codex, Claude). Keeps a checkpoint file so nothing already read, decided or verified is redone or lost, and defines the handoff format between agents.
---

# Session continuity (long sessions and cross-session handoff)

The harness keeps a progress ledger for you (what you already read, in the context every turn) and writes it to `~/.local/state/agent-checkpoints/auto-<sessionID>.md`. This skill covers what the harness cannot know: your goal, decisions, open items and next step. Do not rely on your memory of the conversation; after a compaction it is gone.

## Task contract (first thing in the findings file)

Before any work on sources, create the findings file with the task contract at the top. Paste the user's request word for word, then list what must be delivered:

```text
## TASK (verbatim)
<the user's request exactly as written>

## DELIVERABLES
- note: <path of the note you must write>
- review: approved (proceed) by <codex for public/private, qwen for restricted>

## Facts
```

The harness injects this into every turn and checks the deliverables (is the note file there, did the adviser approve). After a compaction or in a new session this is the task; the user did not ask a new question. Do not finish with a plain answer while a deliverable is missing: continue the work, or say what is missing. The opencode gate blocks source work until the contract exists (R6).

## Findings file (research phase)

While you read sources, record each fact as you learn it, in `~/.local/state/agent-checkpoints/findings-<sessionID>.md` (the harness ledger in your context shows the exact path). One line per fact: `claim | file:line | verified or [INFERENCE]`. Append with the edit tool; do not rewrite the whole file.

The harness injects this file into your context on every turn, so it survives compaction. In harnesses that enforce it (opencode gate): re-reading a project file whose facts are already in the findings file is blocked (R4), and the vault note cannot be written until the findings file has content (R5). If you need a detail that is not recorded, append one line `NEED: <file> <exact question>` to the findings file, then read only the narrow range you need (each NEED line allows one re-read of that file).

## Checkpoint file

Path: `~/.local/state/agent-checkpoints/<context_id>.md` (create the folder if needed). `context_id` is the same short id you use for the adviser bridge (e.g. `esc32_m0g3507_erpm_chain`). Plain markdown, overwritten in place, these sections:

1. **Goal** — the user's request in one or two sentences, and the data class (public / private / restricted).
2. **Done** — steps finished and verified, each with its evidence (file:line, command, SDK header).
3. **Decisions** — what you chose and why; which options you rejected.
4. **Unverified** — claims you have not checked against source, labelled `[INFERENCE]`.
5. **Review state** — which adviser round ran (request id, backend, decision), what you adopted or rejected, what is still open. Copy this from the adviser ledger, never from memory.
6. **Next step** — the single next concrete action.

Write it at these moments: after the research phase, before you start writing the deliverable, after each adviser round, and whenever you are told the context is getting full. A checkpoint is a few lines per section, not a transcript. Pointers over copies: cite paths, never paste files.

## After a compaction or in a new session

1. Read the checkpoint for your `context_id` first. Then read the harness ledger in the system prompt.
2. Re-read only the files the next step needs. Do not repeat research that the checkpoint lists as Done.
3. Never re-run an adviser round that the ledger shows as completed for this `context_id`; apply its decision and continue.
4. If the checkpoint and the files on disk disagree, trust the files, fix the checkpoint, and say so.

## Handing off to another agent

Use the existing handoff format (`~/handoffs/`, sections: Project, Written by / intended for, Purpose, Context, Pointers, Suggested skills, Open questions / next step). Build it from the checkpoint plus the adviser ledger so a different agent can continue without your conversation. Redact keys and personal data. Distinguish confirmed facts, user-provided facts, your assumptions and unverified guesses. A handoff does not replace the vault note (verified reusable knowledge goes to the vault) or memory (preferences).

## Failure rules

If you cannot write the checkpoint, say so; do not pretend it was saved. Never delete or overwrite another session's checkpoint. Do not use a checkpoint to skip the research rules or the required adviser round.
