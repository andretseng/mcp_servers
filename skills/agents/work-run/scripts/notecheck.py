"""Checks of a finished analysis note: the Provenance table (claims with checked quotes), the citations in the body,
code blocks copied from sources, the frontmatter and the required sections.

Hard errors reject a write; warnings are kept for the audit and the reviewer."""

from __future__ import annotations

from pathlib import Path
import re

CLAIM_ID = re.compile(r"^[A-Z]\d+$")
CITE = re.compile(r"\[([A-Z]\d+(?:\s*,\s*[A-Z]\d+)*)\]")
REF = re.compile(r"^(/.+?):(\d+)(?:-(\d+))?$")  # vault notes have spaces and parentheses in their names
REF_LIST = re.compile(r"^(/.+?):(\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)+)$")  # several places in one file: 51,55 or 194,226-227
CODE_BLOCK = re.compile(r"```[^\n`]*?\bsrc=(/[^:\n]+?):(\d+)-(\d+)[^\n]*\n(.*?)\n```", re.S)
STATUSES = ("verified", "inferred", "unverified")
WARN_COVERAGE = 0.8  # a share of cited sentences is advice, not a gate: it says nothing about whether a statement is right
REQUIRED = {"summary": ("summary", "conclusion", "摘要", "結論"), "limits": ("limit", "unverified", "open question", "限制", "待驗證", "待確認", "未確認"), "references": ("reference", "source", "參考", "來源")}
SRC_TAG = re.compile(r"(```[^\n`]*?\bsrc=)([^:\s]+?):(\d+-\d+)")
ROW_HEAD = re.compile(r"^(\|\s*[A-Z]\d+\s*\|\s*)([A-Za-z]+)(\s*\|\s*)(`?)([^|`]+?)(`?)(\s*\|)", re.M)
ROOT_ALIASES = ("sdk", "project", "vault")  # names an editor puts in front of a path for a root
PREMISES = re.compile(r"^(?:前提|premises?)\s*[:：]\s*(.+)$", re.I)
REVIEW_ID = re.compile(r"(?<![\w-])R\d+-\d+(?![\w-])")  # an id the review loop gave to a reviewer's point (work-run settle_items)
# characters that exist only in Simplified Chinese; the notes are written in Traditional Chinese
SIMPLIFIED = "弃开读们这对时为与东书会长门问间发过关还进远连运选钱错键阅项须头领题类显验证将样处现础线结构统数块写应该变换传级总输认视说调场产带额网态约续观电压设计备记笔测试图码阵联释义让从个来经动机无体点实层简单边缘触节频识标择优势"
_cache: dict[str, list[str]] = {}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\r", "")).strip()


def _lines(path: str) -> list[str]:
    if path not in _cache:
        _cache[path] = Path(path).read_text(encoding="utf-8", errors="replace").split("\n")
    return _cache[path]


def _under(path: str, roots: list[str]) -> bool:
    q = str(Path(path).resolve())
    return any(q == r or q.startswith(r.rstrip("/") + "/") for r in (str(Path(x).resolve()) for x in roots))


def _cells(row: str, strip: bool = True) -> list[str]:
    parts = re.split(r"(?<!\\)\|", row.strip())
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [(p.strip() if strip else p).replace("\\|", "|") for p in parts]


def _unquote(cell: str) -> str:
    c = cell.strip()
    return c[1:-1] if len(c) >= 2 and c[0] == c[-1] == "`" else c


