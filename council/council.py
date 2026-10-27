#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""council: coordinator for the agent-council skill (technical-explorer / knowledge-explore profile).

S1: item parser and citation checker.
    uv run --script council.py check-cites ITEMS.md --root DIR [--root DIR ...] [--format json|text]
S3: `run BRIEF.md --pane NAME=HANDLE ...` drives real agents in Orca panes through orca-ask.sh (class OrcaAgent).
S4: `start BRIEF.md` opens the participant panes, runs `run` detached and returns at once; `status RUN_DIR` and `stop RUN_DIR`
    report on / end it (so a lead agent never blocks on a run that takes minutes to hours).
S2: the council state machine (`run_council`), driven through an Agent interface so it can be tested with scripted agents.
    Rounds: 1 independent answers -> 2 merge into one draft (integrator) -> 3.. challenge (reviewers ACCEPT/OBJECT per item,
    integrator revises) until every reviewer validly accepts every item, or a limit is hit.
    Status: consensus | consensus-unverified (normal end, flagged) | no-consensus (round cap) | time-limit | aborted.

Item format (one block per item, fields start at column 0, continuation lines are appended to the previous field):

    ITEM A1
    statement: <one sentence>
    basis: verified | inferred | unverified
    evidence: <path:line "quote"> | <path:start-end "quote"> | <URL "quote"> | measurement: <what was done, what was seen>
    check: <how to confirm>

Citation check (deterministic, no model):
  * an item declared `verified` must have at least one file citation whose quote really appears in the cited line range
    of a file inside an allowed --root; otherwise it is downgraded to `unverified` (the discussion is never blocked);
  * a citation that matches only comment lines does not count: a comment is a claim, not evidence;
  * URL and measurement evidence cannot be checked offline: the item keeps `verified` but is flagged;
  * `inferred` / `unverified` items are not downgraded further.
Exit status of check-cites: 0 = format ok, 1 = format problems found (items are still reported), 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BASES = ("verified", "inferred", "unverified")
FIELDS = ("statement", "basis", "evidence", "check")
OPTIONAL_FIELDS = ("from", "files", "rollback", "depends")
TYPE_REQUIRED = {"plan": ("files", "rollback"), "issue": (), "analysis": ()}

ITEM_RE = re.compile(r"^ITEM\s+(\S+)\s*$")
FIELD_RE = re.compile(r"^(statement|basis|evidence|check|from|files|rollback|depends):\s*(.*)$")
_QUOTE = r'"(?P<q>(?:[^"\\]|\\.)*)"'
URL_CITE_RE = re.compile(r"(?P<url>https?://\S+?)\s+" + _QUOTE)
FILE_CITE_RE = re.compile(r"(?P<path>[~/\w.+@\-][^\s\"]*?):(?P<a>\d+)(?:-(?P<b>\d+))?\s+" + _QUOTE)

# line-comment / block-comment markers by extension; unknown extensions skip the comment check
_C_LIKE = ("//", "/*", "*")
_HASH = ("#",)
_ASM = (";", "@", "//", "/*", "*")
COMMENT_PREFIXES = {
    **{e: _C_LIKE for e in (".c", ".h", ".cc", ".cpp", ".hpp", ".js", ".ts", ".mjs", ".java", ".go", ".rs", ".cs", ".sysconfig")},
    **{e: _HASH for e in (".py", ".sh", ".bash", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".mk", ".cmake")},
    **{e: _ASM for e in (".s", ".asm", ".S", ".inc")},
}


@dataclass
class Item:
    id: str
    fields: dict = field(default_factory=dict)


@dataclass
class Citation:
    kind: str  # file | url | measurement
    spec: str
    ok: bool
    reason: str = ""
    comment_only: bool = False


def norm(s: str) -> str:
    return " ".join(s.split())


def unescape(q: str) -> str:
    return q.replace('\\"', '"').replace("\\\\", "\\")


def parse_items(text: str) -> tuple[list[Item], list[str]]:
    """Parse item blocks. Returns (items, format problems)."""
    items: list[Item] = []
    problems: list[str] = []
    cur: Item | None = None
    current_field: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = ITEM_RE.match(line)
        if m:
            cur = Item(id=m.group(1))
            items.append(cur)
            current_field = None
            continue
        if cur is None:
            continue
        fm = FIELD_RE.match(line)
        if fm:
            current_field = fm.group(1)
            if current_field in cur.fields:
                problems.append(f"{cur.id}: duplicate field '{current_field}'")
            cur.fields[current_field] = fm.group(2).strip()
            continue
        if not line.strip():
            current_field = None
        elif current_field:
            cur.fields[current_field] = (cur.fields[current_field] + "\n" + line.strip()).strip()
    seen: set[str] = set()
    for it in items:
        if it.id in seen:
            problems.append(f"{it.id}: duplicate item id")
        seen.add(it.id)
        for name in FIELDS:
            if not it.fields.get(name, "").strip():
                problems.append(f"{it.id}: missing or empty field '{name}'")
        basis = it.fields.get("basis", "").strip().lower()
        if basis and basis not in BASES:
            problems.append(f"{it.id}: invalid basis '{basis}' (use {', '.join(BASES)})")
    if not items:
        problems.append("no ITEM blocks found")
    return items, problems


def find_citations(evidence: str) -> list[dict]:
    """Extract recognisable citations from an evidence field."""
    found: list[dict] = []
    rest = evidence
    for m in URL_CITE_RE.finditer(evidence):
        found.append({"kind": "url", "spec": f'{m.group("url")} "{unescape(m.group("q"))}"'})
    rest = URL_CITE_RE.sub(" ", rest)
    for m in FILE_CITE_RE.finditer(rest):
        a = int(m.group("a"))
        b = int(m.group("b")) if m.group("b") else a
        found.append({"kind": "file", "path": m.group("path"), "a": a, "b": b, "quote": unescape(m.group("q")),
                      "spec": f'{m.group("path")}:{a}' + (f"-{b}" if m.group("b") else "") + f' "{unescape(m.group("q"))}"'})
    if re.search(r"(?im)(^|[\s;|-])measurement:\s*\S", evidence):
        found.append({"kind": "measurement", "spec": "measurement"})
    return found


