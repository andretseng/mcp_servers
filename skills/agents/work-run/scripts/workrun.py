#!/usr/bin/env python3
"""work-run (thin): ONE gated opencode or pi session writes the note from the user's request. By default that is all (the local model plus the harness); --review adds up to 3 adviser rounds, --final-editor codex a Codex rewrite.

  run         the work session; with --review the reviewer's problems go back into the SAME session
  check-note  the note check (frontmatter, sections, Provenance quotes, citations, code blocks) that the model may run itself

The gate blocks only on order (guidelines first, vault before project and SDK), scope (roots minus excluded sources, one draft file).
The note check is a report, not a gate: its result is appended to the note and given to the adviser.
Nothing here knows the topic: the topic is in the request.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import finaledit as fe  # noqa: E402
import notecheck as nc  # noqa: E402

KARPATHY = str(Path.home() / ".agents" / "skills" / "karpathy-guidelines" / "SKILL.md")
VAULT_ROOT = "/home/andre/llm-wiki"
DEFAULT_MODEL = "omlx/Ornith-1.5-35B-A3B-MLX-8bit#low"
EXEC_TEMPERATURE = 0.3  # the work session writes and copies, so it samples colder than the model's configured 0.6
DEFAULT_EXECUTOR = "opencode:omlx/Ornith-1.5-35B-A3B-MLX-8bit"
REVIEW_PY = Path(os.environ.get("ADVISER_REVIEW_PY", Path.home() / ".agents/skills/execute-with-adviser/scripts/adviser_review.py"))
MAX_NEW_BLOCKING = 3
REPORT_HEADING = "## Harness check (script, after the session)"


# ---------------------------------------------------------------- the request and the spec
def note_path(note: str) -> Path:
    p = Path(note).expanduser()
    return p if p.is_absolute() else Path(VAULT_ROOT) / p


def work_spec(args, wd: Path) -> dict:
    roots = [str(Path(r).expanduser()) for r in args.root]
    return {
        "mode": "work", "id": "W", "question": args.question, "classification": args.classification, "task": "Write the analysis note.",
        "scope_file": str(wd / "request.md"), "out": str(wd / "draft.md"), "final_note": str(note_path(args.note)), "seeds": roots, "required_reads": [],
        "extra_reads": [str(wd / "request.md"), KARPATHY], "first_reads": [KARPATHY], "vault_first": True, "deny": list(args.deny),
        "script": str(Path(__file__).resolve()), "work_file": str(wd / "work.json"), "workdir": str(wd), "gate_level": "thin",
    }


# ---------------------------------------------------------------- opencode sessions
def opencode_bin() -> str:
    return shutil.which("opencode") or "opencode"


def run_agent(prompt: str, gate_file: Path, workdir: Path, model: str, timeout: int, title: str, cwd: Path | None = None, session: str | None = None, agent: str = "opencode") -> tuple[int, str]:
    """One message to opencode. Output goes live to <workdir>/logs/live.log (GATE_THINKING=1 adds its thinking); on timeout the whole
    process group is killed, including the private server it started."""
    # opencode takes the project directory (and so its project config and its session list) from $PWD, not from the process cwd
    env = dict(os.environ, GATE_FILE=str(gate_file), PWD=str(cwd or workdir))
    if agent == "pi":
        env.update(pi_env(workdir))
        cmd = pi_command(model, prompt, session or pi_session_id(workdir))
    else:
        cmd = [opencode_bin(), "run", "--standalone", "--auto", "--model", model] + (["--session", session] if session else ["--title", title])
        if os.environ.get("GATE_THINKING"):
            cmd.append("--thinking")
        cmd.append(prompt)
    live = workdir / "logs" / "live.log"
    live.parent.mkdir(parents=True, exist_ok=True)
    with live.open("ab") as lf:
        lf.write(f"\n===== {title} ({dt.datetime.now().strftime('%H:%M:%S')}) =====\n".encode())
        lf.flush()
        start = live.stat().st_size
        proc = subprocess.Popen(cmd, cwd=cwd or workdir, env=env, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code, note = proc.wait(timeout=timeout), ""
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            code, note = 124, f"timeout after {timeout}s\n"
        lf.flush()
    with live.open("rb") as rf:
        rf.seek(start)
        return code, note + rf.read().decode("utf-8", "replace")[-4000:]


# ---------------------------------------------------------------- pi (the same gate, a different agent)
PI_DIR = HERE.parent / "pi-agent"  # its own config dir: models (context, sampling), settings, AGENTS.md and the gate extension; nothing of the user's pi setup


def pi_bin() -> str:
    return shutil.which("pi") or "pi"


def pi_env(workdir: Path) -> dict:
    return {"PI_CODING_AGENT_DIR": str(PI_DIR), "PI_CODING_AGENT_SESSION_DIR": str(workdir / "pi-sessions"), "PI_OFFLINE": "1", "GATE_SESSION": "pi-" + workdir.name}


def pi_session_id(workdir: Path) -> str:
    """A fixed id per workdir: `pi --session-id` creates the session if it is missing and continues it otherwise."""
    return "work-" + re.sub(r"[^A-Za-z0-9]+", "-", workdir.name).strip("-")


def pi_command(model: str, prompt: str, session: str) -> list[str]:
    name, _, level = model.partition("#")
    cmd = [pi_bin(), "-p", "--mode", "json", "-ne", "-e", str(PI_DIR / "extensions" / "work-gate.ts"), "--tools", "read,grep,find,ls,bash,write,edit",
           "--model", name, "--session-id", session]
    if level:
        cmd += ["--thinking", level]
    return cmd + [prompt]


def latest_session(cwd: Path) -> str | None:
    """The newest opencode session of a directory (the work session's own directory holds only its sessions)."""
    p = subprocess.run([opencode_bin(), "session", "list", "--standalone"], cwd=cwd, env=dict(os.environ, PWD=str(cwd)), capture_output=True, text=True)
    ids = [line.split()[0] for line in p.stdout.splitlines() if line.startswith("ses_")]
    return ids[0] if ids else None


def exec_config(model: str, temperature: float | None) -> dict:
    """Project config of the work session: only the sampling differs from the global config. opencode reads it at start-up.
    temperature None (`--temperature default`) sends none: the model keeps whatever sampling opencode and the server apply."""
    if temperature is None:
        return {}
    prov, _, rest = model.partition("/")
    return {"providers": {prov: {"models": {rest.split("#")[0]: {"body": {"temperature": temperature}}}}}}


# ---------------------------------------------------------------- the work session
NOTE_FORMAT = (
    "The note: (1) start with frontmatter (--- title, date YYYY-MM-DD, tags ---); "
    "(2) a Summary first, then one section per part of the answer in the order the signal or data really moves, then 'Known limits and open questions' (what you could not confirm, what the sources leave open), then 'References'; "
    "(3) the LAST section is '## Provenance', a table | id | status | source | quote | claim | with one row per claim, ids S1, S2, ...; "
    "status `verified`: source is `<absolute path>:<line>` and quote is the exact text of that line in backticks (write a | inside a cell as \\|); "
    "status `inferred`: source is `inference: S1, S2` (the claims it follows from) and quote is empty; status `unverified`: you could not confirm it; "
    "(4) every sentence of the body that states a fact ends with the claim ids it rests on, like [S3]; a sentence that rests on an inferred claim also says [inferred], on an unverified one [unverified]; "
    "(5) a code block copied from a source starts with ```c src=<absolute path>:<first line>-<last line> and holds exactly those lines. Never state as fact what no claim supports."
)


EVIDENCE_FIRST = ("For a point that says a claim is unsupported or a value is not shown: the reviewer only saw excerpts of your note, so the evidence may exist in the sources. "
                  "FIRST look for it (open the file that defines the symbol, grep -n for the value) and, if you find it, add or fix the Provenance row with the exact line and quote. "
                  "Mark the claim unverified only after you looked and found nothing; never delete or weaken a claim you can support.")


def work_prompt(spec: dict, feedback: list[str] | None = None) -> str:
    lines = [
        "You are doing ONE analysis job from start to end. Work in English.",
        f"The user's request, word for word: {spec['question']}",
        f"First read {KARPATHY} (working guidelines; the harness blocks every other call until you have).",
        "Research path, in this order (the harness enforces it): 1) the knowledge vault /home/andre/llm-wiki: grep -n or glob it for the keywords of the request and read the notes that match, "
        "2) the project source, 3) the SDK. The answers are unknown, so follow the evidence; do not assume. Reads outside these roots are blocked: " + ", ".join(spec["seeds"]) + ".",
        "When you handle a module (a peripheral, a driver, a register block, a protocol), read its vault note first, at least the part you need (grep -n for the keyword, then a narrow range); "
        "the vault holds module notes under 專業領域. The harness shows you the module notes your calls touched that you have not read yet.",
        "When you are unsure about something, first grep -n the vault notes for its keyword and use the lines you find; reason on your own only when they do not say, and then mark the claim `inferred` (with its premises) or `unverified`.",
    ]
    lines += [f"Output: write the note in the draft file {spec['out']} with write and edit (classification {spec['classification']}); the harness copies it to {spec['final_note']} when you stop.", NOTE_FORMAT,
              f"Nothing is checked while you write. When the draft is complete, run the check command (read-only, with the shell tool): python3 {spec['script']} check-note --work {spec['work_file']} --file {spec['out']}, and fix what it lists with edits; then reply with one short line and stop."]
    if feedback:
        lines.append(f"A reviewer found these problems in your previous note, which is the draft file {spec['out']}: " + " | ".join(feedback) + ". Read the draft, fix exactly these points with edits and change nothing else, then run the check command. Do not re-read sources you do not need. " + EVIDENCE_FIRST)
    return "\n".join(lines)


def continue_prompt(spec: dict, feedback: list[str] | None, nudge: bool) -> str:
    """The next message into the SAME work session: it still knows everything it read, so it does not explore again."""
    if feedback:
        return ("A reviewer read your finished note and found these problems: " + " | ".join(feedback) + f". Fix exactly these points with edit calls on the draft {spec['out']} and change nothing else, "
                "then run the check command. Answer from what you already know; read a source again to look for evidence a point says is missing, or to check a line. " + EVIDENCE_FIRST + " When you are done, reply with one short line and stop.")
    return (f"You stopped, but the draft file {spec['out']} does not exist or is empty. Write the note there now, from what you have found so far, following the note rules; "
            "then run the check command and reply with one short line.")


def strip_report(text: str) -> str:
    return re.sub(rf"\n*{re.escape(REPORT_HEADING)}.*\Z", "\n", text, flags=re.S)


def harness_report(errs: list[str], warns: list[str]) -> str:
    if not errs and not warns:
        return ""
    out = [REPORT_HEADING, "The note check was not enforced while the model wrote. What it found afterwards:"]
    out += [f"- error: {e}" for e in errs[:40]] + [f"- warning: {w}" for w in warns[:20]]
    return "\n" + "\n".join(out) + "\n"


def finalize(spec: dict) -> tuple[list[str], list[str]]:
    """Check the draft after the session (a report, not a gate) and publish it: the note at the vault path is the draft plus the report."""
    draft, final = Path(spec["out"]), Path(spec["final_note"])
    text = strip_report(draft.read_text(encoding="utf-8"))
    errs, warns, _ = nc.check_note(text, spec["seeds"], spec["deny"])
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(text.rstrip("\n") + "\n" + harness_report(errs, warns), encoding="utf-8")
    return errs, warns


def run_work(spec: dict, wd: Path, args, feedback: list[str] | None = None) -> dict:
    """ONE work session. The first message gives the whole job. When the model stops, the draft is checked and published; reviewer problems and
    a missing draft go into the same session as the next message (`--session`), so nothing is explored twice."""
    gf = wd / "work.json"
    gf.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    draft, final = Path(spec["out"]), Path(spec["final_note"])
    exec_dir = wd / "work"
    exec_dir.mkdir(exist_ok=True)
    agent = getattr(args, "agent", "opencode")
    if agent == "opencode":  # pi takes its sampling from pi-agent/models.json
        (exec_dir / "opencode.json").write_text(json.dumps(exec_config(args.model, getattr(args, "temperature", EXEC_TEMPERATURE)), indent=1), encoding="utf-8")
    if feedback and final.is_file():
        draft.write_text(strip_report(final.read_text(encoding="utf-8")), encoding="utf-8")  # a revision starts from the note that was reviewed
    sid_file = wd / "work.session"
    logs = wd / "logs"
    logs.mkdir(exist_ok=True)
    t0 = dt.datetime.now()
    rec = {"attempts": 0, "ok": False, "seconds": 0.0, "errors": [], "warnings": [], "session": None}
    for attempt in range(1, args.retries + 2):
        rec["attempts"] = attempt
        sid = sid_file.read_text(encoding="utf-8").strip() if sid_file.is_file() else None
        prompt = continue_prompt(spec, feedback if attempt == 1 else None, attempt > 1) if sid else work_prompt(spec, feedback)
        turn = len(list(logs.glob("work.turn*.log"))) + 1
        code, log = run_agent(prompt, gf, wd, args.model, args.timeout, "work" + (" (revision)" if feedback else ""), cwd=exec_dir, session=sid, agent=agent)
        (logs / f"work.turn{turn}.log").write_text(log, encoding="utf-8")
        if not sid:
            new = pi_session_id(wd) if agent == "pi" else latest_session(exec_dir)
            if new:
                sid_file.write_text(new, encoding="utf-8")
        elif code not in (0, 124):
            sid_file.unlink(missing_ok=True)  # the session could not be continued: the next turn starts a new one with the whole job
        rec["session"] = sid_file.read_text(encoding="utf-8").strip() if sid_file.is_file() else None
        if draft.is_file() and draft.stat().st_size > 200 and (not feedback or draft.stat().st_mtime >= t0.timestamp()):
            rec["errors"], rec["warnings"] = finalize(spec)
            rec["ok"] = True
            break
        rec["errors"] = [f"no draft was written (agent exit {code})"]
    rec["seconds"] = round((dt.datetime.now() - t0).total_seconds(), 1)
    return rec


# ---------------------------------------------------------------- review of the note (logic)
ITEM = re.compile(r"^\s*(?:\d+[.)]\s*)?\[(B|U|O)\]\s*([^:\n]{1,80}?)\s*[:\-–]\s*(.+)$", re.M)
PRIOR_REF = re.compile(r"\bR\d+-\d+\b")
NOTE_CONSTRAINTS = [
    "Judge the LOGIC of the note. Raise a [B] point only for: (a) a claim that contradicts its quoted source line; (b) an inferred claim that does not follow from its premises; (c) a statement of the body that its cited claims do not support, or a step of the path whose output does not match the next step's input. Everything else, including wording, is [U] or not worth a point.",
    "The evidence you may ask for is limited to the supplied sources and the notes and files that exist in the listed roots. Do not raise a point that needs anything else (a PCB, a schematic that is not in a note, a bench measurement, runtime behaviour). A claim already marked unverified needs no point for missing evidence.",
    "You see excerpts of the note, not the files. When a value or definition is not in the excerpts, do not ask for the claim to be marked unverified: say where the executor should look (the file or symbol) and what line would settle it, so it can find the evidence first. Ask for unverified only for a claim whose evidence the listed roots cannot hold.",
]


def parse_review_items(text: str, round_no: int) -> list[dict]:
    return [{"id": f"R{round_no}-{n}", "tag": m.group(1), "target": m.group(2).strip(), "text": m.group(3).strip()} for n, m in enumerate(ITEM.finditer(text or ""), 1)]


def settle_items(items: list[dict], prior: list[dict], rnd: int) -> tuple[list[dict], dict]:
    """Stable ids: an item that quotes a prior id keeps it; after round 1 at most MAX_NEW_BLOCKING new [B] count, the rest are demoted to [U]."""
    known = {p["id"] for p in prior if p["tag"] == "B"}
    seen, new_b, out = set(), 0, []
    for n, it in enumerate(items, 1):
        refs = [r for r in PRIOR_REF.findall(it["text"]) if r in known and r not in seen]
        if refs:
            it = {**it, "id": refs[0], "status": "persisting"}
            seen.add(refs[0])
        else:
            it = {**it, "id": f"R{rnd}-{n}", "status": "new"}
            if rnd > 1 and it["tag"] == "B":
                new_b += 1
                if new_b > MAX_NEW_BLOCKING:
                    it = {**it, "tag": "U", "text": f"(demoted from blocking: more than {MAX_NEW_BLOCKING} new blocking points) " + it["text"]}
        out.append(it)
    return out, {"persisting": len(seen), "new": sum(1 for i in out if i["status"] == "new"), "resolved": len(known - seen),
                 "demoted": sum(1 for i in out if i["text"].startswith("(demoted"))}


def prior_constraints(prior: list[dict], limit: int = 10) -> list[str]:
    """One short constraint per open blocking point (the adviser bridge caps each constraint at 1000 characters)."""
    blocking = [p for p in prior if p["tag"] == "B"]
    out = ["The previous round left these blocking points open, each given as its own constraint with its id. For each one that is STILL a problem, repeat it on its own line as "
           f"`[B] <target>: (<id>) <text>`, quoting its id. A point you do not repeat is treated as resolved. Do not re-word an old point as a new one. Raise at most {MAX_NEW_BLOCKING} NEW [B] points; put anything else under [U]."]
    out += [f"Open point {p['id']} [{p['target']}]: {p['text'][:300]}" for p in blocking[:limit]]
    if len(blocking) > limit:
        out.append(f"{len(blocking) - limit} more open points are not listed: " + ", ".join(p["id"] for p in blocking[limit:]))
    return out


def adviser_call(note: Path, cls: str, executor: str, extra: list[str], constraints: list[str], goal: str, question: str, position: str, changed: str = "") -> tuple[dict, int, str]:
    cmd = [sys.executable, str(REVIEW_PY), "--note", str(note), "--data-class", cls, "--executor", executor, "--goal", goal, "--question", question, "--position", position] + extra
    for c in constraints:
        cmd += ["--constraint", c]
    if changed:
        cmd += ["--changed", changed]
    p = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"full response: (\S+)", p.stdout)
    resp = json.loads(Path(m.group(1)).read_text(encoding="utf-8")) if m and Path(m.group(1)).is_file() else {}
    rnd = int(re.search(r"ADVISER ROUND (\d+)", p.stdout).group(1)) if re.search(r"ADVISER ROUND (\d+)", p.stdout) else 0
    return resp, rnd, (p.stderr or p.stdout)[-600:]


