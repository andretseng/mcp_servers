#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Failure-rate report from the review ledger (reviews.jsonl + dispositions.jsonl).

A "context" is one review chain (same context_id). Outcomes per context:
  agreed_proceed          round 1 proceed, executor adopted
  executor_accepted       reviewer flagged (change/stop/need-evidence), executor adopted (unverified)
  reviewer_overruled      executor rejected, Codex said proceed      -> reviewer false alarm
  executor_overruled      executor rejected, Codex said change/stop  -> executor was wrong (Codex-confirmed)
  inconclusive            Codex answered need-evidence / failed
  unresolved              executor rejected, no completed Codex round (e.g. dry-run arbiter, user escalation)
  (no disposition logged, e.g. Codex CLI: adopt/reject is inferred from the round sequence)
  no_review               no completed review round
Test backends (scripted, fake) are excluded unless --include-test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

FLAGGED = {"change", "stop", "need-evidence"}


def default_dir() -> Path:
    return Path(os.environ.get("ADVISER_LEDGER_DIR") or Path.home() / ".local" / "state" / "adviser-ledger")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def infer_verdicts(rounds: list[dict[str, Any]]) -> dict[int, str]:
    """No executor disposition was logged (e.g. Codex CLI): read it off the round sequence.

    The protocol only sends round k+1 when the executor rejected round k (or, after
    need-evidence, brings new evidence). The last round is assumed adopted; that part is a guess.
    """
    verdicts: dict[int, str] = {}
    indexes = {r["consult_index"] for r in rounds}
    for r in rounds:
        if r["state"] != "completed":
            continue
        again = r["consult_index"] + 1 in indexes
        verdicts[r["consult_index"]] = "reject" if again and r["decision"] != "need-evidence" else "adopt"
    return verdicts


def classify(rounds: list[dict[str, Any]], verdicts: dict[int, str]) -> str:
    done = [r for r in rounds if r["state"] == "completed"]
    if not done:
        return "no_review"
    if not verdicts:
        return "no_disposition"
    if "reject" in verdicts.values():
        arbiter = [r for r in rounds if r["backend"] == "codex" and r["consult_index"] >= 2]
        arbiter_done = [r for r in arbiter if r["state"] == "completed"]
        if not arbiter_done:
            return "inconclusive" if arbiter else "unresolved"
        final = arbiter_done[-1]["decision"]
        if final == "proceed":
            return "reviewer_overruled"
        if final in {"change", "stop"}:
            return "executor_overruled"
        return "inconclusive"
    first = done[0]["decision"]
    return "agreed_proceed" if first == "proceed" else "executor_accepted"