def split_note(text: str) -> tuple[str, str, str]:
    """frontmatter, body (before the Provenance heading), provenance section."""
    fm = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm, text = text[: end + 4], text[end + 4 :]
    m = re.search(r"^#{1,6}\s+Provenance\b.*$", text, re.M | re.I)
    if not m:
        return fm, text, ""
    nxt = re.search(r"^#{1,6}\s+\S", text[m.end() :], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return fm, text[: m.start()] + text[end:], text[m.start() : end]


def parse_provenance(section: str) -> tuple[list[dict], list[str]]:
    claims, errs = [], []
    for n, line in enumerate(section.split("\n"), 1):
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if not cells or re.fullmatch(r":?-{2,}:?", cells[0]) or cells[0].lower() == "id":
            continue
        if len(cells) < 5:
            errs.append(f"Provenance row {cells[0] if cells else '?'}: {len(cells)} cells, need 5: | id | status | source | quote | claim |")
            continue
        if len(cells) > 5:  # a | inside the quote that was not escaped: the quote is everything between the source and the claim
            cells = cells[:3] + ["|".join(_cells(line, strip=False)[3:-1]).strip()] + cells[-1:]
        cid, status, source, quote, claim = cells
        claims.append({"id": cid, "status": status.lower(), "source": _unquote(source), "quote": _unquote(quote), "claim": claim})
    return claims, errs


def quote_in_window(quote: str, window: str) -> bool:
    """The quote is verbatim text of the cited lines. Formatting that does not change the text is tolerated: backticks around it or around each
    line (`line one` `line two`), and ... or … between pieces. Every piece must still occur in the window and be at least 4 characters."""
    quote = quote.replace("\\`", "`")  # a backtick inside a quoted line may be written escaped
    w, q = _norm(window), _norm(quote)
    if q in w:
        return True
    wb = w.replace("`", "")
    segments = [x for x in re.split(r"\s*(?:\.\.\.|…)\s*", quote) if x.strip()]  # pieces separated by an ellipsis may be anywhere in the window
    if not segments:
        return False
    for seg in segments:
        lines = [_norm(x.replace("`", "")) for x in re.split(r"`\s*`|\s*<br\s*/?>\s*", seg, flags=re.I) if x.strip()]
        lines = [x for x in lines if x]
        joined = " ".join(lines)  # consecutive lines, one of which may be as short as a lone `{`
        if not lines or not (all(len(x) >= 4 and x in wb for x in lines) or (len(joined) >= 4 and joined in wb)):
            return False
    return True


def check_claims(claims: list[dict], roots: list[str], deny: list[str], final: bool = True) -> list[str]:
    errs, ids = [], set()
    by_id = {c["id"]: c for c in claims}
    for c in claims:
        i = c["id"]
        if not CLAIM_ID.match(i):
            errs.append(f"{i}: id must look like S12 (one capital letter and a number)")
        if i in ids:
            errs.append(f"{i}: duplicate id")
        ids.add(i)
        if c["status"] not in STATUSES:
            errs.append(f"{i}: status must be verified, inferred or unverified, got `{c['status']}`")
            continue
        if c["status"] == "unverified":
            continue
        if c["status"] == "inferred":
            m = re.match(r"^inference\s*:\s*(.+)$", c["source"], re.I)
            if not m and not (REF.match(c["source"]) or REF_LIST.match(c["source"])):
                errs.append(f"{i}: an inferred claim needs source `inference: S1, S2` (the claims it follows from)")
                continue
            if m:
                for p in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                    if final and (p not in by_id or p == i):
                        errs.append(f"{i}: premise {p} is not a claim of this table")
                continue
            # an inferred statement written on a quoted source line: the quote is checked like a verified one
        ml = REF_LIST.match(c["source"])
        m = REF.match(c["source"])
        if not (m or ml):
            errs.append(f"{i}: a verified claim needs source `<absolute path>:<line>` (or `<a>-<b>`, or a list `<a>,<b>-<c>`), got `{c['source']}`")
            continue
        spans = [(int(x.split("-")[0]), int(x.split("-")[-1])) for x in re.split(r"\s*,\s*", ml.group(2))] if ml else [(int(m.group(2)), int(m.group(3) or m.group(2)))]
        path = (ml or m).group(1)
        a, b = min(x for x, _ in spans), max(y for _, y in spans)
        if any(d in path for d in deny):
            errs.append(f"{i}: {path} is an excluded source")
        elif not Path(path).is_file():
            errs.append(f"{i}: file does not exist: {path}")
        elif roots and not _under(path, roots):
            errs.append(f"{i}: {path} is outside the allowed roots")
        elif len(c["quote"]) < 4:
            errs.append(f"{i}: a verified claim needs a verbatim quote (at least 4 characters) from the cited line")
        else:
            ls = _lines(path)
            if a < 1 or b > len(ls):
                errs.append(f"{i}: line {a}-{b} is outside {Path(path).name} ({len(ls)} lines)")
            elif not quote_in_window(c["quote"], " ".join(" ".join(ls[max(0, x - 3) : y + 2]) for x, y in spans)):
                errs.append(f"{i}: the quote is not in {Path(path).name} within lines {max(1, a - 2)}-{b + 2}. Copy the text exactly from the file, or fix the line number, or mark the claim unverified")
    return errs


def _sentences(body: str) -> list[str]:
    body = CODE_BLOCK.sub(" ", re.sub(r"```.*?```", " ", body, flags=re.S))
    out = []
    for line in body.split("\n"):
        s = line.strip()
        if not s or s.startswith(("#", "|", ">", "<!--", "---")):
            continue
        s = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", s)
        out += [x.strip() for x in re.split(r"(?<=[.!?。！？；])(?!\s*\[[A-Za-z])\s*", s) if len(x.split()) >= 6 or len(re.findall(r"[㐀-鿿]", x)) >= 10]  # Chinese has no spaces: ten characters count as a sentence
    return out


def check_body(body: str, claims: list[dict]) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    by_id = {c["id"]: c for c in claims}
    sents = _sentences(body)
    cited = 0
    for s in sents:
        ids = [x.strip() for grp in CITE.findall(s) for x in grp.split(",")]
        for i in ids:
            if i not in by_id:
                errs.append(f"cites {i}, which is not in the Provenance table: {s[:80]}")
        if ids:
            cited += 1
            if any(by_id.get(i, {}).get("status") == "inferred" for i in ids) and "[inferred" not in s.lower():
                warns.append(f"cites an inferred claim without [inferred]: {s[:80]}")
            if any(by_id.get(i, {}).get("status") == "unverified" for i in ids) and "[unverified" not in s.lower():
                warns.append(f"cites an unverified claim without [unverified]: {s[:80]}")
    if sents:
        cov = cited / len(sents)
        if cov < WARN_COVERAGE:
            warns.append(f"{int(cov * 100)}% of the body sentences cite a claim (target {int(WARN_COVERAGE * 100)}%)")
    return errs, warns


def check_code_blocks(text: str, roots: list[str], deny: list[str]) -> list[str]:
    errs = []
    for m in CODE_BLOCK.finditer(text):
        path, a, b, block = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if any(d in path for d in deny) or not Path(path).is_file() or (roots and not _under(path, roots)):
            errs.append(f"code block src={path}:{a}-{b}: the source is missing, excluded or outside the allowed roots")
            continue
        ls = _lines(path)
        if a < 1 or b > len(ls) or b < a:
            errs.append(f"code block src={path}:{a}-{b}: lines are outside the file ({len(ls)} lines)")
        elif _norm(block) != _norm(" ".join(ls[a - 1 : b])):
            errs.append(f"code block src={path}:{a}-{b}: the block is not the text of those lines; copy them exactly or drop the src= tag")
    return errs


def check_structure(text: str, fm: str) -> list[str]:
    errs = []
    if not fm:
        errs.append("the note must start with frontmatter (--- title, date, tags ---)")
    else:
        for k in ("title", "date", "tags"):
            if not re.search(rf"^{k}\s*:", fm, re.M):
                errs.append(f"frontmatter lacks `{k}:`")
    heads = [h.lower() for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)]
    for name, words in REQUIRED.items():
        if not any(w in h for h in heads for w in words):
            errs.append(f"a section for '{name}' is missing (a heading containing one of: {', '.join(words)})")
    return errs