def _root_prefixed(p: Path, roots: list[Path]) -> list[Path]:
    """A path written from above the root (root `.../source/drivers`, cited `source/drivers/soc/x.c`): drop the leading parts
    that repeat the end of the root and look under the root. The result is still checked to lie inside the root."""
    out = []
    for r in roots:
        for k in range(min(len(p.parts) - 1, len(r.parts)), 0, -1):
            if p.parts[:k] == r.parts[-k:]:
                out.append(r.joinpath(*p.parts[k:]))
                break
    return out


def path_problems(items: list, roots: list[Path]) -> list:
    """Every file citation of a reply points at a file that does not exist under the roots: the agent is using the wrong paths,
    so say so now instead of letting a whole run end with every item unverified."""
    cites = [c for it in items for c in find_citations(it.fields.get("evidence", "")) if c["kind"] == "file"]
    if len(cites) >= 3 and all(_resolve(c["path"], roots) == (None, "file not found") for c in cites):
        return [f"none of your {len(cites)} file citations exists under the evidence roots ({', '.join(str(r) for r in roots)}); "
                f"cite each path relative to one of those roots, or absolute (you wrote e.g. {cites[0]['path']})"]
    return []


def _resolve(path: str, roots: list[Path]) -> tuple[Path | None, str]:
    p = Path(path).expanduser()
    candidates = [p] if p.is_absolute() else [r / p for r in roots] + _root_prefixed(p, roots)
    for c in candidates:
        if c.is_file():
            real = c.resolve()
            for r in roots:
                if real.is_relative_to(r):
                    return real, ""
            return None, "file is outside the allowed evidence roots"
    return None, "file not found"


def check_file_cite(c: dict, roots: list[Path]) -> Citation:
    real, why = _resolve(c["path"], roots)
    if real is None:
        return Citation("file", c["spec"], False, why)
    a, b, quote = c["a"], c["b"], c["quote"]
    if not norm(quote):
        return Citation("file", c["spec"], False, "empty quote")
    if a < 1 or b < a:
        return Citation("file", c["spec"], False, "invalid line range")
    lines = real.read_text(encoding="utf-8", errors="replace").splitlines()
    if b > len(lines):
        return Citation("file", c["spec"], False, f"line range beyond end of file ({len(lines)} lines)")
    span = lines[a - 1:b]
    if norm(quote) not in norm(" ".join(span)):
        return Citation("file", c["spec"], False, "quote not found in the cited lines")
    prefixes = COMMENT_PREFIXES.get(real.suffix)
    if prefixes:
        body = [l.strip() for l in span if l.strip() and not l.startswith("#!")]  # a shebang is not a comment
        if body and all(l.startswith(prefixes) for l in body):
            return Citation("file", c["spec"], True, "only comment lines are cited", comment_only=True)
    return Citation("file", c["spec"], True)


def resolve_item(item: Item, roots: list[Path]) -> dict:
    declared = item.fields.get("basis", "").strip().lower()
    cites: list[Citation] = []
    for c in find_citations(item.fields.get("evidence", "")):
        if c["kind"] == "file":
            cites.append(check_file_cite(c, roots))
        elif c["kind"] == "url":
            cites.append(Citation("url", c["spec"], True, "not machine-checked (external)"))
        else:
            cites.append(Citation("measurement", c["spec"], True, "not machine-checked (measurement)"))
    final, notes = declared, []
    if declared == "verified":
        files = [c for c in cites if c.kind == "file"]
        good = [c for c in files if c.ok and not c.comment_only]
        external = [c for c in cites if c.kind != "file"]
        if files and not good:
            final = "unverified"
            notes.append("downgraded: no file citation holds up (" + "; ".join(f"{c.spec} -> {c.reason}" for c in files) + ")")
        elif not files and not external:
            final = "unverified"
            notes.append("downgraded: no recognisable evidence")
        else:
            for c in files:
                if not (c.ok and not c.comment_only):
                    notes.append(f"warning: citation failed but another holds: {c.spec} -> {c.reason}")
            if not good:
                notes.append("kept verified on external/measurement evidence only: not machine-checked")
    return {
        "id": item.id,
        "statement": item.fields.get("statement", ""),
        "check": item.fields.get("check", ""),
        "basis_declared": declared,
        "basis_final": final,
        "downgraded": final != declared,
        "notes": notes,
        "citations": [c.__dict__ for c in cites],
    }


# ---------------------------------------------------------------------------------------------------------------------
# S2: the council state machine
# ---------------------------------------------------------------------------------------------------------------------
STATUSES = ("consensus", "consensus-unverified", "no-consensus", "time-limit", "aborted")
RE_LINE = re.compile(r"^RE\s+([\w.\-]+)\s*:?\s*(ACCEPT|OBJECT)\b(.*)$", re.I)
REBUTTAL_RE = re.compile(r"^REBUTTAL\s+([\w.\-]+)\s*:\s*(.*)$")

TYPE_HINTS = {
    "plan": "Each ITEM is one STEP of an action plan, in the order it should be done (use `depends:` for real dependencies). "
            "`statement:` is the action. `files:` lists the files the step may touch. `check:` is an executable acceptance check "
            "(the command to run and what it must show). `rollback:` says how to undo the step. A step whose statement starts with "
            "`KILL:` is a stop condition (when to abandon the plan); one that starts with `OPEN:` is an unresolved question.",
    "issue": "Each ITEM is one HYPOTHESIS about the root cause; list the most likely first. `statement:` is the hypothesis. "
             "`check:` is the experiment or measurement that separates this hypothesis from the others: what you would see if it is true "
             "and what if it is false.",
    "analysis": "Each ITEM is one CLAIM about how the code or system works. `statement:` is the claim, `evidence:` where you read it, "
                "`check:` how someone else can confirm it.",
}

