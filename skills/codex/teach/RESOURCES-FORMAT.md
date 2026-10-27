# RESOURCES.md Format

`RESOURCES.md` is the curated set of trusted sources for this topic. Knowledge for explainers
should be drawn from here, not from parametric guesses. Wisdom comes from the communities listed
here.

This is a vault-adapted fork. The Knowledge section is **tiered** for chip/module workspaces
(see `SKILL.md` § Sourcing Knowledge). Non-chip workspaces use the flat upstream structure
instead — see which one applies via `SKILL.md` § Workspace Root Resolution.

## Structure — chip/module topic

```md
# {Chip} — {Module} Resources

## Knowledge

### Tier 1 — Vault
- [[<chip>/<module>模組分析 (<doc-id>)]] — TRM pages <start>–<end>.
  Use for: <what it covers>.

### Tier 2 — SDK
- `/home/andre/ti/<package>/.../dl_<module>.h`
  Use for: <driverlib calls this mission needs>.
- `/home/andre/ti/<package>/examples/...`
  Use for: <the worked usage pattern it demonstrates>.

### Tier 3 — External (only once Tier 1 + Tier 2 leave a gap)
- [External] [<title>](<url>)
  Use for: <the specific gap this fills — name it>.

## Wisdom (Communities)

- [TI E2E — <sub-forum>](https://e2e.ti.com/...)
  Use for: <what to bring here>.

## Gaps

- <anything Tier 1/2/3 together still don't cover>
```

## Structure — non-chip topic

```md
# {Topic} Resources

## Knowledge

- [Book: _The Science and Practice of Strength Training_ by Zatsiorsky & Kraemer](https://example.com)
  Foundational text on programming and adaptation. Use for: anything to do with periodisation,
  recovery, intensity zones.
- [Article: "How Much Should I Train?" by Greg Nuckols (Stronger By Science)](https://example.com)
  Evidence-based review of volume landmarks. Use for: weekly set targets per muscle group.

## Wisdom (Communities)

- [r/weightroom](https://reddit.com/r/weightroom)
  High-signal subreddit, moderated against bro-science. Use for: programme critique, plateau
  troubleshooting.
- Local: Tuesday strength class at {gym name}
  Use for: real-time coaching feedback on lifts.
```

## Rules

- **Tier order is a fall-through, not a checklist.** Tier 1 alone can be sufficient to start
  teaching; Tier 3 only gets populated once Tier 1 + Tier 2 together leave a real gap the mission
  needs. An empty tier is normal, not an error — never treat a missing vault note as a reason to
  stop or as a failure to report.
- **Label every Tier 3 / non-vault entry `[External]`** so it's never confused with vault/SDK
  ground truth.
- **High-trust only.** Prefer primary sources, recognised experts, peer-reviewed work, and
  communities with strong moderation. If a resource is marketing dressed as education, leave it
  out.
- **Primary source, not just the summary.** For each Tier 1 vault note, also list the underlying
  TRM sections/figures/register tables the lessons will actually open, and the SDK files that
  exercise them (Tier 2). Do not write "this lesson does not teach API operation" as a default
  limit on Tier 2 entries; list what the mission's skills need.
- **Annotate every entry.** A bare link or path is useless in three months. Add one line: what it
  covers and when to reach for it.
- **Surface gaps explicitly.** If no good resource exists for an area the mission needs, write a
  `## Gaps` section listing what is missing. For a chip topic missing a Tier 1 vault note,
  mention that `/pdf-analysis` could produce one — but do not invoke it yourself.
- **Prune ruthlessly.** A resource that turned out to be wrong, shallow, or off-mission should be
  removed, not buried. Better five sharp sources than thirty mediocre ones.
- **Record community preferences.** If the user has opted out of joining communities, note it
  here so future sessions don't keep proposing them.
