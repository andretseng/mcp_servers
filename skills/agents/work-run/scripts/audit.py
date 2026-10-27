#!/usr/bin/env python3
"""Process audit of a work-run, from the gate log: did every session follow the harness order?

Per session: the guideline skill read first, vault before project/SDK, the indexed SDK tree not touched without a
a draft or scope written. Sessions with no gate id ran outside the gate. Prints lookup-before-claim
(a lower bound) and how much of the index was read; neither fails the audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notecheck as nc  # noqa: E402

VAULT = "/home/andre/llm-wiki"
PROJ_SDK = ("/home/andre/workspace_ccstheia", "/home/andre/ti")
IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`|\b([A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]|[A-Z]{4,}[0-9]*|[a-z]+_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*)\b")


def paths_in(inp: str) -> list[str]:
    return re.findall(r"(?:^|[\s\"'=:])(/home/[^\s\"'|;&<>()\\]+)", inp.replace('\\"', '"'))


def lookup_before_claim(spec: dict, allowed: list[dict], last_write: int) -> tuple[int, list[str]]:
    out = Path(spec.get("out") or "")
    if not out.is_file():
        return 0, []
    _, _, claims = nc.check_note(out.read_text(encoding="utf-8"))
    searches = " ".join(c["input"].lower() for c in allowed[:last_write] if c["tool"] in ("grep", "glob", "shell", "bash"))
    checked, missing = 0, []
    for c in claims:
        if c["status"] == "verified":
            continue
        idents = {(x or y).lower() for x, y in IDENT.findall(c["claim"])}
        if not idents:
            continue
        checked += 1
        if not any(i in searches for i in idents):
            missing.append(c["id"])
    return checked, missing


def audit(workdir: Path, gate_log: Path) -> tuple[list[str], list[str]]:
    rows = [json.loads(l) for l in gate_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    specs = {}
    for name in ("work.json", "plan.gate.json"):
        f = workdir / name
        if f.is_file():
            s = json.loads(f.read_text(encoding="utf-8"))
            specs[s["id"]] = s
    report, fails = [], []
    sessions: dict[tuple, list[dict]] = {}
    for r in rows:
        sessions.setdefault((r.get("card"), r["session"]), []).append(r)
    for (gid, sid), calls in sessions.items():
        if gid is None:
            fails.append(f"session {sid[-8:]}: {len(calls)} calls ran OUTSIDE the gate (a stray or resumed session)")
            continue
        spec = specs.get(gid, {})
        allowed = [c for c in calls if not c["blocked"]]
        line = f"{gid} ({sid[-8:]}): {len(calls)} calls, {len(calls) - len(allowed)} blocked, {calls[0]['ts'][11:19]}-{calls[-1]['ts'][11:19]} UTC"
        problems = []
        first = spec.get("first_reads") or []
        if first:
            idx = next((i for i, c in enumerate(allowed) if first[0] in c["input"]), None)
            if idx is None:
                problems.append("the guideline skill was never read")
            elif idx != 0:
                problems.append(f"the guideline skill was read as call {idx + 1}, after other accepted calls")
        if spec.get("vault_first"):
            seen_vault = False
            for c in allowed:
                ps = paths_in(c["input"])
                if any(p.startswith(VAULT) for p in ps):
                    seen_vault = True
                if not seen_vault and any(p.startswith(PROJ_SDK) for p in ps):
                    problems.append("project/SDK was touched before any vault access")
                    break
        wr = [i for i, c in enumerate(allowed) if c["tool"] in ("write", "edit") and (spec.get("out") or "\0") in c["input"]]
        if not wr:
            problems.append("no accepted write of the output file")
        extra = ""
        if wr and gid == "W":
            n, miss = lookup_before_claim(spec, allowed, wr[-1])
            if n:
                extra += f"  [lookup-before-claim {n - len(miss)}/{n}" + (f", not searched: {', '.join(miss)}" if miss else "") + "]"
        if spec.get("index"):
            read = sum(1 for e in spec["index"] if any(e["path"] in c["input"] for c in allowed))
            extra += f"  [index read {read}/{len(spec['index'])}]"
        report.append(line + ("  FAIL: " + "; ".join(problems) if problems else "  ok") + extra)
        fails += [f"{gid}: {p}" for p in problems]
    return report, fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--gate-log", default=str(Path.home() / ".local/state/opencode-gate/gate.jsonl"))
    a = ap.parse_args()
    report, fails = audit(Path(a.workdir), Path(a.gate_log))
    print("\n".join(report))
    print(f"AUDIT {'FAILED' if fails else 'PASSED'}: {len(fails)} problem(s)")
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
