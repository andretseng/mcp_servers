---
name: teach
description: Teach the user a chip/module topic (or any other topic) as a stateful, mission-driven course in a dedicated ~/teach/ workspace — vault-first sourcing, hands-on verification over quizzes, and confirmed write-back of durable insights into existing llm-wiki notes. Use when explicitly asked to teach, tutor, or run a learning course on a topic.
disable-model-invocation: true
argument-hint: "What would you like to learn about?"
---

The user has asked you to teach them something. This is a stateful request — they intend to
learn the topic over multiple sessions.

This is a vault-adapted fork of Matt Pocock's `/teach` (`mattpocock/skills@321658273cb1`,
`skills/productivity/teach/`). See `AI學習/skills/teach.md` in the vault for the full list of
deviations from upstream and why each one was made.

## Workspace Root Resolution

Never treat the invocation directory as the workspace. Resolve the workspace root explicitly,
every time `/teach` runs, before touching any other file:

1. Try chip/package classification: run the exact algorithm in
   `/home/andre/llm-wiki/.claude/skills/pdf-analysis/references/sdk-discovery.md` Sub-step A
   against the stated topic (glob `/home/andre/ti` package dirs, substring-match against the
   topic, latest-version-wins on multiple matches).
2. **Match found** → this is a chip/module topic. Workspace root = `~/teach/<chip>/`, where
   `<chip>` is the same label `專業領域/<chip>/` uses (e.g. `MSPM33`, `AM13Ex`).
