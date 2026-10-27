---
name: karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, define verifiable success criteria, resolve conflicting patterns, read before writing, verify intent in tests, and fail loud.
license: MIT
---

# Karpathy Guidelines (local fork, extended)

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls. This is a local fork that extends the upstream 4-rule version with 4 additional rules (5–8) found useful in this vault's workflow.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Surface Conflicts, Don't Average Them

**If two patterns contradict, pick one. Don't blend.**

When you find inconsistent conventions in the codebase:
- Pick the version that is more recent, more tested, or more central — and follow it.
- Explain *why* you picked it. State the conflict out loud.
- Flag the other location for cleanup, but don't fix it in the same change (see Rule 3).
- Never silently merge two incompatible APIs / styles / data shapes into a third hybrid that matches neither.

The failure mode this prevents: "averaged" code that looks plausible but matches no existing caller, leaving the codebase with three patterns instead of two.

## 6. Read Before You Write

**Understand the surrounding code before adding to it.**

Before adding new code in an existing area:
- Read the file's exports and the immediate callers.
- Read any shared utility / helper you're about to invoke — not just its signature.
- If the existing structure looks "orthogonal" to your change, that's a warning sign, not a green light. Ask why it's structured that way before working around it.
- For SDK / project analysis in this vault: Grep gives you a location, not an answer — **Read the source file** before drawing conclusions.

The test: Can you explain why the existing code is shaped the way it is? If no, you're not ready to add to it.

## 7. Tests Verify Intent, Not Just Behavior

**A test that can't fail when the business logic changes is wrong.**

When writing or reviewing tests:
- Encode *why* the behavior matters, not just *what* the function returns.
- A passing test on a broken implementation is worse than no test.
- Snapshot tests and "does it return something truthy" assertions are usually intent-free — replace or strengthen them.
- If a test wouldn't catch the bug you're about to fix, write a better test before the fix.

Applies to scripts and tooling too (e.g. `llm-expand`, template substitution): an assertion that the output is non-empty is not the same as an assertion that the output is correct.

## 8. Fail Loud

**"Completed" is wrong if anything was skipped silently.**

When reporting work as done:
- If a step was skipped, say so. Don't smuggle it into a "completed" status.
- If tests were skipped, marked xfail, or didn't run — surface it, don't bury it.
- If a tool returned an error you worked around, mention the workaround.
- If you couldn't verify something (e.g. UI behavior, an external service), say "could not verify X" explicitly — don't claim success.

Default to surfacing uncertainty, not hiding it. The user can't course-correct on a problem they don't know exists.
