---
name: grill-me
description: Relentlessly clarify an ambiguous plan, design, or decision through a one-question-at-a-time interview, while inspecting discoverable facts and providing a recommended answer for each user decision. Use when Codex needs to turn vague requirements into an executable, testable plan for code, hardware, schematics, architecture, workflows, or other consequential work.
---

# Grill Me

Convert an underspecified idea into shared understanding and an executable plan. Interview sequentially, resolve dependencies in decision order, and do not perform implementation work until the user confirms the resulting plan.

## Operating rules

- Ask exactly one substantive question per turn. Do not bundle independent decisions.
- Ask every user-facing interview question in Chinese, preferably Traditional Chinese. This includes recommendations, alternatives, tradeoffs, decision questions, and the final confirmation question; preserve technical identifiers in their original spelling when needed.
- State the recommended answer first and briefly explain the tradeoff.
- Inspect the environment for facts before asking the user: files, repository state, notes, tool capabilities, schemas, measurements, and current configuration.
- Ask the user only for decisions, preferences, constraints, or intent that cannot be reliably discovered.
- Track decisions explicitly. If a decision changes, identify affected downstream decisions and revisit them.
- Prefer concrete options and examples over abstract questions.
- Keep an unresolved-items list. A plan is not ready while a decision-critical ambiguity remains.
- Do not edit files, call mutating tools, create branches, send messages, or start implementation merely because the interview has produced a plausible direction.
- Before acting, summarize the agreed goal, assumptions, decisions, out-of-scope items, implementation steps, validation gates, and recovery plan; request explicit confirmation.

## Interview workflow

### 1. Frame the objective

Establish:

- desired outcome;
- scope and stakeholders;
- measurable success criteria;
- deadline, lifecycle, or permanence;
- explicit non-goals.

If the objective is unclear, ask what exact outcome must exist when the work is complete and propose a concrete framing.

### 2. Build the fact base

Separate facts from assumptions. Explore facts using available tools instead of asking the user.

For code, inspect project structure, entry points, tests, dependencies, public interfaces, and branch state.

For schematics and hardware, inspect project sheets, pinmux notes, power domains, connectors, net names, footprints, design rules, and validation tools. Check the local knowledge database first when one exists.

If a fact is unavailable, ask whether to measure it, research it, or proceed with an explicit assumption.

### 3. Resolve decisions in dependency order

Use this order unless the domain requires another:

1. Requirements and success criteria.
2. Scope and non-goals.
3. Architecture or partitioning.
4. Interfaces, data flow, pin/net ownership, and compatibility constraints.
5. Risk-sensitive choices and fallbacks.
6. Implementation sequence and checkpoints.
7. Validation, acceptance tests, and rollback.

For each decision, use:

```text
Decision N: <short title>
Recommended: <one concrete choice>
Why: <brief evidence-based rationale>
Alternatives: <material alternatives and tradeoffs>
Question: <one decision question>
```

Wait for the user's answer before asking the next decision question.

### 4. Convert decisions into an executable plan

Specify:

- ordered steps and dependencies;
- exact files, sheets, modules, components, nets, or interfaces to touch;
- invariants that must remain unchanged;
- commands or tools to use;
- validation after each meaningful step;
- milestone checks and stop conditions;
- rollback and recovery actions;
- explicit out-of-scope work.

For schematics, include sheet/component/net ownership and state whether each change affects schematic only, PCB, firmware, or manufacturing. For code, include API compatibility, tests, migration, and deployment implications.

### 5. Confirm before execution

End with a concise shared-understanding summary and ask:

> Do you confirm this plan and authorize implementation?

Remain in interview mode until confirmation. If implementation is requested before confirmation, identify unresolved decisions and ask the next single question.

## Ambiguity handling

- Distinguish unknown facts from user choices: discover the former and ask the latter.
- Resolve coupled decisions from higher level to lower level.
- Translate vague preferences such as “robust” into measurable criteria and ask which criteria matter.
- Surface impacts to PCB, firmware, manufacturing, safety, compatibility, or data migration before requesting approval.
- If new evidence invalidates an earlier decision, pause, explain the conflict, and reopen only the affected branch.
- Do not ask questions whose answers cannot change the plan.

## Completion standard

A completed grill must leave a plan another engineer can execute without guessing. It must identify the target, owner, sequence, interfaces, validation, and recovery path. Phrases such as “implement the design” or “clean up the code” are not executable steps.