def run_review(spec: dict, wd: Path, args, rounds: int = 3, revise: bool = True) -> dict:
    """Adviser rounds on the published note. With revise=False the adviser only reads (one round): its problems become open items."""
    note = Path(spec["final_note"])
    history, prior, changed, items = [], [], "", []
    for rnd in range(1, rounds + 1):
        text = strip_report(note.read_text(encoding="utf-8"))
        errs, warns, claims = nc.check_note(text, spec["seeds"], spec["deny"])
        findings = wd / "findings.md"
        findings.write_text(nc.findings_text(spec["question"], claims) + "\n## HARNESS CHECK (script)\n" + ("\n".join(f"- {e}" for e in errs[:40]) or "- no errors") + "\n", encoding="utf-8")
        extra = ["--findings", str(findings), "--scope-file", str(wd / "request.md"), "--tagged"]
        for r in spec["seeds"]:
            extra += ["--root", r]
        cons = list(NOTE_CONSTRAINTS) + (prior_constraints(prior) if prior else [])
        resp, arnd, err = adviser_call(note, spec["classification"], args.executor, extra, cons,
                                       "Check the analysis note against the cited sources, within the scope of the request only.",
                                       "Is every claim supported by its cited source line, and does the logic hold from one step to the next?",
                                       "The note's Provenance table holds claims with quotes; the findings file lists what the harness check found afterwards (it was not enforced while the model wrote).", changed)
        items, stats = settle_items(parse_review_items(str(resp.get("reason", "")) + "\n" + str(resp.get("recommendation", "")), arnd), prior, arnd)
        decision = resp.get("decision")
        history.append({"round": arnd, "decision": decision, "items": len(items), "tags": "".join(sorted(i["tag"] for i in items)), **stats})
        if not resp:
            return {"outcome": "review-error", "history": history, "stderr": err, "open": []}
        if decision == "proceed" and resp.get("state") == "completed":
            return {"outcome": "proceed", "history": history, "open": []}
        if not items:
            return {"outcome": "untagged-answer", "history": history, "open": [str(resp.get("recommendation", ""))[:500]]}
        if rnd == rounds or not revise:
            break
        prior = [i for i in items if i["tag"] == "B"]
        fb = [f"{i['id']} [{i['tag']}] {i['target']}: {i['text']}" for i in items]
        rec = run_work(spec, wd, args, feedback=fb)
        if not rec["ok"]:
            return {"outcome": "work-failed", "history": history, "open": fb, "errors": rec["errors"]}
        changed = f"Revised the note for items {', '.join(i['id'] for i in items)}."
    left = [f"{i['id']} [{i['tag']}] {i['target']}: {i['text']}" for i in items if i["tag"] in ("B", "U")]
    return {"outcome": "open-items", "history": history, "open": left}