3. **No match** → this is a non-chip topic (language, yoga, a cert, a framework, ...). Skip chip
   discovery entirely. Derive a deterministic slug instead: lowercase the stated topic, collapse
   runs of whitespace/punctuation to a single `-`, strip leading/trailing `-` (e.g. "Japanese
   formal register" → `japanese-formal-register`). Workspace root = `~/teach/<topic-slug>/`.
4. **Collision/reuse**: if the resolved workspace root already exists, reuse it — read its
   existing `MISSION.md`/`learning-records/` and continue that course. A second `/teach` call for
   the same chip or topic is a continuation, not a new course.

Everything below refers to paths relative to this resolved root, never to
`~/.claude/skills/teach/` itself. Upstream has a known bug (mattpocock/skills#377) where relative
paths like `./lessons/` get resolved against the skill's own install directory instead of the
workspace — resolving the root explicitly by this rule closes that off by construction.

## Teaching Workspace

The state of the user's learning lives in the resolved workspace root, in these files:

- `MISSION.md`: why the user is learning this. Grounds all teaching. Format:
  [MISSION-FORMAT.md](./MISSION-FORMAT.md).
- `RESOURCES.md`: vetted sources — see "Sourcing Knowledge" below for how this fork populates it.
  Format: [RESOURCES-FORMAT.md](./RESOURCES-FORMAT.md).
- `GLOSSARY.md`: the canonical terms for this topic. Restored in this fork — upstream's
  `SKILL.md` stopped linking it even though the format file still ships
  (mattpocock/skills#559). Format: [GLOSSARY-FORMAT.md](./GLOSSARY-FORMAT.md).
- `MODEL.md`: the teacher's concept map of the topic — the concepts and how they fit together, the
  design logic behind them, which diagram teaches each, and a coverage ledger. Built from primary
  sources before the first lesson. Format: [MODEL-FORMAT.md](./MODEL-FORMAT.md).
- `./reference/*.html`: compressed cheat sheets, algorithms, syntax — the raw units of learning,
  designed for quick reference.
- `./learning-records/*.md`: what the user has demonstrably learned. ADR-style, evidence-gated.
  Titled `0001-<dash-case-name>.md`, incrementing. Format:
  [LEARNING-RECORD-FORMAT.md](./LEARNING-RECORD-FORMAT.md).
- `./lessons/*.html`: one self-contained HTML lesson each, tightly scoped, tied to the mission.
- `./assets/*`: reusable components shared across lessons (stylesheet, verification/quiz widgets,
  simulators, diagram helpers).
- `NOTES.md`: your own scratchpad on this learner's preferences.

All of the above are workspace state, free to write without confirmation — they're
scratch/intermediate by design (see "Writing Back to the Vault" for the one exception).

## Philosophy

To learn at a deep level, the user needs three things:

- **Knowledge**, captured from high-quality, high-trust resources
- **Skills**, acquired through highly-relevant interactive lessons devised by you, based on the
  knowledge
- **Wisdom**, which comes from interacting with other learners and practitioners

Never trust your parametric knowledge — ground every claim in `RESOURCES.md`. Some topics need
more skills than knowledge (yoga); some need more knowledge (theoretical physics).

### Fluency vs Storage Strength

Split between two types of learning:

- **Fluency strength**: in-the-moment retrieval of knowledge
- **Storage strength**: long-term retention — the real goal

Fluency can give an illusory sense of mastery, but storage strength is the actual goal. Design
lessons for storage strength via desirable difficulty:

- Retrieval practice (recall from memory)
- Spacing (distributing practice over time)
- Interleaving (mixing related topics — skills practice only)

## Sourcing Knowledge (vault-first, tiered fall-through)

This fork overrides upstream's "search the web for high-trust resources" default. When
populating or refreshing `RESOURCES.md`'s Knowledge section:

**Chip/module workspace** (Workspace Root Resolution found a chip match) — walk these tiers in
order. **An empty tier is not an error — continue to the next one, don't stop:**

1. **Tier 1 — vault**: search `專業領域/<chip>/` for a `*模組分析 (...).md` note covering the
   module/topic named in `MISSION.md`. Found → cite it (wikilink) plus its underlying TRM PDF
   page range as the primary source; this alone is sufficient to start teaching.
2. **Tier 2 — SDK**: regardless of Tier 1's result, also check `/home/andre/ti` using the same
   header/example discovery `sdk-discovery.md` Sub-steps A/B/C already do, for the driverlib
   calls or example code the mission's skills will require.
3. **Tier 3 — external**: only once Tier 1 + Tier 2 together leave a gap the mission actually
   needs (no vault note yet, or the note doesn't cover a sub-topic the learner asked about) —
   search external sources (TI E2E, app notes), and label every such entry `[External]` in
   `RESOURCES.md` so it's never mistaken for vault/SDK ground truth.

If Tier 1 finds nothing for a chip topic, say so plainly to the user and mention that
`/pdf-analysis` could produce the missing note — but do not invoke it yourself; `/teach` only
reads vault notes, it never triggers the PDF pipeline.

**Non-chip workspace**: Tier 1/2 don't apply — go straight to external sourcing as upstream does.

## Division of Labor: Concepts Here, Detail in the Vault

The vault module note (and the TRM behind it) is the **detail reference**: every register field,
every policy value, every table row. `/teach` is **not** a second copy of it. A lesson's job is to
make the learner *understand the concepts* — what the parts are, how they fit, why the design is
the way it is, and how to think about choosing — so that when they open the detail reference it
makes sense. Do not paste or retype tables, register lists or exhaustive value-by-value
enumerations into a lesson; link to the vault note section instead.

Skin-deep and detail-dump are both failures. Skin-deep is naming parts and defining labels.
Detail-dump is reproducing the reference. The target between them: **a clear conceptual model,
shown visually, with the reasoning in plain language.**

## The Concept Map (before any lesson)

Before the first lesson of a unit, build that unit's entry in `MODEL.md` (see MODEL-FORMAT.md)
by reading the primary source: the **concepts** in the unit (at concept level, *all* of them —
e.g. every operating mode appears, so the learner never gets a partial picture), how they relate
(states and what moves between them, what depends on what), the **design logic** (why several
modes/policies/paths exist, what each trades), the diagram that will teach each, and a link to
the vault section holding the detail. Then plan lessons from it in dependency order and tell the
user the plan.

A unit stays open until each concept is taught and the learner can reproduce the picture and the
reasoning (sketch the diagram, explain the why, choose for a scenario). If a unit has five
operating modes, a state machine and per-mode policies, then "PMU supplies power" is not a lesson
on it; the lessons are the mode map, why there are several, how the system moves between them,
and why a different policy suits each mode. Use a *series* of lessons for a large unit.

For non-chip topics the same applies with the domain's own structure (e.g. the cases of a grammar
rule and why the rule exists; the phases of a protocol and what each guards against).

## Course Structure: Mirror the Source

Organize a module course the way the reference organizes the module, so what the learner builds
here maps one-to-one onto the manual they will later open (pattern borrowed from the C2000
Academy, `/home/andre/ti/c28x_academy_1_03_00_00`: one page per module, sections in the
reference manual's own order, figures first, a worked example, an end-to-end summary, resources).

- **Hub + sub-lessons.** A source chapter/module gets one **hub lesson** (the chapter intro and
  overview section: what the submodules are, how they connect, a diagram, a map of the chapter
  with each section's lesson and status). Each major source section becomes a **sub-lesson** the
  hub links to.
- **Name by source section.** Sub-lessons live in `./lessons/<hub-number>-<slug>/` and are named by
  the source's own section number, e.g. `2.2-pmu.html`, `2.1.2-operating-modes.html`. The
  learner can say "I'm in TRM §2.2" and find the lesson. Sections not yet written appear on the
  hub as "planned", never silently missing.
- **Same section order as the source** inside a sub-lesson, so the lesson can be read beside the
  manual. Add the teaching layers (why, choose-for-scenario, where to dig) on top of that order,
  not instead of it.
- **Page anatomy**: one-paragraph purpose → a "not every device has every feature — check your
  datasheet" note where the source says so → figure-driven sections in source order → a worked
  example or scenario → an end-to-end summary figure when sections interact → resources.
- **Hands-on is separate.** Labs/bench exercises are their own lesson, not mixed into the concept
  lessons.

## Lessons

A lesson is the main thing you produce: one self-contained HTML file, saved to `./lessons/` and
titled `0001-<dash-case-name>.html`, incrementing.

A lesson should be **beautiful**, with clean, readable typography and layout — the user will
return to these later. Think Tufte.

Scope each lesson to **one skill**, not one label. Narrow scope is what keeps working memory
manageable — brevity is not. By default a lesson is a 20–40 minute sitting (shorter if the user asks for quick lessons — cut
scope, never the Depth Contract items) that takes the user from not being
able to do one concrete thing to being able to do it, tied to the mission, in the user's zone of
proximal development. A 5-minute "overview card" that names parts and defines terms is not a
lesson; it is the skin-deep failure this section exists to prevent. If possible, open the lesson
file for the user by running a CLI command.

### Depth Contract

Every lesson must contain all of the following. If any is missing, the lesson is not done. Items
7–8 apply whenever the lesson's subject has states, modes, policies or phases:

1. **Mechanism** — a causal chain that explains *why* it works, not a list of parts. The reader
   should be able to predict behavior in a case the lesson never showed.
2. **Worked example with concrete values** — a traced scenario through the concept model, with
   real names and the numbers that matter taken from the primary source (see Knowledge). "The PMU supplies
   power" is a label; "VDD falls below the BOR threshold → reset asserted → …" with the source's
   actual threshold names/values is a worked example.
3. **The wrong mental model** — the most likely misconception, stated plainly, and why it fails.
4. **A failure mode** — one real way this goes wrong (erratum, ordering requirement, common
   misconfiguration, observable symptom), sourced. If the source documents none, say so, note it
   in `RESOURCES.md` Gaps, and instead give a prediction derived from the mechanism, explicitly
   labeled "derived, not sourced" — never present it as a documented fact.
5. **A produce-first task** — see "Skills — Feedback Loop". The user generates something before
   seeing the answer. It must use a **new case** (different values/conditions from the worked
   example) that requires applying the mechanism; plain recall of a definition does not count.
   The expected answer stays withheld until the user has committed to theirs.
6. **A transfer question** — one question about a case the lesson did *not* cover ("what changes
   if X?"), answerable only with the mechanism, likewise answered before it is revealed.

7. **The concept model, as a diagram** — the structure the learner must hold in their head, drawn
   as a self-contained HTML/SVG block diagram, state diagram, flow or timeline (interactive when
   it helps: click a mode to see what is on, off and kept; step a transition). The first lesson of a
   unit shows *every* part of the unit (e.g. every operating mode) and the labeled transitions
   between them; later lessons may zoom into one part of that map but keep it in context. It shows
   the parts at concept level and the arrows that matter, each labeled with its trigger
   or condition in plain words. It is a picture of the idea, not a transcription of the TRM
   table. Row-level detail (registers, every value) is linked to the vault note, not copied.
8. **Why it exists, in plain language** — for each concept, mode and policy *taught in this
   lesson*: the situation that
   forces it, why one general-purpose version would not do, what you gain and give up by choosing
   it, and when you would pick it. Write for a smart newcomer: lead with a concrete scenario
   ("a battery sensor that wakes once a minute…"), then the choice, then the reason. For policies
   in particular, give a plain-language comparison of *why a different policy suits a different
   mode* (the trade each side of the choice makes), not just what the policy value does — without
   enumerating every value or combination. Use the source's numbers where they
   sharpen the argument, in a sentence, not a table. Every rationale and "what breaks" claim must
   cite source evidence or be labeled "derived, not sourced"; if neither is supportable, say the
   rationale is unknown rather than asserting design intent.

Explanatory text should carry the reasoning and the worked example in full, in plain prose around
the diagram. A lesson whose body is mostly bullet-point definitions fails the contract
(skin-deep); so does one that is mostly a copy of the vault note's tables (detail-dump).

### The 30-Minute Orientation Lesson (default for a new module)

When the learner is new to a module, the first lesson is a timeboxed orientation that leaves them
with the picture, the why/how, and a map of where to dig. Put the time budget on each section.
Default arc (adjust minutes, keep the order):

1. **The one idea (≈3 min)** — the design problem in a scenario, and the one strategy that answers
   it. Name the few "knobs" and what each one controls.
2. **The picture (≈6 min)** — the interactive concept diagram of the whole module (every mode/part,
   labeled transitions), click-to-inspect. The learner sees the whole before any part.
3. **Why each part exists (≈6 min)** — walk the diagram as a ladder of tradeoffs: each step gives
   up one thing to gain another. Name the cost of each, not just the label.
4. **Policy/option logic (≈7 min)** — why different settings suit different situations, as a
   compact visual (ladder strips) plus one stated rule for choosing. Mark inferences as
   "derived, not sourced".
5. **The mechanism that makes it work (≈4 min)** — how the design handles the hard case (e.g.
   bursts out of deep sleep), plus the one sourced trap. If the source contradicts itself, say so.
6. **How you use it (≈2 min)** — the few steps to invoke it, and one SDK example to look at.
7. **Your turn (≈2 min to read; the learner's reply time is outside the 30)** — scenario tasks and a transfer question the learner answers
   *before* seeing the key. Keep the key out of the lesson file; check their reply against the
   source in the conversation.
8. **Where to dig next (always)** — a "question → where to look" table naming the exact TRM
   section/page, vault section, SDK file, or datasheet table. State plainly what the primary source
   does *not* contain (e.g. current/latency numbers live in the datasheet).

This lesson is both complete as a picture and honest about what it left out; "I don't yet know X"
becomes a pointer, not a gap.

### Diagrams

Draw static figures with the `diagram-design` skill (`/diagram-design`): it fixes the visual type
(architecture for module maps, state machine for sequences like reset/boot, etc.), the connector
rules and the accessibility contract. State the chosen type and what you cut before drawing,
embed the result as inline SVG in the lesson, and run its `scripts/self_check.py` on the diagram
file. Keep the click-to-inspect component in `./assets/` for diagrams that need interaction (it is
not a substitute for a drawn figure). One diagram, one idea; split anything past its node budget.

### Writing for the Learner

- Concept first, then the diagram, then the reasoning, then the choice. Put the picture before the
  detail it summarizes.
- Prefer a diagram over a table and a table over a paragraph of enumeration. Reuse the diagram
  component from `./assets/` across lessons so the picture stays consistent.
- Plain words and one analogy or scenario per concept; keep terms in English per the glossary
  once introduced. Define a term by what it is for, then name it.
- Numbers only where they make the argument concrete. Everything finer goes to a link into the
  vault note ("details: <note> §x").

### Self-Audit Before Delivering

Before telling the user a lesson is ready, check it against these and fix what fails:

- Could a learner pass an exam question on this lesson that the vault note's summary alone does
  not answer? If no, it is too shallow — deepen it.
- Does every concrete value (number, register, address) trace to a cited primary-source page,
  figure, or SDK file? Cite it inline. A value you cannot cite does not go in.
- Is the produce-first task something that can be wrong? If any answer the learner gives is
  "correct", it is not a task.
- **Coverage (concept level)**: list the modes/states/paths the primary source defines for this
  slice. Does the diagram show every one? A missing mode gives the learner a wrong picture — add
  it. Does it *not* reproduce register-level detail? If it does, cut it and link the vault.
- **Why**: pick any mode/policy/state in the lesson and ask "why not just one?" and "what breaks
  if it's removed?". If the lesson cannot answer from its own text, add the rationale.
- **Reconstruction**: after the lesson, could the learner sketch the diagram from memory and say
  why each mode/policy exists and when to choose it? If the lesson gives them nothing to
  reconstruct from, it is incomplete.
- **Plain language**: read the lesson as a newcomer. Any paragraph that is a jargon list or a
  table copy gets rewritten as scenario → choice → reason.
- Is the file name accurate for what the lesson now teaches?

Each lesson should link via HTML anchors to other lessons and reference documents, recommend a
primary source to read or watch, and contain a reminder to ask follow-up questions — you are
their teacher and can clarify anything unclear.

## Assets

Lessons are built from reusable **components**, stored in `./assets/`: stylesheets, quiz/
verification widgets, simulators, diagram helpers, and anything else a second lesson could
reuse.

Reuse is the default, not the exception. Before authoring a lesson, read `./assets/` and build
from the components already there. When a lesson needs something new and reusable, write it as a
component in `./assets/` and link to it; never inline code a future lesson would duplicate.

A reusable **mode/state diagram component** (click a node to see what is on/off/kept; step a
transition) is the second one a modelled topic earns; every lesson on that topic draws with it.

A shared stylesheet is the first component every workspace earns: every lesson links it, so the
lessons look like one consistent course rather than a pile of one-offs.

## The Mission

Every lesson should be tied into the mission — the reason the user is interested in learning
about the topic.

A mission is only usable if it names an **observable output**. Two shapes qualify:

- **Build/fix/decide**: something the user will build, fix, pass or decide ("bring up the ADC with
  DMA on my board", "debug why BOR resets the bench", "pass the exam on …").
- **Orientation**: "sketch the picture, choose for a scenario with a stated reason, and name where
  to look for each next question." This is observable and testable, so a new-to-the-module learner
  may legitimately start here (see "The 30-Minute Orientation Lesson").

A bare "understand X", "build a mental map" or "get an overview" with neither shape is not a
mission — treat it as unanswered and keep interviewing: what will they do with it, on what
artifact, by when, what would they have gotten wrong without it? Do not write `MISSION.md` or teach until the mission
passes this test. "Out of scope" lists topics to defer; it must never be used to cap how deep the
in-scope topics go.

If the user is unclear about the mission, or `MISSION.md` is not populated, your first job is to
question the user on why they want to learn this. Failing to understand the mission means
knowledge acquisition isn't grounded in real-world goals, lessons feel abstract, and there's no
way to judge what the user should do next.

Missions may change as the user develops skills and knowledge. This is normal — update
`MISSION.md` and add a learning record to capture the change. Confirm with the user before
changing the mission.

## Zone Of Proximal Development

Each lesson, the user should always feel challenged "just enough." If the user hasn't specified
an exact thing to learn, figure out their zone of proximal development by reading their
`learning-records`, and teaching the most relevant thing that fits, given the mission.

Locate each lesson on this depth ladder and state the rung in the lesson header:

1. **Orient** — where it sits and what it is for (a map)
2. **Model** — the concept structure and behavior as a diagram: all modes/states, what moves
   between them and why, and how policies fit
3. **Rationale** — why each exists: the forcing problem, rejected alternatives, tradeoffs with
   numbers, what breaks without it
4. **Worked example** — trace a real case end to end through the model from the primary source
5. **Edge case / failure** — erratum, ordering rule, misconfiguration, symptom
6. **Apply** — do it on the mission's real artifact (project code, bench, exercise)

Rung 1 is a means, never a finish line. An orient lesson is still bound by the Depth Contract:
its mechanism is the causal path between the parts (what signal/data/ownership flows where and
why), its worked example traces one real transaction across the map with cited values, and its
task asks the user to route a new case through the map. A label-only map does not qualify. At
most one orient lesson per course unless the user asks for more, and the next lesson after it
must reach rung 2 or higher. If the course has delivered
two lessons without reaching Model *and* Rationale for the current unit, stop and say so — that is the skin-deep failure, not
"going at the user's pace". A user preference for "quick" or "simple" constrains lesson *length
and scope*, not whether the model, rationale, mechanism and worked example appear.

## Knowledge

Lessons are designed around a skill the user is going to learn. The knowledge in the lesson
should be only what's required to acquire that skill — teach the knowledge first, then get the
user to practice via an interactive feedback loop.

Gather knowledge from `RESOURCES.md` (see "Sourcing Knowledge" above for how this fork populates
it) — never from parametric guesses. Lessons should be littered with citations back to it.

**Read at primary-source depth.** The vault module note is orientation — itself a summary — so a
lesson built only from it is a summary of a summary. For every lesson, open at least one
primary-source slice and quote concrete content from it: for a chip topic, the TRM section,
register table or timing/state figure the note points to (via the note's page range) **and** the
SDK header or example that exercises it; for other topics, the specific chapter, paper section
or exercise in the cited book. Record the exact locator (page, figure, file:line) in the lesson.
Read the source's mode/state/policy tables and state-machine figures in full so the concept map is
complete and accurate, then distill them into the diagram and the reasoning; do not copy them into
the lesson.
If the note and the primary source disagree, that is a finding — teach the primary source and
flag the note (see "Writing Back to the Vault").

For acquiring knowledge, make the *path* easy, not the *content* thin: ordered steps, one idea
per paragraph, diagrams where the mechanism is spatial or temporal. Cutting the mechanism to save
effort is the wrong way to reduce difficulty — difficulty that is the tool belongs in the
feedback loop below.

## Skills — Feedback Loop

If knowledge is about acquisition, skills are about durability and flexibility. For skill
acquisition, difficulty is the tool: effortful retrieval is what builds storage strength.

Each lesson needs a feedback loop, as tight and automatic as possible.

**The user must produce before they see.** A "click to reveal the answer" card that the learner
grades themselves does not count as a feedback loop — fluent re-reading feels like learning and
builds no storage strength. Each lesson's task asks the user to commit to a specific, falsifiable
output *in the conversation or in an artifact* (a computed value, a written call sequence, a
sketched data path, a predicted outcome) **before** the lesson reveals or checks it. Strong task types for
modelled topics: sketch the mode diagram from memory and say what triggers each move; given a
scenario (battery life, wake latency, what must keep running), choose the mode/policy and defend
it against the next-best alternative, in your own words; given a misconfiguration, predict the
state it ends up in and why. Then check it
against the source (below), and record what they got wrong. Wrong attempts are the most valuable
signal; do not soften them.

**Prefer verification against real artifacts over quizzes for this domain.** Upstream's quiz
mechanism has a known, unfixed bug: the correct answer reportedly lands in the same slot almost
every time regardless of wording fixes (mattpocock/skills#335) — treat any quiz's answer
position as meaningless, and don't rely on multiple-choice as the primary gate. Prefer instead:

- Predict a register value, driverlib call, or config sequence, then verify it against SDK source
  or the cited vault note.
- Where the user has real hardware on the bench, prefer an actual read-back/observation as the
  check over a self-report.
- If a quiz is still the best fit for a given lesson: keep every answer the same length
  (upstream's existing anti-tell rule) **and** manually randomize which slot holds the correct
  answer — don't let it default to the first option.

## Acquiring Wisdom

Wisdom comes from true real-world interaction — testing skills outside the learning environment.
When the user asks something that appears to require wisdom, attempt an answer, but ultimately
delegate to a **community** (a forum, subreddit, real-world class, or local interest group) —
attempt to find high-reputation ones. If the user expresses a preference not to join a community,
respect it.

## Reference Documents

While creating lessons, also create reference documents — lessons are rarely revisited later,
reference documents will be. They should be the compressed essence of a lesson, in a format
designed for quick reference: syntax/code snippets, algorithms/flowcharts, pose sequences,
exercises/routines, glossaries.

Glossaries are essential reference. Add a term to `GLOSSARY.md` only once the user has
demonstrated understanding of it (not on first exposure) — compressing a concept into a tight
definition is itself evidence of understanding, as is using the term correctly in a produce-first
task or transfer question. After each lesson, promote the terms the user used correctly; a course
that is several lessons in with an empty glossary means the loop is not collecting evidence. Once a term is in the glossary, adhere to it in
every subsequent lesson and definition.

## Writing Back to the Vault

New mechanism, not present upstream. The teaching workspace stays scratch — no confirmation is
needed to write anywhere under `~/teach/<slug>/`. The **one exception** is when a learning-record
is durable enough to belong in the corresponding vault reference note.

A learning-record is write-back-eligible **only if** it does at least one of:

- **(a) Corrects** a specific claim already written in an existing
  `專業領域/<chip>/*模組分析 (...).md` note — the note said X, hands-on/SDK verification showed Y.
- **(b) Adds** durable, module-specific operational knowledge (an erratum, a sequencing
  requirement, a real observed quirk) that the note's 14-section template doesn't cover at all.

**Not eligible**: personal mnemonics/preferences, anything already stated in the note,
restatements of material merely covered (no evidence of correction or addition), or
tentative/unconfirmed conclusions the learner hasn't actually verified.

When eligible: before writing anything, present the target note path, a one-line rationale
(which of (a)/(b), and why), and the exact proposed patch text (the literal addition — append
under an existing or new `## 學習補充` section — or the corrected line), preserving the note's
existing frontmatter and content. Wait for explicit confirmation before writing. Never write to
a vault note silently.

## `NOTES.md`

The user will sometimes express preferences for how they want to be taught, or things to keep in
mind. Record those here so you can refer back to them when designing lessons or working with the
user.

Also record **how the learner failed**: wrong predictions, misconceptions that surfaced, the
step where the transfer question went wrong. These steer the next lesson more than stated
preferences do.
