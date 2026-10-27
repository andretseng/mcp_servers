#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Pack bounded executor-owned evidence for a tool-free adviser review.

The adviser must not be responsible for discovering or streaming arbitrary
workspace artifacts.  This adapter accepts an ``evidence-read`` request,
reads only explicit local line ranges in short-lived worker processes, and
emits an equivalent ``summary-only`` request containing compact excerpts.
If a read is ambiguous, too large, non-text, or does not finish before its
deadline, the adapter exits with protocol code 4 and does not invoke an
adviser.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.parse import unquote, urlparse

import adviser_bridge as bridge


DEFAULT_MAX_LINES = 120
DEFAULT_MAX_BYTES = 6000
DEFAULT_MAX_TOTAL_CHARS = 72000
DEFAULT_FILE_TIMEOUT_SECONDS = 3.0
DEFAULT_TOTAL_TIMEOUT_SECONDS = 20.0


class EvidencePackError(RuntimeError):
    """Evidence cannot be packed within the bounded contract."""


def _line_range(locator: str, *, max_lines: int) -> tuple[int, int]:
    """Return an explicit one-based line range from a locator.

    Deliberately accept only common line-address forms.  Section names,
    function names, and vague phrases are rejected because resolving them
    would require an unbounded search by either the executor or adviser.
    """

    patterns = (
        re.compile(
            r"\blines?\s+(?P<start>\d+)"
            r"(?:\s*[-–—]\s*(?:lines?\s*)?(?P<end>\d+))?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bL(?P<start>\d+)"
            r"(?:\s*[-–—]\s*L?(?P<end>\d+))?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r":(?P<start>\d+)"
            r"(?:\s*[-–—]\s*(?P<end>\d+))?\b"
        ),
    )
    match = None
    for candidate in patterns:
        match = candidate.search(locator)
        if match is not None:
            break
    if match is None:
        raise EvidencePackError(
            f"locator {locator!r} needs an explicit line or line range; "
            "supply a compact excerpt for section/name/timestamp locators"
        )
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if start < 1 or end < start:
        raise EvidencePackError(f"locator {locator!r} has an invalid line range")
    if end - start + 1 > max_lines:
        raise EvidencePackError(
            f"locator {locator!r} requests {end - start + 1} lines; "
            f"the bounded pack limit is {max_lines}"
        )
    return start, end


def _local_path(uri: str, *, base_dir: Path) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"}:
        return None
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise EvidencePackError(
                f"file URI {uri!r} has a non-local host; supply an excerpt"
            )
        path = Path(unquote(parsed.path))
    elif parsed.scheme:
        raise EvidencePackError(
            f"evidence URI scheme {parsed.scheme!r} is not locally readable; "
            "supply an excerpt"
        )
    else:
        path = Path(uri)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def _worker_read_range(
    path: Path, *, start_line: int, end_line: int, max_bytes: int
) -> str:
    """Read one bounded UTF-8 line range; called in a killable child."""

    chunks: list[bytes] = []
    total_bytes = 0
    with path.open("rb") as stream:
        for line_number in range(1, end_line + 1):
            raw_line = stream.readline(max_bytes + 1)
            if not raw_line:
                break
            if len(raw_line) > max_bytes:
                raise EvidencePackError(
                    f"line {line_number} in {path} exceeds {max_bytes} bytes"
                )
            if line_number >= start_line:
                chunks.append(raw_line)
                total_bytes += len(raw_line)
                if total_bytes > max_bytes:
                    raise EvidencePackError(
                        f"lines {start_line}-{end_line} in {path} exceed "
                        f"{max_bytes} bytes"
                    )
    if not chunks:
        raise EvidencePackError(
            f"{path} does not contain requested lines {start_line}-{end_line}"
        )
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidencePackError(
            f"{path} is not UTF-8 text; supply a decoded compact excerpt"
        ) from exc


def _worker_main(args: argparse.Namespace) -> int:
    try:
        excerpt = _worker_read_range(
            Path(args.worker_path),
            start_line=args.worker_start,
            end_line=args.worker_end,
            max_bytes=args.worker_max_bytes,
        )
    except (EvidencePackError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    sys.stdout.write(excerpt)
    return 0


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _read_range_with_timeout(
    path: Path,
    *,
    start_line: int,
    end_line: int,
    max_bytes: int,
    timeout_seconds: float,
) -> str:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_worker-path",
        str(path),
        "--_worker-start",
        str(start_line),
        "--_worker-end",
        str(end_line),
        "--_worker-max-bytes",
        str(max_bytes),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _kill_process(process)
        process.communicate()
        raise EvidencePackError(
            f"bounded read of {path} lines {start_line}-{end_line} timed out "
            f"after {timeout_seconds:g} seconds"
        ) from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:1000]
        raise EvidencePackError(
            f"bounded read of {path} lines {start_line}-{end_line} failed"
            + (f": {detail}" if detail else "")
        )
    try:
        excerpt = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidencePackError(
            f"bounded read of {path} returned non-UTF-8 output"
        ) from exc
    if len(stdout) > max_bytes:
        raise EvidencePackError(
            f"bounded read of {path} returned more than {max_bytes} bytes"
        )
    return excerpt