def _resolve(path: str, roots: list[str]) -> str | None:
    """A relative path as an absolute one, when exactly one root holds that file."""
    if path.startswith("/"):
        return None
    first, _, rest = path.partition("/")
    for rel in (path, rest if first.lower() in ROOT_ALIASES else ""):  # the second try drops a root alias an editor wrote in front (`SDK/source/...`); any other prefix is not guessed
        hits = {str((Path(r).expanduser() / rel).resolve()) for r in roots if rel and (Path(r).expanduser() / rel).is_file()}
        if len(hits) == 1:
            return hits.pop()
    return None


def normalize_paths(text: str, roots: list[str]) -> str:
    """Repair what an editor rewrites without changing a fact: a relative source path in the Provenance table or a `src=` tag becomes the absolute
    path (when exactly one root holds the file), and an inferred row's `前提：S1、S2` becomes `inference: S1, S2`. Anything else is left for the check."""
    def row(m: re.Match) -> str:
        head, status, mid, tick1, src, tick2, tail = m.groups()
        head = head + status + mid
        val = src.strip()
        pm = PREMISES.match(val) if status.lower() == "inferred" else None  # a premise list belongs to an inferred row only
        if pm:
            return head + "inference: " + ", ".join(x.strip() for x in re.split(r"[、,，]", pm.group(1)) if x.strip()) + tail
        hit = re.match(r"^(.+?):(\d[\d,\s-]*)$", val)
        abs_ = _resolve(hit.group(1), roots) if hit else None
        return head + tick1 + (f"{abs_}:{hit.group(2)}" if abs_ else src) + tick2 + tail
    def tag(m: re.Match) -> str:
        abs_ = _resolve(m.group(2), roots)
        return f"{m.group(1)}{abs_ or m.group(2)}:{m.group(3)}"
    text = SRC_TAG.sub(tag, text)
    prov = split_note(text)[2]
    return text.replace(prov, ROW_HEAD.sub(row, prov), 1) if prov else text


