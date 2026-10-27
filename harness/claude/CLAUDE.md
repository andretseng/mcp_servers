# Assistant Operating Rules (all projects)

## Adviser consult (all projects)

Consult an adviser — do not skip — whenever any of these apply:
1. Before claiming a job/task is finished.
2. When something is uncertain.
3. When you hit an issue after retrying twice.
4. When the user explicitly asks for one.

Use the `execute-with-adviser` skill (`~/.claude/skills/execute-with-adviser/`) to run the consultation: Codex (`gpt-6-sol`, `xhigh`) first, Claude (`opus`, `xhigh`) as same-round failover.

## Source and location priority (all projects)

Applies to architecture, explaining, debugging, researching, and writing code — even when the file path is already known from IDE context or conversation history. A known location is not a bypass.

1. Always search `/home/andre/llm-wiki` first. This is the knowledge vault; use it for prior decisions, architecture notes, and related references.
2. Then inspect the relevant project folder. The normal project root is `/home/andre/workspace_ccstheia`, or the specific project directory under it when one is identified. Also inspect the current project's own source when the session is already inside that project.
3. Then inspect SDK sources under `/home/andre/ti` when the task involves an SDK, chip, driver, or vendor source.
4. Only after the vault, project, and SDK checks, consult other locations or external resources such as datasheets, vendor forums, and web documentation.

For source trees, grep/search for the symbol or identifier first, then read the matching file to confirm before citing or changing it. Do not skip an earlier location merely because a later path is already known.

Notes are not reliable unless they have evidence. You must always challenge a note's conclusion.

A code comment, a variable or macro name, a note, or a document that restates a claim is a claim, not evidence. Evidence is executable code, the original specification or datasheet text, or a measurement. When a conclusion rests only on a comment or a note, mark it [inference] and say what would confirm it.

Project-specific nuances (e.g. line-number offsets between an SDK-master copy and a workspace fork) belong in that project's own context file, not here.

## Coding workflow (all projects)

Invoke/load the `/karpathy-guidelines` skill before proposing or implementing any coding plan, and before writing any code.

## Cross-agent handoff (all projects)

When the user asks you to pick up, continue, or resume work handed off from another agent session (e.g. Codex), check `~/handoffs/latest-from-codex.md` before asking the user to re-explain context. Confirm its `Project` field actually matches the current working directory/task before treating it as context; if it doesn't match, say so explicitly rather than silently using or silently ignoring it. Do not check this file at the start of every session — only when the user's request signals a handoff.

To produce a handoff for another agent to pick up, use the `handoff` skill (`~/.claude/skills/handoff/`) — you may invoke it on your own initiative (user allowed 2026-10-11) when a session pauses on work that must wait or ends with follow-up items; also on `/handoff`. Print the full document inline, not just the path.