def _validate_excerpt_budget(
    evidence: list[dict[str, Any]], *, max_total_chars: int
) -> None:
    total_chars = sum(len(item.get("excerpt", "")) for item in evidence)
    if total_chars > max_total_chars:
        raise EvidencePackError(
            f"evidence excerpts total {total_chars} characters; "
            f"the bounded pack limit is {max_total_chars}"
        )



FINDING_REF = re.compile(
    r"(?P<file>[A-Za-z0-9_][A-Za-z0-9_./+-]*\.(?:c|h|syscfg|js|md|json))"
    r"(?:\s*:\s*L?|\s*,?\s+L|\s+lines?\s+)"
    r"(?P<nums>\d+(?:\s*[-–—]\s*L?\d+)?(?:\s*,\s*L?\d+(?:\s*[-–—]\s*L?\d+)?)*)"
)
NUM_SPAN = re.compile(r"L?(\d+)(?:\s*[-–—]\s*L?(\d+))?")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".claude", ".obsidian"}


def _resolve_basename(name: str, roots: list[Path]) -> Path:
    """Find the one file called `name` (or ending in it). Roots are tried in order: the first root that has
    the file decides; fail loud if that root has more than one match, or no root has it."""
    wanted = name.replace("\\", "/")
    base = wanted.rsplit("/", 1)[-1]
    for root in roots:
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if base in filenames:
                candidate = Path(dirpath) / base
                if str(candidate).replace("\\", "/").endswith(wanted):
                    found.append(candidate.resolve())
        unique = sorted(set(found))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            raise EvidencePackError(
                f"findings cite {name!r} but {len(unique)} files match under {root} ({', '.join(str(u) for u in unique[:4])}); "
                "write a longer path in the finding, e.g. mcu/mspm0g3507/config.c:372"
            )
    raise EvidencePackError(f"findings cite {name!r} but no such file exists under the --root directories")


