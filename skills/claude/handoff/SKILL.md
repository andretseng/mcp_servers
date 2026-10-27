---
name: handoff
description: Compress the current session into a portable handoff document so a different agent session (Codex, or a fresh Claude session) can pick up the task immediately without re-deriving context. Use when told to hand off, to continue elsewhere, or to write a handoff, or when a session pauses on waiting work or ends with follow-up items.

---

# Handoff

Compress the current session into a portable markdown handoff document so a different agent session — Codex, or a fresh Claude session — can pick up the task immediately, without re-deriving context from scratch.

## Before writing

Determine the purpose of the next session — what it should actually do. Use $ARGUMENTS if given; otherwise ask the user directly and wait for an answer. A handoff document written without a stated purpose degenerates into a full transcript dump, which defeats the point.

If the user's answer is empty or non-committal (they skip the question, say "you decide," or otherwise don't specify), do not keep blocking on it and do not fall back to a transcript dump either — infer a reasonable purpose yourself from the current task/session state and proceed. Mark it clearly as agent-inferred in the Purpose section (e.g. "Inferred by Claude, not specified by the user:") rather than silently presenting it as something the user said.

## Required sections

Write a single markdown document with these sections, in order:

1. **Project** — absolute path of the working directory / vault / project this handoff belongs to (e.g. `/home/andre/llm-wiki`, `/home/andre/workspace_ccstheia/motor`). This lets a reader confirm the handoff is even relevant to what it's being asked to continue, before using it. Never omit it, and never let it be wrong.
2. **Written by / intended for** — that this was produced by Claude Code, who it's meant for (Codex / a fresh Claude session / a named person), and a UTC timestamp.
3. **Purpose** — the next session's actual goal, from the argument or the user's direct answer. If neither was given, state the agent-inferred purpose and label it as inferred, not user-specified.
4. **Context** — only what's needed for that purpose, compressed. Summarize decisions made and why, not a transcript.
5. **Pointers, not copies** — link to existing artifacts instead of repeating their content: vault notes via `[[wikilink]]`, file paths with line numbers, git commit hashes / `git diff` refs, plan files. Never paste a whole file or a long code block that already exists on disk.
6. **Suggested skills** — name specific skills the next session should invoke, drawn only from that environment's actually-installed skill list (e.g. for Codex: `execute-with-adviser`, `grill-me`, `karpathy-guidelines`, `herdr-review`, `ccs-operate`, ...; for Claude: `ask`, `pdf-analysis`, `grilling`, `to-spec`, `execute-with-adviser`, ...). Never invent a skill name — if unsure what's installed on the receiving side, say so rather than guessing.
7. **Open questions / next step** — the single next concrete action.

## Redaction

Before writing, scan the content for API keys, tokens, passwords, and personally identifiable information, and redact them. This document may travel to a different agent and a different sandboxed context — treat it like anything else you'd hand to a third party.

## Output

Write the file to `~/handoffs/<UTC-timestamp>-claude-<slug>.md` (slug: short kebab-case derived from the purpose), then overwrite `~/handoffs/latest-from-claude.md` with the identical content.

After writing, reply with the exact path **and the full document content inline** in your response. The printed content — not just the path — is what lets the user paste this directly into a Codex (or other) session when that session cannot read the path itself, e.g. a sandboxed session rooted in a different trusted project than `~/handoffs/`.

## When this fires

Claude may invoke this on its own initiative (user allowed 2026-10-11) when a session pauses on work that must wait (e.g. a long job still running) or ends with follow-up items, as well as on explicit `/handoff` or when the user says to hand off, continue elsewhere, or pause the session for another agent. It is a document, not a scheduler: it does not make anything run later. Do not fire it just because a session feels long.