def append_open_items(note: Path, items: list[str]) -> None:
    if not items:
        return
    text = note.read_text(encoding="utf-8").rstrip("\n")
    text += "\n\n## Open items (review)\n" + "\n".join(f"- {i}" for i in items) + "\n"
    note.write_text(text, encoding="utf-8")


def apply_final_edit(spec: dict, wd: Path, args) -> dict:
    """Codex checks the published note against the sources, corrects it and rewrites it in Traditional Chinese. The local model's note is kept
    as <workdir>/note.ornith.md. The edit is repaired by the script where that loses no fact (relative paths become absolute), checked, and if
    it still fails the check Codex gets ONE round to fix the listed errors. An edit that still has errors is published only when it has fewer
    than the local note; otherwise (and when Codex cannot deliver) the local note stays as it is and the edit is kept as <workdir>/note.edited.md."""
    final = Path(spec["final_note"])
    original = final.read_text(encoding="utf-8")
    (wd / "note.ornith.md").write_text(original, encoding="utf-8")
    body = strip_report(original)
    errs, warns, _ = nc.check_note(body, spec["seeds"], spec["deny"])
    report = "\n".join([f"- error: {e}" for e in errs[:40]] + [f"- warning: {w}" for w in warns[:20]])
    t0 = dt.datetime.now()
    usage: dict = {}
    def secs() -> float:
        return round((dt.datetime.now() - t0).total_seconds(), 1)
    def call(note_text: str, rep: str) -> str:
        edited_ = fe.final_edit(spec["question"], note_text, rep, spec["seeds"], spec["deny"], spec["classification"], model=args.final_model, effort=args.final_effort,
                                timeout=args.final_timeout, codex=getattr(args, "codex", "codex"))
        for k, v in fe.LAST_USAGE.items():
            usage[k] = usage.get(k, 0) + (v if isinstance(v, int) else 0)
        return nc.normalize_paths(edited_, spec["seeds"])
    touched: list[str] = []
    try:
        edited = call(body, report)
        touched += fe.denied_touches(spec["deny"])
        errs2, warns2, claims2 = nc.check_note(edited, spec["seeds"], spec["deny"])
        repaired = False
        if errs2:  # one round: Codex fixes exactly what the script lists
            repaired = True
            edited = call(edited, "\n".join(f"- error: {e}" for e in errs2[:40]))
            touched += fe.denied_touches(spec["deny"])
            errs2, warns2, claims2 = nc.check_note(edited, spec["seeds"], spec["deny"])
    except fe.FinalEditError as exc:
        return {"outcome": "kept the local note", "detail": str(exc), "seconds": secs(), "codex_tokens": usage}
    (wd / "note.edited.md").write_text(edited, encoding="utf-8")
    touched = list(dict.fromkeys(touched))
    if errs2 and len(errs2) >= len(errs):
        return {"outcome": "kept the local note", "detail": f"the edit still fails the note check ({len(errs2)} error(s), the local note has {len(errs)}): " + " | ".join(errs2[:3]) + "; the edit is in note.edited.md",
                "seconds": secs(), "errors": len(errs2), "repair_round": repaired, "codex_tokens": usage, "denied_touches": touched}
    if touched:
        warns2 = warns2 + [f"Codex ran {len(touched)} command(s) that touch an excluded source: " + " | ".join(touched[:5])]
        print(f"WARNING: Codex touched an excluded source in {len(touched)} command(s); see run.json final_edit.denied_touches", flush=True)
    final.write_text(edited.rstrip("\n") + "\n" + harness_report(errs2, warns2), encoding="utf-8")
    return {"outcome": "published", "seconds": secs(), "errors": len(errs2), "warnings": len(warns2), "claims": len(claims2), "repair_round": repaired,
            "codex_tokens": usage, "denied_touches": touched}


