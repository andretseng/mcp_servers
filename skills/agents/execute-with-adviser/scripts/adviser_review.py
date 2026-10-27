#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""One-command adviser review for an executor that wrote a note from findings.

Builds the request, packs the evidence from the artifact and the findings file,
checks it before spending an adviser round, runs adviser_bridge.py, saves the full
response and prints only a short summary. Exit codes follow the bridge:
0 completed, 4 needs more evidence, 3 rejected/limit, 2 invalid input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import evidence_pack as packer  # noqa: E402

ROUND_LIMIT = int(os.environ.get("ADVISER_ROUND_LIMIT", "3"))  # 3 unless a test run raises it
LEDGER = Path.home() / ".local" / "state" / "adviser-ledger" / "reviews.jsonl"
OUT_ROOT = Path.home() / ".local" / "state" / "agent-checkpoints" / "reviews"
CJK = re.compile(r"[㐀-鿿豈-﫿　-〿＀-￯]")
SUMMARY_CHARS = 1500


def ledger_rounds(context_id: str) -> list[dict]:
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "review-round" and row.get("context_id") == context_id:
            rows.append(row)
    return rows


def default_context_id(note: Path, findings: str = "") -> str:
    """ASCII slug plus a short hash of note path AND findings path (findings are per session), so a later
    run on the same note never inherits an earlier run's ledger rows, and non-ASCII names cannot collide."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", note.stem).strip("_")[:60] or "note"
    key = f"{note}|{findings}"
    return f"{slug}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"


NEED_REF = re.compile(
    r"(?P<file>[A-Za-z0-9_][A-Za-z0-9_./+-]*\.(?:c|h|syscfg))\b[`'\"]?\s*[:,(]?\s*(?:lines?\s*)?L?(?P<a>\d+)?(?:\s*[-–—]\s*L?(?P<b>\d+))?"
)


def auto_need_lines(response: dict, round_no: int) -> list[str]:
    """NEED lines for the sources the adviser asked about, so the R4 gate lets the executor fetch them."""
    text = " ".join(str(response.get(k, "")) for k in ("change_evidence", "reason", "recommendation", "executor_next_step"))
    seen: dict[tuple[str, str], None] = {}
    for m in NEED_REF.finditer(text):
        name = m.group("file").rsplit("/", 1)[-1]
        span = f"L{m.group('a')}" + (f"-{m.group('b')}" if m.group("b") else "") if m.group("a") else "definition"
        seen.setdefault((name, span), None)
    return [f"NEED: {name} {span} (asked by the adviser, round {round_no})" for name, span in seen]


TAGGED_CONSTRAINTS = [
    "Put every point of your answer on its own line in the `reason` field, in exactly this form: `[B] <id>: <text>`, `[U] <id>: <text>` or `[O] <id>: <text>`, where <id> is a claim id such as C2-03 or a card id such as C2 (use SEAM for a unit mismatch between stages, GENERAL only if nothing else fits).",
    "[B] = blocking: the cited source directly contradicts the claim, or two stages use inconsistent units. [U] = unsupported: the supplied evidence is not enough to confirm the claim (the executor will mark it unverified; this does not need another round, so do not ask for more evidence). [O] = outside the scope of the task (the executor will delete it). Use decision `proceed` when you have no [B] point.",
    "The evidence you may ask for is limited to the supplied sources and the notes and files that exist in the listed roots. Do not raise a point that needs anything else (a PCB, a schematic that is not in a note, a bench measurement, runtime behaviour). If a note in the roots probably holds the missing evidence, name that note in the point; if nothing there can hold it, the claim stays marked unverified and is not a point. A claim already marked unverified needs no point for missing evidence.",
    "From round 2 on: first say which earlier points are resolved or still open by their ids; add a new point only if it concerns text that changed since the previous round.",
]


def apply_scope(request: dict, scope_file: str) -> None:
    """Put the user's task and the out-of-scope list into the request, so the adviser does not review what was never asked."""
    text = Path(scope_file).expanduser().read_text(encoding="utf-8")
    section = lambda name: re.search(rf"(?ims)^##\s+{name}\s*\n(.*?)(?=^##\s|\Z)", text)  # noqa: E731
    q = section("Question")
    question = q.group(1).strip() if q else ""
    o = section("Out of scope")
    out_of_scope = [m.group(1).strip() for m in re.finditer(r"(?m)^\s*[-*]\s+(.*)$", o.group(1))] if o else []
    request["goal"] = f"TASK (verbatim): {question} | " + request["goal"]
    if out_of_scope:
        request["constraints"].append(
            "The task is limited to what is stated above. Content about these topics is out of scope; tag it [O], do not ask for evidence for it: " + "; ".join(out_of_scope)
        )