_FORMAT_BASE = """Write items in exactly this format (field names at the start of the line):

ITEM <id>
statement: <one sentence>
basis: verified | inferred | unverified
evidence: <path:line "exact quote"> | <path:start-end "exact quote"> | <URL "exact quote"> | measurement: <what you did, what you saw>
check: <how to confirm it>
{extra}
Rules: `verified` needs at least one file citation whose quote really appears on the cited lines (a script checks this; a comment
is a claim, not evidence, and does not count). Wrap every quote in double quotes (single quotes are not recognised); if the source line
contains double quotes, quote a shorter piece of it that has none. If you did not read it yourself, use `inferred` or `unverified`. Write in English."""

_FORMAT_EXTRA = {
    "plan": "files: <files this step may touch, comma separated>\nrollback: <how to undo this step>\ndepends: <ids of steps that must come first> (optional)\n",
}


def format_spec(type_: str) -> str:
    return _FORMAT_BASE.replace("{extra}", _FORMAT_EXTRA.get(type_, "").replace("\\n", "\n"))


def type_problems(items: list, type_: str) -> list:
    """Fields a given council type requires on top of the four basic ones."""
    return [f"{i.id}: a {type_} item needs a `{k}:` field" for i in items for k in TYPE_REQUIRED.get(type_, ()) if not i.fields.get(k, "").strip()]


class AgentError(Exception):
    """An agent did not deliver (kind: silent | refused | timeout | format)."""

    def __init__(self, kind: str, detail: str = "", agent: str = ""):
        super().__init__(f"{agent}: {kind}: {detail}".strip(": "))
        self.kind, self.detail, self.agent = kind, detail, agent


class TimeLimit(Exception):
    """The whole-run time limit ran out while an agent was still working on a turn."""


class Agent:
    """Interface: ask() returns the agent's complete reply text, or raises AgentError."""

    name = "agent"

    def ask(self, prompt: str, timeout: int) -> str:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class Brief:
    type: str
    task: str
    participants: list
    integrator: str = ""
    roots: list = field(default_factory=lambda: ["."])
    max_rounds: int = 5
    time_limit_s: int = 7200
    turn_timeout_s: int = 3600
    format_retries: int = 1


def load_brief(text: str) -> Brief:
    """`key: value` lines up to the first blank line, then the task text.
    Keys: type, participants (comma list), integrator, roots (comma list), max_rounds, time_limit_minutes, turn_timeout_minutes."""
    head, _, body = text.strip().partition("\n\n")
    kv = {}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        kv[k.strip().lower()] = v.strip()
    if kv.get("type") not in TYPE_HINTS:
        raise ValueError(f"brief type must be one of {sorted(TYPE_HINTS)}")
    parts = [p.strip() for p in kv.get("participants", "").split(",") if p.strip()]
    if len(parts) < 2:
        raise ValueError("brief needs at least two participants")
    b = Brief(type=kv["type"], task=body.strip(), participants=parts, integrator=kv.get("integrator", ""))
    if kv.get("roots"):
        b.roots = [r.strip() for r in kv["roots"].split(",") if r.strip()]
    if kv.get("max_rounds"):
        b.max_rounds = int(kv["max_rounds"])
    if kv.get("time_limit_minutes"):
        b.time_limit_s = int(float(kv["time_limit_minutes"]) * 60)
    if kv.get("turn_timeout_minutes"):
        b.turn_timeout_s = int(float(kv["turn_timeout_minutes"]) * 60)
    if not b.task:
        raise ValueError("brief has no task text")
    return b


def item_hash(it: Item) -> str:
    s = "|".join(norm(it.fields.get(k, "")) for k in (*FIELDS, "files", "rollback", "depends"))
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def has_evidence(text: str) -> bool:
    return bool(find_citations(text)) or bool(re.search(r"evidence:\s*\S", text, re.I))


def items_to_text(items: list) -> str:
    out = []
    for it in items:
        out.append(f"ITEM {it.id}")
        for k in (*FIELDS, *OPTIONAL_FIELDS):
            if it.fields.get(k):
                out.append(f"{k}: {it.fields[k]}")
        out.append("")
    return "\n".join(out)


def parse_review(text: str, ids: list) -> tuple:
    """Reviewer reply: one `RE <id>: ACCEPT|OBJECT [because ...]` per draft item, optional `RE MISSING: ...`."""
    res: dict = {}
    problems: list = []
    cur = None
    for raw in text.splitlines():
        m = RE_LINE.match(raw.strip())
        if m:
            key, verb = m.group(1), m.group(2).upper()
            rest = re.sub(r"^(because\b[:,]?)\s*", "", m.group(3).strip(), flags=re.I)
            if key in res:
                problems.append(f"duplicate RE line for {key}")
            res[key] = [verb, rest]
            cur = key
        elif not raw.strip():
            cur = None
        elif cur:
            res[cur][1] += "\n" + raw.strip()
    for i in ids:
        if i not in res:
            problems.append(f"no `RE {i}: ACCEPT|OBJECT` line for item {i}")
    for k, (verb, rest) in res.items():
        if k != "MISSING" and k not in ids:
            problems.append(f"unknown item id '{k}'")
        if verb == "OBJECT" and not rest.strip():
            problems.append(f"RE {k}: OBJECT needs a reason")
    return {k: (v[0], v[1].strip()) for k, v in res.items()}, problems