def cmd_run(args) -> int:
    wd = Path(args.workdir).expanduser().resolve()
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "request.md").write_text(f"# Request\n## Question\n{args.question}\n", encoding="utf-8")
    spec = work_spec(args, wd)
    (wd / "draft.md").unlink(missing_ok=True)
    (wd / "work.session").unlink(missing_ok=True)
    run = {"started": dt.datetime.now().isoformat(timespec="seconds"), "work": None, "review": None}
    t0 = dt.datetime.now()
    rec = run_work(spec, wd, args)
    run["work"] = rec
    print(f"work: {'ok' if rec['ok'] else 'FAILED'} in {rec['seconds']}s, {rec['attempts']} attempt(s); note check after the session: {len(rec['errors'])} error(s), {len(rec['warnings'])} warning(s)", flush=True)
    (wd / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    if rec["ok"] and args.final_editor == "codex":
        run["final_edit"] = apply_final_edit(spec, wd, args)
        print(f"final edit ({args.final_model}): {run['final_edit']['outcome']}" + (f" in {run['final_edit']['seconds']}s" if run["final_edit"].get("seconds") else "")
              + (f" - {run['final_edit']['detail']}" if run["final_edit"].get("detail") else "") + f"; note check afterwards: {run['final_edit'].get('errors', '?')} error(s)", flush=True)
        (wd / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    if rec["ok"] and not args.no_review and ((args.final_editor != "codex" and getattr(args, "review", False)) or (args.final_editor == "codex" and args.review_after_edit)):
        run["review"] = run_review(spec, wd, args, rounds=1 if args.final_editor == "codex" else args.review_rounds, revise=args.final_editor != "codex")
        print("review: " + json.dumps({k: run["review"][k] for k in ("outcome", "history")}), flush=True)
        if run["review"]["outcome"] == "open-items":
            append_open_items(Path(spec["final_note"]), run["review"]["open"])
        for o in run["review"].get("open", []):
            print("  open: " + o)
    run["total_seconds"] = round((dt.datetime.now() - t0).total_seconds(), 1)
    (wd / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    try:
        subprocess.run([sys.executable, str(HERE / ("pitrace.py" if getattr(args, "agent", "opencode") == "pi" else "sessiontrace.py")), "--workdir", str(wd)], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        pass
    print(f"total {run['total_seconds']}s; state: {wd / 'run.json'}")
    return 0 if rec["ok"] else 1


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="the work session writes the note, then the adviser reviews it")
    s.add_argument("--question", required=True, help="the user's request, word for word")
    s.add_argument("--note", required=True, help="the vault path of the note (relative to the vault, or absolute)")
    s.add_argument("--classification", default="public", choices=("public", "private", "restricted"))
    s.add_argument("--root", action="append", required=True, help="a folder the session may read (repeatable)")
    s.add_argument("--deny", action="append", default=[], help="a path substring the session must not touch (repeatable)")
    s.add_argument("--workdir", required=True)
    s.add_argument("--agent", choices=("opencode", "pi"), default="opencode", help="which agent runs the work session (the same gate applies to both)")
    s.add_argument("--model", default=DEFAULT_MODEL); s.add_argument("--temperature", type=lambda v: None if v == "default" else float(v), default=EXEC_TEMPERATURE, help="a number, or `default` to send none")
    s.add_argument("--timeout", type=int, default=2400); s.add_argument("--retries", type=int, default=1)
    s.add_argument("--executor", default=DEFAULT_EXECUTOR); s.add_argument("--no-review", action="store_true", help="kept for old command lines: review is off unless --review is given")
    s.add_argument("--review", action="store_true", help="run the adviser (Codex) review loop on the note; off by default (the field flow is the local model plus the harness only)")
    s.add_argument("--final-editor", choices=("codex", "none"), default="none", help="none (default): the local model's note is the result; codex: Codex checks, corrects and rewrites it in Traditional Chinese (public and private material only)")
    s.add_argument("--final-model", default=fe.DEFAULT_MODEL); s.add_argument("--final-effort", default=fe.DEFAULT_EFFORT); s.add_argument("--final-timeout", type=int, default=1200)
    s.add_argument("--review-rounds", type=int, default=3, help="adviser rounds when the reviewer (not the final editor) checks the note; 3 by default")
    s.add_argument("--review-after-edit", action="store_true", help="one read-only adviser round on the edited note; its problems become open items")
    s = sub.add_parser("check-note"); s.add_argument("--work", required=True); s.add_argument("--file", required=True)
    a = ap.parse_args()
    if a.cmd == "run":
        os.environ["ADVISER_ROUND_LIMIT"] = str(max(3, a.review_rounds))
        return cmd_run(a)
    spec = json.loads(Path(a.work).read_text(encoding="utf-8"))
    errs, warns, claims = nc.check_note(strip_report(Path(a.file).read_text(encoding="utf-8")), spec["seeds"], spec["deny"])
    for e in errs[:40]:
        print(f"- error: {e}", file=sys.stderr)
    warns = [x for x in warns if "of the body sentences cite a claim" not in x]  # the share of cited sentences is for the report after the session; the model chases it otherwise
    for w in warns[:20]:
        print(f"- warning: {w}", file=sys.stderr)
    print(f"{len(claims)} claims, {len(errs)} error(s), {len(warns)} warning(s)")
    return 2 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