def findings_evidence(findings_path: str, roots: list[Path], *, max_lines: int, max_items: int = 24, context: int = 0, merge_gap: int = 5) -> list[dict[str, str]]:
    """Turn `file:line[-line]` references in a findings file into evidence items (merged, bounded)."""
    text = Path(findings_path).read_text(encoding="utf-8")
    ranges: dict[Path, list[tuple[int, int]]] = {}
    cache: dict[str, Path] = {}
    for match in FINDING_REF.finditer(text):
        name = match.group("file")
        for span in NUM_SPAN.finditer(match.group("nums")):
            start = int(span.group(1))
            end = int(span.group(2) or start)
            if end < start:
                start, end = end, start
            if name not in cache:
                cache[name] = _resolve_basename(name, roots)
            ranges.setdefault(cache[name], []).append((max(1, start - context), end + context))
    if not ranges and len(text.strip()) > 200:
        raise EvidencePackError(
            "the findings file has no file:line references the packer can read. Write each fact as "
            "`claim | src/main.c:264 | verified` (also accepted: `main.c L264`, `main.c L69,74`), "
            "otherwise the adviser receives only the note and answers need-evidence"
        )
    items: list[dict[str, str]] = []
    for path, spans in sorted(ranges.items()):
        spans.sort()
        merged: list[list[int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + merge_gap:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            while end - start + 1 > max_lines:
                items.append({"uri": str(path), "locator": f"L{start}-{start + max_lines - 1}"})
                start += max_lines
            items.append({"uri": str(path), "locator": f"L{start}-{end}"})
    if len(items) > max_items:
        raise EvidencePackError(
            f"findings reference {len(items)} separate line ranges; the request allows {max_items}. "
            "Group the facts of one file into nearby lines or drop the minor ones"
        )
    return items



def artifact_evidence(path_value: str, *, max_lines: int, max_bytes: int = 5500) -> list[dict[str, str]]:
    """Split the artifact under review into evidence chunks that respect the per-item limits."""
    path = Path(path_value).expanduser().resolve()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    items: list[dict[str, str]] = []
    start, size = 1, 0
    for number, line in enumerate(lines, 1):
        line_bytes = len(line.encode("utf-8"))
        if number > start and (size + line_bytes > max_bytes or number - start >= max_lines):
            items.append({"uri": str(path), "locator": f"L{start}-{number - 1}"})
            start, size = number, 0
        size += line_bytes
    if lines:
        items.append({"uri": str(path), "locator": f"L{start}-{len(lines)}"})
    return items


def pack_request(
    payload: Any,
    *,
    base_dir: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    file_timeout_seconds: float = DEFAULT_FILE_TIMEOUT_SECONDS,
    total_timeout_seconds: float = DEFAULT_TOTAL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    request = bridge.validate_request(payload)
    packed = deepcopy(request)
    evidence = packed["evidence"]

    if request["review_scope"] == "summary-only":
        _validate_excerpt_budget(evidence, max_total_chars=max_total_chars)
        return packed

    if not evidence:
        raise EvidencePackError("evidence-read request has no evidence to pack")
    if max_lines < 1 or max_bytes < 1 or max_total_chars < 1:
        raise EvidencePackError("evidence pack limits must be positive")
    if file_timeout_seconds <= 0 or total_timeout_seconds <= 0:
        raise EvidencePackError("evidence pack timeouts must be positive")

    deadline = time.monotonic() + total_timeout_seconds
    for index, item in enumerate(evidence):
        existing_excerpt = item.get("excerpt")
        if isinstance(existing_excerpt, str) and existing_excerpt:
            continue
        uri = item["uri"]
        path = _local_path(uri, base_dir=base_dir)
        if path is None:
            raise EvidencePackError(
                f"evidence[{index}] {uri!r} has no excerpt; the packer does not "
                "fetch URLs, so supply an executor-fetched excerpt"
            )
        start_line, end_line = _line_range(item["locator"], max_lines=max_lines)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EvidencePackError(
                f"evidence pack deadline of {total_timeout_seconds:g} seconds exceeded"
            )
        excerpt = _read_range_with_timeout(
            path,
            start_line=start_line,
            end_line=end_line,
            max_bytes=max_bytes,
            timeout_seconds=min(file_timeout_seconds, remaining),
        )
        if not excerpt:
            raise EvidencePackError(f"bounded read of {path} returned an empty excerpt")
        if len(excerpt) > 6000:
            raise EvidencePackError(
                f"bounded read of {path} exceeds the protocol excerpt limit"
            )
        item["excerpt"] = excerpt
        if sum(len(entry.get("excerpt", "")) for entry in evidence) > max_total_chars:
            raise EvidencePackError(
                f"packed evidence exceeds the {max_total_chars}-character total limit"
            )

    packed["review_scope"] = "summary-only"
    _validate_excerpt_budget(packed["evidence"], max_total_chars=max_total_chars)
    return bridge.validate_request(packed)


def _read_json(path_value: str) -> Any:
    if path_value == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path_value).read_text(encoding="utf-8"))


def _write_json(path_value: str, payload: Any, *, pretty: bool) -> None:
    indent = 2 if pretty else None
    text = json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=pretty)
    if path_value == "-":
        sys.stdout.write(text)
        sys.stdout.write("\n")
    else:
        Path(path_value).write_text(text + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack bounded local evidence into a tool-free adviser request."
    )
    parser.add_argument("--request", default="-", help="JSON request path; default: stdin")
    parser.add_argument("--output", default="-", help="packed request path; default: stdout")
    parser.add_argument(
        "--base-dir",
        default=str(Path.cwd()),
        help="base directory for relative evidence paths",
    )
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-total-chars", type=int, default=DEFAULT_MAX_TOTAL_CHARS)
    parser.add_argument("--file-timeout", type=float, default=DEFAULT_FILE_TIMEOUT_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TOTAL_TIMEOUT_SECONDS)
    parser.add_argument("--from-findings", help="findings file; every file:line reference becomes an evidence item")
    parser.add_argument("--context", type=int, default=12, help="lines of context added around each --from-findings reference")
    parser.add_argument("--artifact", action="append", default=[], help="file under review; added to the evidence in bounded chunks (repeatable)")
    parser.add_argument("--root", action="append", default=[], help="directory searched for files cited in --from-findings (repeatable)")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--_worker-path", dest="worker_path", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--_worker-start", dest="worker_start", type=int, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--_worker-end", dest="worker_end", type=int, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--_worker-max-bytes",
        dest="worker_max_bytes",
        type=int,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.worker_path is not None:
        if (
            args.worker_start is None
            or args.worker_end is None
            or args.worker_max_bytes is None
        ):
            print("invalid worker invocation", file=sys.stderr)
            return 2
        return _worker_main(args)
    try:
        request = _read_json(args.request)
        for art_path in args.artifact:
            have = {(e.get("uri"), e.get("locator")) for e in request.get("evidence", [])}
            request["evidence"] = list(request.get("evidence", [])) + [e for e in artifact_evidence(art_path, max_lines=args.max_lines) if (e["uri"], e["locator"]) not in have]
            request["review_scope"] = "evidence-read"
        if args.from_findings:
            if not args.root:
                raise EvidencePackError("--from-findings needs at least one --root directory")
            extra = findings_evidence(args.from_findings, [Path(r).resolve() for r in args.root], max_lines=args.max_lines, context=args.context)
            have = {(e.get("uri"), e.get("locator")) for e in request.get("evidence", [])}
            request["evidence"] = list(request.get("evidence", [])) + [e for e in extra if (e["uri"], e["locator"]) not in have]
            request["review_scope"] = "evidence-read"
        packed = pack_request(
            request,
            base_dir=Path(args.base_dir).resolve(),
            max_lines=args.max_lines,
            max_bytes=args.max_bytes,
            max_total_chars=args.max_total_chars,
            file_timeout_seconds=args.file_timeout,
            total_timeout_seconds=args.timeout,
        )
        _write_json(args.output, packed, pretty=args.pretty)
        return 0
    except bridge.ProtocolError as exc:
        print(f"invalid request: {exc}", file=sys.stderr)
        return 2
    except (EvidencePackError, OSError, json.JSONDecodeError) as exc:
        print(f"evidence unavailable: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
