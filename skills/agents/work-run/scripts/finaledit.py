"""The final edit: once the local model has handed out its note, Codex (gpt-6-sol) checks it against the sources, corrects it and rewrites it in
Traditional Chinese. One read-only Codex call; the result is checked by the note check afterwards and every change is listed in the note.

Only public and private material may go to Codex. Restricted material is refused here (a local editor would be needed)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

DEFAULT_MODEL = "gpt-6-sol"
DEFAULT_EFFORT = "high"
START, END = "<<<NOTE", "NOTE>>>"
LOG_HEADING = "## 編修紀錄"


class FinalEditError(Exception):
    pass


def build_prompt(question: str, note: str, report: str, roots: list[str], deny: list[str]) -> str:
    return "\n".join([
        "You are the FINAL EDITOR of an analysis note. A small local model wrote it from the user's request; you check it against the sources, correct it and rewrite it in Traditional Chinese (Taiwan wording).",
        f"The user's request, word for word: {question}",
        "You may READ files (the sandbox is read-only) under: " + ", ".join(roots) + ("; never read or cite: " + ", ".join(deny) if deny else "") + ".",
        "Rules:",
        "1. Check every claim of the Provenance table against the cited source line. Fix a wrong claim, a statement the claims do not support, a step whose output does not match the next step's input, and a wrong unit or number. When something cannot be confirmed from the sources, say so under the limits section and mark the claim `unverified` or `inferred` (with its premises); never present a guess as a fact.",
        "2. Do not add a fact you have not confirmed in a source. A new claim needs a Provenance row whose quote is the exact text of the cited line.",
        "3. Keep the format: the frontmatter keys (title, date, tags) in English; sections in the same order (a summary first, the parts of the answer, the limits and open questions, the references, and `## Provenance` last); the claim ids `[S1]`, the tags `[inferred]` and `[unverified]`; the Provenance table with the columns `| id | status | source | quote | claim |` and the statuses `verified`, `inferred`, `unverified` in English; code blocks marked ```c src=<path>:<a>-<b> with exactly those lines. Quotes, source code, identifiers, register names and file paths stay exactly as in the sources.",
        "4. Write the headings and the prose in Traditional Chinese (Taiwan wording); keep technical terms in English. Keep the note as long as its content needs; do not shorten it by dropping supported claims. The reader never sees the local model's version: in the headings and the body (and in the log) do not mention a draft, an original version or the local model (no 原稿, 初稿, 原版, 前版, 本地模型); state each corrected fact as the fact itself, and say what was wrong only in the log, by claim id.",
        f"5. After the Provenance section add `{LOG_HEADING}`: a short list of what you changed and why (one line each), including every claim you corrected, removed or downgraded.",
        "6. Do not run or mention an adviser review or any other workflow from your own instructions: this is a single editing step, and the harness handles everything else. Do not write anywhere; only read.",
        f"7. Reply with the complete note and nothing else, between the lines `{START}` and `{END}`.",
        "",
        "What a script found in the note after the local model stopped (it was not enforced while the model wrote):",
        report.strip() or "(no problems found)",
        "",
        "THE NOTE:",
        START, note.strip(), END,
    ])


def parse_note(text: str) -> str | None:
    m = re.search(rf"{re.escape(START)}\s*\n(.*?)\n\s*{re.escape(END)}", text or "", re.S)
    note = m.group(1).strip() if m else None
    if not note or not note.startswith("---") or not re.search(r"^#{1,6}\s+Provenance\b", note, re.M | re.I):
        return None
    return note + "\n"


LAST_COMMANDS: list = []  # (command, output) of every command Codex ran in the last call
LAST_USAGE: dict = {}  # token usage of the last codex call (turn.completed event)


def run_codex(prompt: str, model: str, effort: str, cwd: str, timeout: int, codex: str = "codex") -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory(prefix="final-edit-") as t:
        out = Path(t) / "last-message.txt"
        cmd = [codex, "exec", "--model", model, "-c", f'model_reasoning_effort="{effort}"', "-c", 'model_provider="openai"', "--json", "--output-last-message", str(out),
               "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "-C", cwd, "-"]
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(input=prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.communicate()
            return 124, "", f"codex exceeded {timeout} s"
        LAST_USAGE.clear()
        LAST_COMMANDS.clear()
        for line in stdout.decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            it = ev.get("item") if isinstance(ev, dict) else None
            if isinstance(it, dict) and it.get("type") == "command_execution" and ev.get("type") == "item.completed":
                LAST_COMMANDS.append((str(it.get("command", "")), str(it.get("aggregated_output", ""))))
            if isinstance(ev, dict) and ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
                LAST_USAGE.update(ev["usage"])
        text = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
        return proc.returncode, text, stderr.decode("utf-8", "replace")[-600:]


def denied_touches(deny: list[str]) -> list[str]:
    """Commands Codex ran whose text or output names an excluded source (the prompt only asks it not to read those)."""
    def names(c: str, d: str) -> bool:  # a deny string used as --exclude/--exclude-dir/grep -v pattern keeps the source out; it is not a read
        return d in re.sub(r"--exclude(?:-dir)?[= ]\s*(?:'[^']*'|\"[^\"]*\"|\S+)|\b(?:rg|grep)\s+-v\s+(?:'[^']*'|\"[^\"]*\"|\S+)|(?:^|\s)(?:-g|--glob|--iglob)[= ]\s*\S*!\S*", "", c)
    return [c[:200] for c, out in LAST_COMMANDS if any(d and (names(c, d) or d in out) for d in deny)]


def final_edit(question: str, note: str, report: str, roots: list[str], deny: list[str], classification: str, model: str = DEFAULT_MODEL,
               effort: str = DEFAULT_EFFORT, timeout: int = 1200, codex: str = "codex", cwd: str = str(Path.home())) -> str:
    """The edited note, or raise FinalEditError (the caller keeps the local model's note)."""
    if classification == "restricted":
        raise FinalEditError("restricted material may not be sent to Codex")
    code, text, err = run_codex(build_prompt(question, note, report, roots, deny), model, effort, cwd, timeout, codex)
    if code != 0:
        raise FinalEditError(f"codex exited {code}: {err.strip()[:300]}")
    edited = parse_note(text)
    if edited is None:
        raise FinalEditError("the reply did not hold a complete note between the markers")
    return edited
