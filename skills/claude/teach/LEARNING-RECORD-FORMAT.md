# Learning Record Format

Learning records live in `./learning-records/` and use sequential numbering: `0001-slug.md`,
`0002-slug.md`, etc. Create the directory lazily: only when the first record is written.

They are the teaching equivalent of ADRs: they capture non-obvious lessons, key insights, and
stated prior knowledge that will steer future sessions. They are used to calculate the zone of
proximal development.

## Template

```md
# {Short title of what was learned or established}

{1-3 sentences: what was learned (or what prior knowledge was established), and why it matters
for future sessions.}
```

That is the whole format. A learning record can be a single paragraph. The value is recording
_that_ this is now known and _why_ it changes what to teach next, not in filling out sections.

## Optional sections

Only include these when they add genuine value. Most records won't need them.

- **Status** frontmatter (`active | superseded by LR-NNNN`): useful when an earlier
  understanding turns out to be wrong and is replaced.
- **Evidence**: how the user demonstrated the understanding (a question answered, an exercise
  completed, prior experience cited). Useful when the claim might be revisited.
- **Implications**: what this unlocks or rules out for future sessions. Worth recording when
  non-obvious.
- **`Eligible-for-vault-writeback: yes`**: set when this record meets the write-back eligibility
  test in `SKILL.md` § Writing Back to the Vault (it corrects or materially extends an existing
  `專業領域/<chip>/*模組分析` note). Flagging it here means a future session doesn't have to
  re-derive eligibility from prose — but the confirmation step in `SKILL.md` still applies before
  any vault note actually gets edited.

## Numbering

Scan `./learning-records/` for the highest existing number and increment by one.

## When to write a learning record

Write one when any of these is true:

1. **The user demonstrated genuine understanding of something non-trivial**: not just exposure,
   but evidence they can use the concept correctly. This sets a new floor for what to teach next.
   A correct produce-first task or transfer answer (see `SKILL.md` § Skills — Feedback Loop) is
   such evidence; a self-graded "show answer" click is not. Record a wrong attempt too, as a
   misconception under trigger 3 — it is often more informative than a right one.
2. **The user disclosed prior knowledge**: "I already know X." Record it so future sessions
   don't re-teach it. Also record the _depth_ claimed.
3. **A misconception was corrected**: the user previously believed something wrong and now sees
   why. These are high-value: they predict future stumbling blocks for related topics.
4. **The mission shifted in response to learning**: the user discovered they cared about
   something different than they thought. Cross-link to [[MISSION.md]] and update it.
5. **The evidence meets the vault write-back eligibility test** (`SKILL.md` § Writing Back to the
   Vault): it corrects a specific claim in an existing `專業領域/<chip>/*模組分析 (...).md` note,
   or adds durable module-specific operational knowledge the note's 14-section template doesn't
   cover. Set `Eligible-for-vault-writeback: yes` and follow the confirmation flow in `SKILL.md`
   before writing anything to the vault note itself.

### What does _not_ qualify

- Material that was merely covered. Coverage is not learning. Wait for evidence.
- Anything already captured tersely in [[GLOSSARY.md]] as a term definition. Don't duplicate.
- Session-by-session activity logs. Learning records are not a journal: they are decision-grade
  insights.
- Personal mnemonics/preferences, or tentative/unconfirmed conclusions the learner hasn't
  actually verified — these don't qualify as write-back-eligible even if they otherwise look
  record-worthy (see trigger 5 above).

## Supersession

When a later record contradicts an earlier one (the user's understanding deepened or corrected),
mark the old record `Status: superseded by LR-NNNN` rather than deleting it. The history of how
understanding evolved is itself useful signal.
