# MODEL.md Format

`MODEL.md` is the teacher's **concept map** of the topic, built from primary sources *before the
first lesson* and kept in the workspace root. It is the course's plan and coverage ledger. It is
**not** a copy of the vault note and not where learners read detail: the vault module note holds
the row-level reference (registers, every policy value). `MODEL.md` records what concepts the
learner must come to understand, how they connect, why the design is the way it is, and which
diagram will teach each.

Build it by reading the primary source (TRM chapter, state/timing figures, SDK headers; or the
book chapter / spec for non-chip topics) in full, so the map is complete and accurate. Then keep
only concept-level content here.

## Structure

One `##` unit per teachable subsystem (e.g. operating modes, reset levels, clock switching). Fill
every block; write `n/a — <why>` rather than omitting one.

```md
# {Topic} Concept Map

## Unit: {name}   (mission link: {which mission output this serves})

### The picture
- Concepts at concept level — **all of them** (e.g. every operating mode), each in one plain line.
- How they relate: what moves between them, what triggers it (plain words), what depends on what.
- Diagram that will teach it (kind + what it shows + any interaction).

### Design logic (why it exists)
- **Forcing problem**: the need that makes this unit exist, as a tradeoff.
- **Why several, not one**: what each variant buys and gives up; where the boundaries sit.
- **Policy logic**: for each policy knob, why a different setting suits a different mode/situation
  (the trade each side makes), and the rule of thumb for choosing.
- Mark any rationale the source doesn't state as `derived, not sourced`, or `unknown`.

### Pitfalls worth a lesson
- The few sourced traps that change how a learner should think (not every erratum).

### Detail lives here
- Links to the vault note sections / TRM pages / SDK files for row-level detail, with locators.

### Coverage
| Concept / reasoning | Taught in lesson | Learner reproduced it? (sketch / explain / choose) |
|---|---|---|
```

## Rules

- **Complete at concept level, thin at detail level.** Every mode/state/path appears; register
  fields and value tables do not — they are links.
- **Every claim carries a locator** (TRM page/figure/table, SDK file:line), or is marked
  `[unverified]` and resolved before teaching.
- **"Design logic" is mandatory.** If the source never states a rationale, derive it from the
  source's own numbers and label it; never present a guess as design intent.
- **The Coverage table drives lesson planning.** A unit is done when each concept is taught *and*
  the learner reproduced it (sketched the diagram, explained why, chose for a scenario).
- **Revise when the source contradicts it.** Update in place; log a learning record if it
  overturns something already taught.
- **Defer whole units only.** A unit the mission does not need is listed under `## Deferred units`
  with a reason. Within a mission-relevant unit, never leave a concept out of the picture for
  convenience.