def parse_revision(text: str, type_: str = "") -> tuple:
    """Integrator reply after a review round: full item blocks (new draft) or NOCHANGE, plus optional REBUTTAL lines."""
    rebuttals = {m.group(1): m.group(2).strip() for l in text.splitlines() if (m := REBUTTAL_RE.match(l.strip()))}
    if re.search(r"(?m)^ITEM\s+\S+", text):
        items, problems = parse_items(text)
        ids = [i.id for i in items]
        problems += [f"duplicate item id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
        problems += type_problems(items, type_)
        return (items, rebuttals), problems
    if re.search(r"(?m)^\s*NOCHANGE\b", text):
        return (None, rebuttals), []
    return (None, rebuttals), ["reply with the full revised ITEM blocks, or the single word NOCHANGE"]


def _revision_checked(text: str, type_: str, roots: list) -> tuple:
    value, problems = parse_revision(text, type_)
    if value[0] is not None:
        problems = problems + path_problems(value[0], roots)
    return value, problems


def prompt_independent(brief: Brief) -> str:
    return (f"[[COUNCIL phase=independent round=1 attempt=1]]\nYou are one of {len(brief.participants)} independent experts. Work alone: "
            f"you cannot see the other answers. Read-only: do not change any file.\n\nTASK ({brief.type}):\n{brief.task}\n\n"
            f"{TYPE_HINTS[brief.type]}\n\n{format_spec(brief.type)}\n\nReply with the ITEM blocks only.")


def prompt_merge(brief: Brief, candidates: dict) -> str:
    cands = "\n\n".join(f"=== candidate from {n} ===\n{t.strip()}" for n, t in candidates.items())
    return (f"[[COUNCIL phase=merge round=2 attempt=1]]\nYou are the integrator. Merge the independent candidates below into ONE draft. "
            f"Do not average: keep what is best supported by evidence, drop what is not. If the candidates differ so much that the "
            f"task itself seems unclear, still produce your best draft and say so in a `statement:`.\n\nTASK ({brief.type}):\n"
            f"{brief.task}\n\n{TYPE_HINTS[brief.type]}\n\n{format_spec(brief.type)}\n\nAdd a `from:` field to every item naming the candidate "
            f"item ids it came from (e.g. `from: Ornith/A1, Qwen/B2`). Use short stable ids; later rounds keep them.\n\n{cands}\n\n"
            f"Reply with the ITEM blocks only.")


def prompt_review(brief: Brief, rnd: int, draft_text: str, rebuttals: dict, carried: list) -> str:
    extra = ""
    if rebuttals:
        extra += "\nIntegrator rebuttals to earlier objections:\n" + "\n".join(f"- {k}: {v}" for k, v in rebuttals.items()) + "\n"
    if carried:
        extra += "\nYour objections that are still open:\n" + "\n".join(f"- {c}" for c in carried) + "\n"
    return (f"[[COUNCIL phase=review round={rnd} attempt=1]]\nChallenge this draft. Read-only. For EVERY item reply on its own line:\n"
            f"RE <id>: ACCEPT [because ...]\nRE <id>: OBJECT because <reason> evidence: <citation or measurement>\n"
            f"Something important is missing: `RE MISSING: OBJECT because <what> evidence: ...`.\n"
            f"If you objected before, switching to ACCEPT needs either a changed item or new evidence in that line; agreement "
            f"without new evidence is not counted. Agreement is only real if you checked it yourself.\n"
            f"Object on substance only. Do not object because of citation quoting or formatting: a script checks citations and downgrades a bad one by itself.\n\nTASK ({brief.type}):\n"
            f"{brief.task}\n{extra}\nDRAFT:\n{draft_text}")


def prompt_revise(brief: Brief, rnd: int, draft_text: str, objections: list) -> str:
    obj = "\n".join(f"- {o['agent']} on {o['id']}: {o['text']}" + (" [agreement without new evidence was not counted]" if o.get("flag") else "")
                    for o in objections)
    return (f"[[COUNCIL phase=revise round={rnd} attempt=1]]\nReviewers objected. Fix the draft where they are right, using evidence; "
            f"where you disagree, keep the item and answer with `REBUTTAL <id>: <reason + evidence>`. Keep item ids stable. "
            f"Reply with the complete revised ITEM blocks, or the single word NOCHANGE.\n\nOBJECTIONS:\n{obj}\n\nCURRENT DRAFT:\n{draft_text}")


class CouncilRun:
    def __init__(self, brief: Brief, agents: dict, workdir, clock=time.monotonic):
        self.b, self.agents, self.dir, self.clock = brief, agents, Path(workdir), clock
        self.dir.mkdir(parents=True, exist_ok=True)
        self.roots = [Path(r).expanduser().resolve() for r in brief.roots]
        self.integrator = brief.integrator or brief.participants[0]
        self.reviewers = [n for n in brief.participants if n != self.integrator]
        if not self.reviewers or self.integrator not in agents or any(n not in agents for n in brief.participants):
            raise ValueError("participants/integrator must all be provided as agents, with at least one reviewer")
        self.start = clock()
        self.rounds = 0
        self.draft: list = []
        self.flags: list = []
        self.open: list = []
        self.missing_open: list = []
        self.reviewed: dict = {}        # item id -> item hash, as the last review saw it
        self.objected: set = set()      # item ids with an objection standing in the last review
        self.ledger: list = []

    # ---- plumbing -------------------------------------------------------------------------------------------------
    def elapsed(self) -> float:
        return self.clock() - self.start

    def log(self, line: str) -> None:
        self.ledger.append(line)

    def ask(self, name: str, phase: str, prompt: str, validate):
        """Ask an agent; on a format problem send the problems back (up to format_retries) before giving up."""
        problems: list = []
        for attempt in range(1, 2 + self.b.format_retries):
            p = prompt if attempt == 1 else re.sub(r"attempt=\d+", f"attempt={attempt}", prompt, count=1) + \
                "\n\nYOUR PREVIOUS REPLY WAS REJECTED:\n- " + "\n- ".join(problems) + "\nSend the complete reply again in the required format."
            stem = f"r{self.rounds:02d}-{phase}-{name}" + (f"-a{attempt}" if attempt > 1 else "")
            prompt_f, reply_f = self.dir / f"{stem}.prompt.md", self.dir / f"{stem}.reply.md"
            if prompt_f.exists() and reply_f.exists() and prompt_f.read_text(encoding="utf-8") == p:
                # resume: this exact prompt was already answered in this run directory (e.g. the previous run died on another agent)
                value, problems = validate(reply_f.read_text(encoding="utf-8"))
                if not problems:
                    self.log(f"round {self.rounds} {phase} {name}: reused the saved reply {reply_f.name}")
                    return value, reply_f.read_text(encoding="utf-8")
                # the same prompt already got this reply and it was rejected: go straight to the retry, do not ask again
                self.log(f"round {self.rounds} {phase} {name}: rejected (attempt {attempt}, saved reply): {'; '.join(problems)}")
                continue
            prompt_f.write_text(p, encoding="utf-8")
            left = self.b.time_limit_s - self.elapsed()
            if left <= 0:
                raise TimeLimit(f"{name} was not asked ({phase}): no time left")
            try:
                # a turn may not outlast the run: the limit holds even while an agent is still thinking
                text = self.agents[name].ask(p, min(self.b.turn_timeout_s, max(60, int(left))))
            except AgentError as e:
                e.agent = e.agent or name
                if e.kind == "timeout" and self.elapsed() >= self.b.time_limit_s:
                    raise TimeLimit(f"{name} was still working on {phase} when the limit ran out")
                raise
            reply_f.write_text(text, encoding="utf-8")
            value, problems = validate(text)
            if not problems:
                return value, text
            self.log(f"round {self.rounds} {phase} {name}: rejected (attempt {attempt}): {'; '.join(problems)}")
        raise AgentError("format", "; ".join(problems), name)

    def gate(self):
        if self.rounds >= self.b.max_rounds:
            return self.finish("no-consensus", f"round limit reached ({self.b.max_rounds} rounds)")
        if self.elapsed() >= self.b.time_limit_s:
            return self.finish("time-limit", f"time limit reached ({self.b.time_limit_s // 60} min) after {self.rounds} rounds")
        return None

    # ---- the run --------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        try:
            return self._run()
        except TimeLimit as e:
            return self.finish("time-limit", f"time limit reached ({self.b.time_limit_s // 60} min) after {self.rounds} rounds: {e}")
        except AgentError as e:
            return self.finish("aborted", f"{e.agent}: {e.kind}: {e.detail}")

    def _run(self) -> dict:
        b = self.b
        (self.dir / "brief.md").write_text(f"type: {b.type}\nparticipants: {', '.join(b.participants)}\nintegrator: {self.integrator}\n\n{b.task}\n", encoding="utf-8")
        if (r := self.gate()):
            return r
        self.rounds += 1
        candidates = {}
        for n in b.participants:
            _, text = self.ask(n, "independent", prompt_independent(b), lambda t: ((), parse_items(t)[1] + type_problems(parse_items(t)[0], b.type) + path_problems(parse_items(t)[0], self.roots)))
            candidates[n] = text
        self.log(f"round 1 independent: {', '.join(candidates)} answered separately")

        if (r := self.gate()):
            return r
        self.rounds += 1

        def valid_draft(t):
            items, problems = parse_items(t)
            ids = [i.id for i in items]
            problems += [f"duplicate item id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
            problems += type_problems(items, b.type)
            problems += path_problems(items, self.roots)
            problems += [f"{i.id}: draft items need a `from:` field naming the candidate items they came from" for i in items if not i.fields.get("from")]
            return items, problems

        self.draft, draft_text = self.ask(self.integrator, "merge", prompt_merge(b, candidates), valid_draft)
        self.log(f"round 2 merge by {self.integrator}: {len(self.draft)} items")

        prev: dict = {}        # (reviewer, id) -> ("OBJECT"|"ACCEPT", item hash when first objected)
        missing: dict = {}     # reviewer -> (reason, frozenset(ids) at the time)
        rebuttals: dict = {}
        carried: dict = {n: [] for n in self.reviewers}
        while True:
            if (r := self.gate()):
                return r
            self.rounds += 1
            ids = [i.id for i in self.draft]
            hashes = {i.id: item_hash(i) for i in self.draft}
            objections, self.missing_open, round_ok = [], [], True
            for rv in self.reviewers:
                parsed, _ = self.ask(rv, "review", prompt_review(b, self.rounds, draft_text, rebuttals, carried[rv]),
                                     lambda t: parse_review(t, ids))
                carried[rv] = []
                for i in ids:
                    verb, text = parsed[i]
                    stance, flag = verb, ""
                    was = prev.get((rv, i))
                    if verb == "ACCEPT" and was and was[0] == "OBJECT" and was[1] == hashes[i] and not has_evidence(text):
                        stance, flag = "OBJECT", "agreement without new evidence"
                        self.flags.append({"round": self.rounds, "agent": rv, "item": i, "kind": "flip-without-new-evidence"})
                    if stance == "OBJECT":
                        round_ok = False
                        objections.append({"agent": rv, "id": i, "text": text or "(accept not counted)", "flag": flag})
                        carried[rv].append(f"{i}: {text or flag}")
                        if not was or was[0] != "OBJECT":
                            prev[(rv, i)] = ("OBJECT", hashes[i])
                    else:
                        prev[(rv, i)] = ("ACCEPT", hashes[i])
                mv, state = parsed.get("MISSING"), missing.get(rv)
                if mv and mv[0] == "OBJECT":
                    missing[rv] = (mv[1], frozenset(ids))
                elif state:
                    if frozenset(ids) != state[1] or (mv and mv[0] == "ACCEPT" and has_evidence(mv[1])):
                        missing.pop(rv)
                    elif mv and mv[0] == "ACCEPT":
                        self.flags.append({"round": self.rounds, "agent": rv, "item": "MISSING", "kind": "flip-without-new-evidence"})
                if rv in missing:
                    round_ok = False
                    self.missing_open.append({"agent": rv, "text": missing[rv][0]})
                    carried[rv].append(f"MISSING: {missing[rv][0]}")
            self.reviewed, self.objected = hashes, {o["id"] for o in objections}
            self.open = [{"id": o["id"], "agent": o["agent"], "text": o["text"], "flag": o["flag"], "rebuttal": rebuttals.get(o["id"], "")} for o in objections]
            self.log(f"round {self.rounds} review: {'all accepted' if round_ok else f'{len(objections)} objection(s), {len(self.missing_open)} missing-item objection(s)'}")
            if round_ok:
                return self.finish_consensus()

            blocking = objections + [{"agent": m["agent"], "id": "MISSING", "text": m["text"]} for m in self.missing_open]
            (revised, new_rebuttals), _ = self.ask(self.integrator, "revise", prompt_revise(b, self.rounds, draft_text, blocking), lambda t: _revision_checked(t, b.type, self.roots))
            rebuttals = new_rebuttals
            if revised is not None:
                self.draft = revised
                draft_text = items_to_text(revised)
            self.log(f"round {self.rounds} revise by {self.integrator}: {'new draft' if revised is not None else 'no change'}, {len(rebuttals)} rebuttal(s)")

    # ---- results --------------------------------------------------------------------------------------------------
    def resolved(self) -> list:
        return [resolve_item(it, self.roots) for it in self.draft]

    def finish_consensus(self) -> dict:
        items = self.resolved()
        weak = [i for i in items if i["basis_final"] != "verified"]
        status = "consensus-unverified" if weak else "consensus"
        reason = (f"all items accepted by all reviewers; {len(weak)} of {len(items)} item(s) are not verified" if weak
                  else "all items accepted by all reviewers and every item is verified")
        return self.finish(status, reason, items)

    def finish(self, status: str, reason: str, items=None) -> dict:
        items = items if items is not None else self.resolved()
        confirmed, unreviewed = [], []
        for it in self.draft:
            if status.startswith("consensus"):
                confirmed.append(it.id)
            elif it.id not in self.objected:
                (confirmed if self.reviewed.get(it.id) == item_hash(it) else unreviewed).append(it.id)
        changed = [it.id for it in self.draft if self.reviewed.get(it.id) != item_hash(it)]
        result = {"status": status, "reason": reason, "rounds": self.rounds, "elapsed_s": round(self.elapsed(), 1),
                  "confirmed": confirmed, "unreviewed": unreviewed, "changed_since_review": changed,
                  "participants": self.b.participants, "integrator": self.integrator, "items": items,
                  "open": [] if status.startswith("consensus") else self.open,
                  "missing_open": [] if status.startswith("consensus") else self.missing_open,
                  "flags": self.flags, "dir": str(self.dir)}
        (self.dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.dir / "ledger.md").write_text("\n".join(self.ledger) + "\n", encoding="utf-8")
        (self.dir / "report.md").write_text(render_report(result), encoding="utf-8")
        return result


def render_report(r: dict) -> str:
    banner = {
        "consensus": "CONSENSUS: every reviewer accepted every item, and every item is verified.",
        "consensus-unverified": "CONSENSUS (UNVERIFIED): every reviewer accepted every item, but some items rest on inferred, unverified or "
                                "unchecked evidence. This is a normal end; read the marked items before relying on them.",
        "no-consensus": "NO CONSENSUS: the round limit was reached. The open items below are the result.",
        "time-limit": "TIME LIMIT: the run stopped between rounds. The open items below are the result so far.",
        "aborted": "ABORTED: an agent did not deliver. Partial results below.",
    }[r["status"]]
    out = [f"# Council result: {r['status']}", "", banner, "", f"- reason: {r['reason']}", f"- rounds: {r['rounds']}, elapsed: {r['elapsed_s']} s",
           f"- participants: {', '.join(r['participants'])} (integrator: {r['integrator']})",
           "- Nothing here has been applied. The lead triages: Act on / Consider / Noted / Dismissed.", ""]

    def item_lines(i):
        mark = i["basis_final"] + (f" (declared {i['basis_declared']})" if i["downgraded"] else "")
        out = [f"- **{i['id']}** [{mark}] {i['statement']}"] + [f"  - {n}" for n in i["notes"]]
        if i.get("check"):
            lines = i["check"].splitlines()
            out.append(f"  - check: {lines[0]}")
            out += [f"    {l}" for l in lines[1:]]
        return out

    if not r["status"].startswith("consensus"):
        # no agreement: say what is confirmed, what is still open (where to dig), and what nobody has reviewed yet
        by_id = {i["id"]: i for i in r["items"]}
        group = lambda ids: [by_id[i] for i in ids if i in by_id]
        out += ["## Confirmed", "", "Accepted by every reviewer in the last review, and not changed since.", ""]
        for i in group(r["confirmed"]) or []:
            out += item_lines(i)
        if not r["confirmed"]:
            out.append("- (none)")
        out += ["", "## Open items", "", "Objections standing in the last review: this is where to dig deeper.", ""]
        for o in r["open"]:
            out += item_lines(by_id[o["id"]]) if o["id"] in by_id else [f"- **{o['id']}**"]
            out.append(f"  - OPEN: {o['agent']} objects" + (f" ({o['flag']})" if o["flag"] else "") + f": {o['text']}")
            if o["rebuttal"]:
                out.append(f"  - integrator rebuttal: {o['rebuttal']}")
            if o["id"] in r["changed_since_review"]:
                out.append("  - the item was revised after this objection and the revision was not reviewed")
        for m in r["missing_open"]:
            out.append(f"- **MISSING** {m['agent']}: {m['text']}")
        if not r["open"] and not r["missing_open"]:
            out.append("- (none)")
        if r["unreviewed"]:
            out += ["", "## Not reviewed yet", "", "Added or revised after the last review (or no review happened), so nobody has checked them.", ""]
            for i in group(r["unreviewed"]):
                out += item_lines(i)
    else:
        out += ["## Items", ""]
        for i in r["items"]:
            out += item_lines(i)
    if r["flags"]:
        out += ["", "## Flags", ""] + [f"- round {f['round']}: {f['agent']} on {f['item']}: {f['kind']}" for f in r["flags"]]
    return "\n".join(out) + "\n"


def run_council(brief: Brief, agents: dict, workdir, clock=time.monotonic) -> dict:
    return CouncilRun(brief, agents, workdir, clock).run()


DEFAULT_ASK_SCRIPT = "~/.agents/skills/orca-ops/scripts/orca-ask.sh"


class OrcaAgent(Agent):
    """An agent running in an Orca pane. The prompt is written to a file and the pane is only told to read it (long pastes
    into a TUI are fragile); orca-ask.sh then waits for the agent and returns the complete answer it wrote to its answer file.
    orca-ask.sh exit codes: 0 answer, 2 refused (not an agent / own pane / busy), 3 idle without answer file, 4 timeout."""

    def __init__(self, name: str, handle: str, promptdir, ask_script: str = DEFAULT_ASK_SCRIPT):
        self.name, self.handle, self.promptdir = name, handle, Path(promptdir)
        self.ask_script = str(Path(ask_script).expanduser())
        self.n = 0

    def ask(self, prompt: str, timeout: int) -> str:
        self.promptdir.mkdir(parents=True, exist_ok=True)
        self.n += 1
        f = (self.promptdir / f"ask-{self.n:03d}-{self.name}.md").resolve()
        f.write_text(prompt, encoding="utf-8")
        question = f"Read the file {f} and follow its instructions exactly. It contains your task and the required reply format."
        try:
            p = subprocess.run([self.ask_script, self.handle, question, str(timeout)], capture_output=True, text=True, timeout=timeout + 120)
        except subprocess.TimeoutExpired:
            raise AgentError("timeout", f"orca-ask did not return within {timeout + 120} s", self.name)
        if p.returncode == 0:
            return p.stdout
        kind = {2: "refused", 3: "silent", 4: "timeout"}.get(p.returncode, "silent")
        raise AgentError(kind, (p.stderr.strip() or f"orca-ask exit {p.returncode}")[:500], self.name)


def cmd_check_cites(args: argparse.Namespace) -> int:
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except OSError as e:
        print(f"cannot read {args.file}: {e}", file=sys.stderr)
        return 2
    roots = [Path(r).expanduser().resolve() for r in (args.root or ["."])]
    items, problems = parse_items(text)
    report = {"roots": [str(r) for r in roots], "problems": problems,
              "items": [resolve_item(it, roots) for it in items]}
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for p in problems:
            print(f"FORMAT  {p}")
        for r in report["items"]:
            mark = f"{r['basis_declared']} -> {r['basis_final']}" if r["downgraded"] else r["basis_final"]
            print(f"{r['id']:<8} {mark}")
            for n in r["notes"]:
                print(f"         {n}")
    return 1 if problems else 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        brief_text = Path(args.brief).read_text(encoding="utf-8")
        brief = load_brief(brief_text)
    except (OSError, ValueError) as e:
        print(f"brief: {e}", file=sys.stderr)
        return 2
    panes = {}
    for spec in args.pane or []:
        name, _, handle = spec.partition("=")
        if not handle:
            print(f"--pane needs NAME=HANDLE, got '{spec}'", file=sys.stderr)
            return 2
        panes[name.strip()] = handle.strip()
    missing = [n for n in brief.participants if n not in panes]
    if missing:
        print(f"no --pane given for participant(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser() if args.out else Path("~/.cache/council").expanduser() / time.strftime("%Y%m%d-%H%M%S")
    agents = {n: OrcaAgent(n, panes[n], out / "prompts", args.ask_script) for n in brief.participants}
    out.mkdir(parents=True, exist_ok=True)
    (out / "brief.input.md").write_text(brief_text, encoding="utf-8")  # verbatim copy: the run rewrites brief.md as a short record
    result = run_council(brief, agents, out)
    print(f"status: {result['status']}\nreason: {result['reason']}\nrounds: {result['rounds']}, elapsed: {result['elapsed_s']} s\nreport: {out / 'report.md'}")
    return {"consensus": 0, "consensus-unverified": 0, "no-consensus": 1, "time-limit": 1}.get(result["status"], 2)


PANE_MODELS = {"Ornith": "dreammaker/Ornith-1.5-35B-A3B-MLX-8bit", "Qwen": "dreammaker/Qwen3.8-27B-4bit-MTP"}
# Pi thinking level per participant. Ornith only has on/off (any level above off is the same); Qwen has real levels through
# reasoning_effort (~/.pi/agent/models.json maps low/medium/high -> low/medium/xhigh), and xhigh runs away (S4 measurements).
PANE_THINKING = {"Ornith": "high", "Qwen": "medium"}
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def _orca_cli() -> str:
    if os.environ.get("ORCA_CLI_COMMAND"):
        return os.environ["ORCA_CLI_COMMAND"]
    if sys.platform.startswith("linux") and subprocess.run(["which", "orca-ide"], capture_output=True).returncode == 0:
        return "orca-ide"
    return "orca"


def _orca(*args: str) -> dict:
    """Run an Orca CLI command with --json and return the parsed output; raise RuntimeError with the reason on failure."""
    p = subprocess.run([_orca_cli(), *args, "--json"], capture_output=True, text=True, timeout=60)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"orca {' '.join(args[:2])}: no JSON (exit {p.returncode}): {(p.stderr or p.stdout).strip()[:300]}")
    if p.returncode != 0 or not d.get("ok", True):
        raise RuntimeError(f"orca {' '.join(args[:2])}: {d.get('error') or (p.stderr or p.stdout).strip()[:300]}")
    return d["result"]


def _alive(state: dict) -> bool:
    """The detached run is alive: the pid exists, is not a zombie, and is really this run (guards against pid reuse)."""
    pid = state.get("pid")
    try:
        os.kill(pid, 0)
        stat_ = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except (OSError, TypeError, IndexError):
        return False
    return stat_ != "Z" and "council.py" in cmd and state["out"] in cmd


def _state_path(out) -> Path:
    return Path(out).expanduser() / "state.json"


def _read_state(out) -> dict | None:
    try:
        return json.loads(_state_path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _close_panes(state: dict) -> list:
    notes = []
    for name, h in state.get("created", {}).items():
        try:
            _orca("terminal", "close", "--terminal", h)
            notes.append(f"closed pane {name} ({h})")
        except RuntimeError as e:
            notes.append(f"could not close pane {name} ({h}): {e}")
    return notes


def cmd_start(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief).expanduser().resolve()
    try:
        brief = load_brief(brief_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"brief: {e}", file=sys.stderr)
        return 2
    unknown = [n for n in brief.participants if n not in PANE_MODELS]
    if unknown:
        print(f"start can only open panes for {', '.join(PANE_MODELS)}; unknown participant(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    source = args.source or os.environ.get("ORCA_TERMINAL_HANDLE")
    if not source:
        print("no pane to split from: run inside an Orca terminal or pass --from HANDLE", file=sys.stderr)
        return 2
    levels = dict(PANE_THINKING)
    for spec in args.thinking or []:
        name, _, level = spec.partition("=")
        if name not in PANE_MODELS or level not in THINKING_LEVELS:
            print(f"--thinking needs NAME=LEVEL with NAME in {', '.join(PANE_MODELS)} and LEVEL in {', '.join(THINKING_LEVELS)}, got '{spec}'", file=sys.stderr)
            return 2
        levels[name] = level
    out = Path(args.out).expanduser().resolve() if args.out else Path("~/.cache/council").expanduser() / time.strftime("%Y%m%d-%H%M%S")
    if _state_path(out).exists():
        print(f"{out} already holds a run (state.json); use another --out", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    created: dict = {}
    try:
        for n in brief.participants:
            cmd = f"pi --model {PANE_MODELS[n]} --thinking {levels[n]}"
            created[n] = _orca("terminal", "split", "--terminal", source, "--direction", "vertical", "--command", cmd)["split"]["handle"]
        deadline = time.monotonic() + args.pane_wait
        for n, h in created.items():
            while _orca("terminal", "show", "--terminal", h)["terminal"].get("agentIdentity") != "pi":
                if time.monotonic() > deadline:
                    raise RuntimeError(f"pane {n} ({h}) did not show a pi agent within {args.pane_wait} s")
                time.sleep(2)
    except (RuntimeError, KeyError) as e:
        print(f"start failed: {e}", file=sys.stderr)
        for note in _close_panes({"created": created}):
            print(note, file=sys.stderr)
        return 2
    argv = [sys.executable, str(Path(__file__).resolve()), "run", str(brief_path), "--out", str(out), "--ask-script", args.ask_script]
    for n, h in created.items():
        argv += ["--pane", f"{n}={h}"]
    log = open(out / "run.log", "w")
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state = {"pid": proc.pid, "out": str(out), "brief": str(brief_path), "panes": created, "created": created,
             "started": time.time(), "stopped": None}
    _state_path(out).write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"started: {out}\npid: {proc.pid}\npanes: " + ", ".join(f"{n}={h}" for n, h in created.items()) +
          f"\ncheck: council.py status {out}\nend:   council.py stop {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser().resolve()
    state = _read_state(out)
    if not state:
        print(f"{out} has no readable state.json (not started with `start`?)", file=sys.stderr)
        return 2
    elapsed = int(time.time() - state["started"])
    try:
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = None
    if result:
        print(f"state: finished\nstatus: {result['status']}\nreason: {result['reason']}\nrounds: {result['rounds']}, elapsed: {result['elapsed_s']} s\nreport: {out / 'report.md'}")
    else:
        replies = sorted(out.glob("r[0-9][0-9]-*.reply.md"), key=lambda f: f.stat().st_mtime)
        last = f"{replies[-1].name} ({int(time.time() - replies[-1].stat().st_mtime)} s ago)" if replies else "none yet"
        alive = _alive(state)
        label = "stopped" if state.get("stopped") else "running" if alive else "dead"
        print(f"state: {label}\nelapsed: {elapsed} s\nreplies so far: {len(replies)}, last: {last}")
        if label == "dead":
            tail = (out / "run.log").read_text(errors="replace").strip().splitlines()[-5:] if (out / "run.log").exists() else []
            print("the run process ended without a result; run.log tail:\n" + "\n".join(tail))
    print("panes: " + ", ".join(f"{n}={h}" for n, h in state["panes"].items()) + (" (closed)" if state.get("panes_closed") else ""))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser().resolve()
    state = _read_state(out)
    if not state:
        print(f"{out} has no readable state.json (not started with `start`?)", file=sys.stderr)
        return 2
    notes = []
    if _alive(state):
        os.killpg(state["pid"], signal.SIGTERM)
        end = time.monotonic() + 5
        while _alive(state) and time.monotonic() < end:
            time.sleep(0.2)
        if _alive(state):
            os.killpg(state["pid"], signal.SIGKILL)
            notes.append("run process needed SIGKILL")
        notes.append("run process stopped; a run stopped mid-way writes no report (read the r*.reply.md files)")
        state["stopped"] = time.time()
    else:
        notes.append("run process was not running")
    if args.keep_panes:
        notes.append("panes left open (an agent may still be working in them)")
    elif not state.get("panes_closed"):
        notes += _close_panes(state)
        state["panes_closed"] = True
    _state_path(out).write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("\n".join(notes))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="council.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cc = sub.add_parser("check-cites", help="parse an items file and check its verified citations")
    cc.add_argument("file")
    cc.add_argument("--root", action="append", help="allowed evidence root (repeatable; default: current directory)")
    cc.add_argument("--format", choices=("json", "text"), default="json")
    cc.set_defaults(fn=cmd_check_cites)
    rn = sub.add_parser("run", help="run a council from a brief; exit 0 consensus(-unverified), 1 no-consensus/time-limit, 2 aborted/usage")
    rn.add_argument("brief")
    rn.add_argument("--pane", action="append", help="NAME=HANDLE of the Orca pane running that participant (repeatable)")
    rn.add_argument("--out", help="run directory (default ~/.cache/council/<timestamp>)")
    rn.add_argument("--ask-script", default=DEFAULT_ASK_SCRIPT, help="path of orca-ask.sh")
    rn.set_defaults(fn=cmd_run)
    st = sub.add_parser("start", help="open the participant panes, run the council detached, return at once (exit 2 on usage/pane failure)")
    st.add_argument("brief")
    st.add_argument("--out", help="run directory (default ~/.cache/council/<timestamp>)")
    st.add_argument("--from", dest="source", help="handle of the pane to split from (default $ORCA_TERMINAL_HANDLE)")
    st.add_argument("--ask-script", default=DEFAULT_ASK_SCRIPT, help="path of orca-ask.sh")
    st.add_argument("--thinking", action="append", help="NAME=LEVEL Pi thinking level for a participant (repeatable; default Ornith=high, Qwen=medium)")
    st.add_argument("--pane-wait", type=int, default=90, help="seconds to wait for each new pane to show a pi agent")
    st.set_defaults(fn=cmd_start)
    ss = sub.add_parser("status", help="state of a run started with `start`: running | finished | dead | stopped")
    ss.add_argument("out")
    ss.set_defaults(fn=cmd_status)
    sp = sub.add_parser("stop", help="kill the run if alive and close the panes `start` created")
    sp.add_argument("out")
    sp.add_argument("--keep-panes", action="store_true", help="do not close the panes")
    sp.set_defaults(fn=cmd_stop)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
