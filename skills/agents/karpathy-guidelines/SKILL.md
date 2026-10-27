---
name: karpathy-guidelines
description: Apply disciplined, minimal, verifiable coding practices when analyzing, explaining, tracing, writing, reviewing, debugging, or refactoring code (including tracing a signal or data path through source code); use it to surface assumptions, define success criteria, resolve conflicting conventions, read before writing, test intent, and report failures honestly.
---

# Karpathy Guidelines

Apply these rules to coding, code review, debugging, refactoring, and implementation planning. Bias toward caution over speed for non-trivial work; use judgment for trivial tasks.

## 1. Think Before Coding

- State assumptions explicitly. If uncertain, ask rather than guess.
- If multiple interpretations exist, present them instead of choosing silently.
- Identify simpler approaches and push back when warranted.
- If confused, stop and name what is unclear.

## 2. Simplicity First

- Write the minimum code that solves the request.
- Add no speculative features, abstractions, flexibility, or configurability.
- Do not add handling for impossible scenarios.
- If the solution is much larger than necessary, simplify it.

## 3. Make Surgical Changes

- Touch only what the request requires.
- Preserve adjacent code, comments, formatting, and existing structure.
- Do not refactor unrelated or working code.
- Remove only imports, variables, or functions made unused by your own change.
- Mention unrelated dead code without deleting it.

## 4. Define and Verify the Goal

- Translate the request into concrete success criteria before acting.
- For a bug, reproduce it with a test before fixing it when practical.
- For validation, test invalid inputs as well as valid ones.
- Continue until the success criteria are verified, or clearly report what could not be verified.

## 5. Surface Conflicts; Do Not Average Them

- When conventions or APIs conflict, choose the more recent, tested, or central pattern.
- Explain the choice and identify the conflicting pattern.
- Do not silently merge incompatible patterns into a new hybrid.
- Leave unrelated cleanup for a separate change.

## 6. Read Before Writing

- Read the target file's exports and immediate callers before modifying it.
- Read shared utilities and helpers completely before invoking them.
- Treat an apparently orthogonal existing structure as a reason to investigate, not work around.
- For SDK or project analysis, search for a location, then read the source file before drawing conclusions.

## 7. Make Tests Verify Intent

- Test why the behavior matters, not only what value a function returns.
- Ensure a test would fail if the relevant business logic regressed.
- Strengthen truthy, empty-output, and snapshot-only assertions when they do not encode intent.
- Apply the same standard to scripts and tooling: non-empty output is not proof of correct output.

## 8. Match the Codebase's Conventions

- Follow local naming, structure, interfaces, formatting, and error-handling conventions.
- Prefer conformance over personal taste.
- If a convention appears harmful, surface the concern instead of silently forking the style.

## 9. Fail Loud

- Do not report completion if a step was silently skipped.
- Report skipped, xfailed, or unrun tests.
- Report tool errors and workarounds.
- Explicitly state when behavior, UI, external services, or another condition could not be verified.

## 10. Checkpoint After Significant Steps

- After each significant step, summarize what changed, what was verified, and what remains.
- Do not continue from a state you cannot describe.
- If you lose track of the state, stop and restate it before proceeding.

## 11. Use the Model for Judgment, Not Deterministic Mechanics

- Use model reasoning for classification, drafting, summarization, extraction, and other judgment calls.
- Use code or deterministic tools for routing, retries, validation, transformations, and other mechanical operations.
- Prefer a small deterministic check over repeatedly reasoning through a mechanical task.

## 12. Respect Token Budgets

- Treat per-task and per-session token budgets as hard limits, not suggestions.
- If approaching a budget, summarize the current state and continue in a fresh context when possible.
- Surface any budget breach instead of silently overrunning it.