def build(ledger: Path, include_test: bool = False) -> dict[str, Any]:
    reviews = [r for r in read_jsonl(ledger / "reviews.jsonl") if include_test or not r.get("is_test")]
    disp = read_jsonl(ledger / "dispositions.jsonl")
    by_ctx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reviews:
        by_ctx[r["context_id"]].append(r)
    verdicts: dict[str, dict[int, str]] = defaultdict(dict)
    for d in disp:
        if d.get("event") == "disposition" and d.get("context_id"):
            verdicts[d["context_id"]][d["round"]] = d["verdict"]
    contexts = []
    for ctx, rounds in by_ctx.items():
        rounds.sort(key=lambda r: (r["consult_index"], r["ts"]))
        known = verdicts.get(ctx, {})
        inferred = not known
        if inferred:
            known = infer_verdicts(rounds)
        contexts.append(
            {
                "context_id": ctx,
                "executor": rounds[0].get("executor", "unknown"),
                "reviewer": f"{rounds[0]['backend']}:{rounds[0].get('model')}:{rounds[0].get('effort')}",
                "first_decision": next((r["decision"] for r in rounds if r["state"] == "completed"), None),
                "rounds": len(rounds),
                "inferred": inferred,
                "outcome": classify(rounds, known),
            }
        )
    executors: dict[str, dict[str, Any]] = {}
    for c in contexts:
        e = executors.setdefault(c["executor"], {"contexts": 0, "outcomes": Counter(), "flagged": 0, "inferred": 0})
        e["contexts"] += 1
        e["inferred"] += 1 if c["inferred"] else 0
        e["outcomes"][c["outcome"]] += 1
        if c["first_decision"] in FLAGGED:
            e["flagged"] += 1
    reviewers: dict[str, dict[str, Any]] = {}
    for r in reviews:
        key = f"{r['backend']}:{r.get('model')}"
        v = reviewers.setdefault(key, {"rounds": 0, "failed": 0, "decisions": Counter(), "latency": [], "attempts_gt1": 0})
        v["rounds"] += 1
        if r["state"] in {"failed", "timeout"}:
            v["failed"] += 1
        else:
            v["decisions"][r.get("decision")] += 1
        v["latency"].append(r.get("latency_s") or 0)
        if (r.get("attempts") or 0) > 1:
            v["attempts_gt1"] += 1
    codex = [r for r in reviews if r["backend"] == "codex"]
    return {"contexts": contexts, "executors": executors, "reviewers": reviewers, "codex_rounds": codex}


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def render(rep: dict[str, Any]) -> str:
    out = ["# Review ledger report", ""]
    out.append("## Executors (per context = one review chain)")
    for name, e in sorted(rep["executors"].items()):
        o = e["outcomes"]
        n = e["contexts"]
        confirmed = o["executor_overruled"]
        out.append(f"- **{name}**: {n} contexts; reviewer flagged on round 1: {pct(e['flagged'], n)}")
        out.append(
            f"  - Codex-confirmed executor errors: {pct(confirmed, n)}; reviewer false alarms (Codex sided with executor): "
            f"{pct(o['reviewer_overruled'], n)}; accepted a flag without arbitration (unverified): {pct(o['executor_accepted'], n)}; "
            f"agreed on proceed: {pct(o['agreed_proceed'], n)}; unresolved: {o['unresolved']}; inconclusive: {o['inconclusive']}; "
            f"no completed review: {o['no_review']}"
        )
        if e["inferred"]:
            out.append(f"  - {e['inferred']} of {n} contexts have no logged dispositions (Codex CLI, or logging off): "
                       "adopt/reject was inferred from the round sequence; the last round is assumed adopted")
    out += ["", "## Reviewers (per round)"]
    for key, v in sorted(rep["reviewers"].items()):
        lat = sum(v["latency"]) / len(v["latency"]) if v["latency"] else 0
        dec = ", ".join(f"{k}={n}" for k, n in sorted(v["decisions"].items(), key=lambda kv: str(kv[0])))
        out.append(
            f"- **{key}**: {v['rounds']} rounds; failed/timeout {pct(v['failed'], v['rounds'])}; needed retry {v['attempts_gt1']}; "
            f"mean latency {lat:.0f}s; decisions: {dec or 'none'}"
        )
    out += ["", f"## Codex rounds ({len(rep['codex_rounds'])})"]
    for r in rep["codex_rounds"]:
        out.append(
            f"- {r['ts']} | {r.get('executor')} | ctx {r['context_id']} #{r['consult_index']} | {r['state']}/{r.get('decision')} "
            f"| {r.get('latency_s')}s | {(r.get('recommendation') or '(restricted or failed)')[:160]}"
        )
    out += ["", "Caveats: small n; 'executor_accepted' is not verified correct or wrong; test backends excluded; "
            "a dry-run arbiter other than Codex shows up as 'unresolved'. Comparing agents (pi: / codex:) "
            "is only fair per review chain: pi forces a review before it finishes, Codex does not, and the "
            "ledger does not count tasks that never reached a review, so review RATES per task cannot be compared."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=default_dir())
    p.add_argument("--include-test", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    rep = build(a.dir, a.include_test)
    if not rep["contexts"] and not rep["reviewers"]:
        print(f"no ledger data in {a.dir}", file=sys.stderr)
        return 1
    if a.json:
        rep["executors"] = {k: {**v, "outcomes": dict(v["outcomes"])} for k, v in rep["executors"].items()}
        rep["reviewers"] = {k: {**v, "decisions": dict(v["decisions"])} for k, v in rep["reviewers"].items()}
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