def _prose(text: str) -> tuple[str, int]:
    """The text outside code fences and inline code, and how many fence lines there are."""
    out, in_code, fences = [], False, 0
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code, fences = not in_code, fences + 1
        elif not in_code:
            out.append(re.sub(r"`[^`]*`", " ", line))
    return "\n".join(out), fences


def form_warnings(text: str, claims: list[dict]) -> list[str]:
    """Cheap form problems a script can see without judging the content: a verified claim that rests on a note, an unpaired code fence,
    a review-loop point id left in the text, Simplified Chinese characters."""
    warns = []
    for c in claims:
        m = REF.match(c["source"]) or REF_LIST.match(c["source"])
        if c["status"] == "verified" and m and m.group(1).lower().endswith(".md"):
            warns.append(f"{c['id']}: marked verified, but the source is a note ({Path(m.group(1)).name}); a note restates a claim, it is not evidence. Use inferred (or unverified) unless the note holds a measurement")
    prose, fences = _prose(text)
    if fences % 2:
        warns.append(f"a code fence (```) is opened or closed without its partner ({fences} fence lines); the layout below it is broken")
    ids = sorted(set(REVIEW_ID.findall(prose)))
    if ids:
        warns.append("review point ids left in the text (" + ", ".join(ids[:5]) + "): they mean nothing to a reader")
    simp = sorted({ch for ch in prose if ch in SIMPLIFIED})
    if simp:
        warns.append("Simplified Chinese characters in a Traditional Chinese note: " + "".join(simp[:12]))
    return warns


def check_note(text: str, roots: list[str] | None = None, deny: list[str] | None = None) -> tuple[list[str], list[str], list[dict]]:
    roots, deny = roots or [], deny or []
    fm, body, prov = split_note(text)
    errs, structure = [], check_structure(text, fm)  # frontmatter and section names are form, not logic: warnings
    if not prov:
        return errs + ["the note needs a '## Provenance' section: a table | id | status | source | quote | claim |"], structure, []
    claims, perr = parse_provenance(prov)
    errs += perr
    if not claims:
        errs.append("the Provenance table has no claim rows")
    errs += check_claims(claims, roots, deny)
    be, warns = check_body(body, claims)
    errs += be + check_code_blocks(text, roots, deny)
    return errs, structure + warns + form_warnings(text, claims), claims


def findings_text(question: str, claims: list[dict]) -> str:
    out = [f"## TASK (verbatim)\n{question}\n", "## FINDINGS"]
    out += [f"- {c['id']} {c['claim']} | {c['source']} | {c['status']}" for c in claims]
    return "\n".join(out) + "\n"