def english_only(label: str, text: str) -> None:
    if text and len(CJK.findall(text)) / len(text) > 0.05:
        fail(2, f"{label} must be written in English (found CJK text). Keep Chinese only in the note and in file names.")


def fail(code: int, message: str):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def build_request(args, context_id: str, round_no: int, previous: str) -> dict:
    attempts = [
        {
            "method": "Read the project source, SDK headers and TRM notes; recorded every fact in the findings file with file:line; wrote the note from those findings.",
            "result": "Draft note written; the evidence below is the note plus the source lines the findings cite.",
            "ruled_out": [],
        }
    ]
    if round_no > 1:
        attempts.append(
            {
                "method": f"Round {round_no - 1} adviser answer: {previous[:1500]}",
                "result": args.changed,
                "ruled_out": [],
            }
        )
    return {
        "protocol": "executor-adviser/0.2",
        "id": f"{context_id}-r{round_no}",
        "context_id": context_id,
        "state": "submitted",
        "trigger": "result-review",
        "trigger_event": {
            "event": "advice-triggered",
            "event_id": f"evt-{context_id}-r{round_no}",
            "trigger": "result-review",
            "trigger_source": "automatic",
            "condition": "The executor finished a draft and asks for the required final review before reporting it.",
            "blocking": True,
            "evidence_ready": True,
        },
        "review_scope": "evidence-read",
        "goal": args.goal,
        "question": args.question,
        "evidence": [],
        "attempts": attempts,
        "constraints": [
            "Answer from the supplied evidence; do not assume facts about the project that are not cited. For public specifications and protocols you may use your own knowledge, but label it \"outside the supplied evidence\" and say which specification it comes from.",
            "Mark any claim you cannot confirm from the evidence as unconfirmed and name the exact file and lines you still need.",
            "Distinguish verified facts from inference; the note tags inferences with [INFERENCE].",
            "Do not review only the wording. Find the premises the conclusion stands on, and check each one: a code comment, a variable name, or a note is a claim, not evidence. If a premise rests only on a comment or a note, challenge it. Compare it with the executable code, the original specification, and your own knowledge of the specification; where they disagree, say so as a blocking point and say what the premise should be.",
            "List EVERY contradiction or unsupported claim you can find in the supplied evidence in this answer, not only the most important ones. The executor has at most three rounds, and anything you leave out now costs a whole round later.",
            "Name the exact file and line range of any source you still need, so the executor can fetch it in one step.",
        ],
        "executor_position": args.position,
        "fallback": "STOP",
        "data_classification": args.data_class,
        "consult_index": round_no,
        "consult_limit": ROUND_LIMIT,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--note", required=True, help="the artifact under review")
    ap.add_argument("--findings", help="findings file; every file:line becomes evidence")
    ap.add_argument("--root", action="append", default=[], help="directory that holds the cited files (repeatable)")
    ap.add_argument("--question", required=True, help="what to check (English)")
    ap.add_argument("--goal", required=True, help="what the note must achieve (English)")
    ap.add_argument("--position", required=True, help="your position / what you want confirmed (English)")
    ap.add_argument("--changed", default="", help="round 2+: what you fixed since the last round (English)")
    ap.add_argument("--data-class", required=True, choices=("public", "private", "restricted"))
    ap.add_argument("--executor", required=True, help="e.g. opencode:omlx/Ornith-1.5-35B-A3B-MLX-8bit")
    ap.add_argument("--context-id", help="default: the note file name without extension")
    ap.add_argument("--backend", help="default: codex (Sol, falls back to claude opus) for public/private, omlx for restricted")
    ap.add_argument("--context", type=int, default=12, help="lines of context around each findings reference")
    ap.add_argument("--scope-file", help="work-run scope.md: adds the task and the out-of-scope list to the request")
    ap.add_argument("--constraint", action="append", default=[], help="extra constraint for the adviser (English, repeatable)")
    ap.add_argument("--tagged", action="store_true", help="ask for [B]/[U]/[O]-tagged points keyed by claim id (work-run flow)")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--bridge", default=str(SCRIPT_DIR / "adviser_bridge.py"), help=argparse.SUPPRESS)
    args = ap.parse_args()

    for label, text in (("--question", args.question), ("--goal", args.goal), ("--position", args.position), ("--changed", args.changed), *[("--constraint", c) for c in args.constraint]):
        english_only(label, text)
    note = Path(args.note).expanduser().resolve()
    if not note.is_file():
        fail(2, f"note not found: {note}")
    context_id = args.context_id or default_context_id(note, str(Path(args.findings).expanduser().resolve()) if args.findings else "")
    # Tiers (user, 2026-10-24): public/private = Codex Sol first, cloud Claude Opus second (same-round failover); restricted (confidential) = local Qwen only, never a cloud backend.
    restricted = args.data_class == "restricted"
    allowed = {"omlx"} if restricted else {"codex", "claude"}
    need = "omlx" if restricted else "codex (or claude after a failover)"
    backend = args.backend or ("omlx" if restricted else "codex")
    if backend not in allowed:
        fail(3, "restricted data may only be reviewed by the local omlx backend" if restricted else "public/private data are reviewed by codex (tier 1) or claude (tier 2); omlx is not a reviewer for them")

    rounds = ledger_rounds(context_id)
    # Only an approving answer ends the review. `change` means "fix this, then show me again".
    done = [r for r in rounds if r.get("state") == "completed" and r.get("backend") in allowed and r.get("decision") == "proceed"]
    if done:
        saved = sorted((OUT_ROOT / context_id).glob("round-*.response.json")) if (OUT_ROOT / context_id).is_dir() else []
        text = ""
        if saved:
            try:
                text = str(json.loads(saved[-1].read_text(encoding="utf-8")).get("recommendation", ""))
            except (OSError, json.JSONDecodeError):
                text = ""
        text = text or str(done[-1].get("recommendation", ""))
        print(f"Review already completed for {context_id} ({done[-1].get('decision')} from {need}). Do not run another round; apply what it said and report.")
        print("Adviser answer: " + text[:SUMMARY_CHARS * 2])
        return 0
    if len(rounds) >= ROUND_LIMIT:
        print(
            f"REVIEW LIMIT: {len(rounds)} rounds are used for {context_id} and none approved (proceed) by {need}. "
            "Stop. Report the result as NOT APPROVED: list what the adviser last asked for, what you fixed, and let the user decide.",
            file=sys.stderr,
        )
        return 3
    round_no = len(rounds) + 1
    if round_no > 1 and not args.changed:
        fail(2, "--changed is required from round 2: say in English what you fixed or added since the last round")

    out_dir = OUT_ROOT / context_id
    out_dir.mkdir(parents=True, exist_ok=True)
    prev_path = out_dir / f"round-{round_no - 1}.response.json"
    previous = ""
    if round_no > 1 and prev_path.exists():
        try:
            previous = str(json.loads(prev_path.read_text(encoding="utf-8")).get("recommendation", ""))
        except (OSError, json.JSONDecodeError):
            previous = ""

    request = build_request(args, context_id, round_no, previous)
    if args.scope_file:
        apply_scope(request, args.scope_file)
    if args.tagged:
        request["constraints"] += TAGGED_CONSTRAINTS
    request["constraints"] += args.constraint
    evidence = []
    try:
        evidence += packer.artifact_evidence(str(note), max_lines=packer.DEFAULT_MAX_LINES)
        if args.findings:
            if not args.root:
                fail(2, "--findings needs at least one --root directory")
            roots = [Path(r).expanduser().resolve() for r in args.root]
            room = 24 - len(evidence)
            # Shrink the context around each cited line, then merge more aggressively, until it fits the 24-item limit.
            for ctx, gap in ((args.context, 5), (8, 5), (4, 10), (2, 20), (0, 40), (0, 120)):
                extra = packer.findings_evidence(
                    args.findings, roots, max_lines=packer.DEFAULT_MAX_LINES, max_items=10**6, context=min(ctx, args.context), merge_gap=gap,
                )
                if len(extra) <= room:
                    break
            else:
                fail(2, f"{len(extra)} source ranges plus {len(evidence)} note chunks exceed the 24-item limit even after merging. Cite fewer, larger groups of lines per file in the findings.")
            evidence += extra
        if len(evidence) > 24:
            fail(2, f"{len(evidence)} evidence items; the request allows 24.")
        request["evidence"] = evidence
        if backend == "omlx":
            packed = request  # omlx reads the listed local files itself
        else:
            packed = packer.pack_request(request, base_dir=Path.cwd())
    except packer.EvidencePackError as exc:
        fail(4, f"evidence not ready (no adviser round was spent): {exc}")

    if backend != "omlx":
        empty = [e["uri"] for e in packed["evidence"] if not str(e.get("excerpt", "")).strip()]
        if empty:
            fail(4, f"evidence items without an excerpt (no round spent): {empty[:3]}")

    req_path = out_dir / f"round-{round_no}.request.json"
    req_path.write_text(json.dumps(packed, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = [
        sys.executable, args.bridge, "--request", str(req_path),
        "--backend", backend, "--timeout", str(args.timeout), "--executor", args.executor, "--pretty",
    ]
    if backend == "codex":
        cmd += ["--failover", "claude"]   # tier 2: Claude Opus answers the same round when Sol cannot run; restricted data never reach it (backend is omlx)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    resp_path = out_dir / f"round-{round_no}.response.json"
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        response = None
    if response is None:
        print(f"Bridge exit {proc.returncode}, no JSON response. stderr: {proc.stderr.strip()[:800]}", file=sys.stderr)
        return proc.returncode or 1
    resp_path.write_text(json.dumps(response, ensure_ascii=False, indent=1), encoding="utf-8")
    decision = response.get("decision")
    state = response.get("state")
    notes = []
    if decision != "proceed" and args.findings:
        needs = auto_need_lines(response, round_no)
        if needs:
            with open(Path(args.findings).expanduser(), "a", encoding="utf-8") as fh:
                fh.write("\n## Adviser requests (round %d)\n" % round_no + "\n".join(needs) + "\n")
            notes.append(f"{len(needs)} NEED lines were added to your findings file for the sources the adviser asked about; you may now read exactly those ranges.")
    size_file = out_dir / f"round-{round_no}.note_size"
    size_file.write_text(str(note.stat().st_size), encoding="utf-8")
    prev_size_file = out_dir / f"round-{round_no - 1}.note_size"
    if round_no > 1 and prev_size_file.exists():
        try:
            before, after = int(prev_size_file.read_text()), note.stat().st_size
            if before and after > before * 1.1:
                notes.append(f"The note grew from {before} to {after} bytes since the last round. New text is new review surface: delete claims you cannot verify instead of adding hedges or sections.")
        except (OSError, ValueError):
            pass
    rec = str(response.get("recommendation", "")).strip()
    print(f"ADVISER ROUND {round_no}/{ROUND_LIMIT}  backend={backend}  state={state}  decision={decision}")
    print(rec[:SUMMARY_CHARS] + (" ...[truncated; full text in the response file]" if len(rec) > SUMMARY_CHARS else ""))
    print(f"full response: {resp_path}")
    for n in notes:
        print("NOTE: " + n)
    if state == "completed" and decision == "proceed":
        print("Review approved. Report the result with this decision.")
    elif state == "completed" and round_no >= ROUND_LIMIT:
        print("Round limit reached; the last answer asked for changes. Apply them, then report NOT APPROVED with what is still open.")
    elif state == "completed":
        print(
            "The adviser asks for changes (decision: change). Fix each point in the note; if you cannot verify a claim, delete it rather than hedging it. "
            "Add needed facts to the findings file as `claim | file:line | verified`, then run this command again with --changed \"<what you fixed, in English>\" so it can confirm."
        )
    elif round_no >= ROUND_LIMIT:
        print("Round limit reached without an approving review. Report NOT APPROVED and tell the user what is still open.")
    else:
        print(
            "Not complete. For each point: if the adviser found an error, fix the note; if it asked for evidence, record the fact in the findings "
            "file as `claim | file:line | verified`. Then run this command again with --changed \"<what you fixed or added, in English>\"."
        )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
