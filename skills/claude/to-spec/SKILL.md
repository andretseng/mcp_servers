---
name: to-spec
description: Turn the current conversation into a spec markdown file — no interview, just synthesis of what you've already discussed.
disable-model-invocation: true
---

This skill takes the current conversation context and codebase/project understanding and produces a spec (you may know this document as a PRD). Do NOT interview the user — just synthesize what you already know.

Adapted for projects with no issue tracker configured: instead of publishing to a tracker with a triage label, this version writes the spec to a markdown file.

## Process

1. Explore the repo/project to understand the current state, if you haven't already. Use the project's own domain vocabulary (register names, control-loop terms, chip/SDK terminology, etc.) throughout the spec, and respect any existing plan/decision docs in the area you're touching (e.g. PLAN_XX notes, ADRs).

2. Sketch out the seam(s) at which you're going to test/validate the feature — e.g. a bench-test gate, a closed-loop acceptance criterion, an interface boundary. Prefer existing seams to new ones, and the highest seam possible. Fewer seams is better; ideally just one across the whole change.

Check with the user that these seams match their expectations before writing.

3. Write the spec using the template below, then save it as a markdown file. If the project has an existing naming convention for plan/spec documents (e.g. `PLAN_XX_<name>.md`), follow it; otherwise ask the user for the target path/filename.

<spec-template>

## Problem Statement

The problem being faced, from the user's (or operator's/system's) perspective.

## Solution

The solution to the problem, at a high level, before implementation detail.

## User Stories / Scenarios

A LONG, numbered list covering all aspects of the feature. Not every project has an end-user actor — behavior-scenario framing is fine too:

`As a <actor>, I want <feature/behavior>, so that <benefit>`

`<actor>` can be an operator, an upstream system, or even "the control loop itself."

<example>
1. As the motor control loop, I want a fault triggered within one electrical cycle of detecting desync, so that the power stage isn't damaged by overcurrent.
</example>

This list should be extensive and cover all aspects of the feature.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Register/data-structure/schema changes
- Interface/protocol contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, register map, schema, timing diagram values), inline it within the relevant decision and note briefly that it came from a prototype/datasheet.

## Testing / Validation Decisions

A list of testing/validation decisions that were made. Include:

- A description of what makes a good test/gate (only test external behavior, not implementation details)
- Which modules/gates will be validated
- Prior art (similar gates/tests already in the project)

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature.

</spec-template>

## Deep modules

Before writing the spec, sketch the seams at which the feature will be tested and look for deep-module opportunities — a lot of functionality hidden behind a small, stable interface. Prefer existing seams to new ones and the highest seam possible, ideally just one across the whole change.

That matters for agentic development: a good interface gives tests something durable to target, so the code underneath can change without the tests moving.
