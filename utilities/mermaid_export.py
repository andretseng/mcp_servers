#!/usr/bin/env python3
"""Export a Mermaid diagram from a markdown note (or .mmd file) to a PNG.

Backends (auto-detected, in order):
  1. mmdc      - mermaid-cli, fully offline, best quality (if on PATH)
  2. mermaid.ink - online service, zero-install (needs network + User-Agent)

The render step that hits mermaid.ink requires outbound network. When the
agent runs this inside a sandboxed shell, run it with the sandbox disabled
(trusted outbound HTTPS to a known service).

Usage:
  python3 mermaid_export.py <note.md> [selector] [options]
  python3 mermaid_export.py <diagram.mmd> [options]

Selectors (pick which diagram in a .md with several mermaid blocks):
  --index N        the N-th ```mermaid block (1-based)
  --match "text"   first block whose nearest heading OR code contains "text"
  --all            export every mermaid block
  (no selector)    if the file has exactly one block, use it; else list them

Options:
  --out PATH       explicit output file (single-diagram only)
  --outdir DIR     output directory (default: Assets)
  --width N        PNG width in px (default 2400 = "big picture")
  --bg COLOR       background: white | transparent | hexlike FFFFFF (default white)
  --theme NAME     default | neutral | dark | forest (default default)
  --backend NAME   auto | mmdc | ink (default auto)
"""
import argparse, base64, os, re, shutil, struct, subprocess, sys, tempfile
import urllib.request, urllib.error

MERMAID_FENCE = re.compile(r"^\s*```+\s*mermaid\b", re.I)
FENCE_END = re.compile(r"^\s*```+\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def extract_blocks(md_text):
    """Return list of {heading, line, code} for every ```mermaid fence."""
    blocks = []
    lines = md_text.splitlines()
    cur_heading = None
    i = 0
    while i < len(lines):
        h = HEADING.match(lines[i])
        if h:
            cur_heading = h.group(2).strip()
        if MERMAID_FENCE.match(lines[i]):
            start = i + 1
            j = start
            while j < len(lines) and not FENCE_END.match(lines[j]):
                j += 1
            code = "\n".join(lines[start:j]).strip("\n")
            blocks.append({"heading": cur_heading, "line": i + 1, "code": code})
            i = j
        i += 1
    return blocks


def slugify(s):
    if not s:
        return "diagram"
    s = re.sub(r"[^\w一-鿿]+", "-", s).strip("-")
    return s[:60] or "diagram"


def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            w, h = struct.unpack(">II", head[16:24])
            return f"{w}x{h}"
    except Exception:
        pass
    return "?"


def norm_bg(bg):
    if not bg or bg.lower() == "transparent":
        return None
    if bg.lower() == "white":
        return "FFFFFF"
    return bg.lstrip("#!")


def render_mmdc(code, out, width, bg, theme):
    with tempfile.NamedTemporaryFile("w", suffix=".mmd", delete=False, encoding="utf-8") as f:
        f.write(code)
        src = f.name
    try:
        cmd = ["mmdc", "-i", src, "-o", out, "-w", str(width), "-t", theme,
               "-b", (bg if bg else "transparent")]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    finally:
        os.remove(src)
    return out


def render_ink(code, out, width, bg, theme):
    b64 = base64.urlsafe_b64encode(code.encode("utf-8")).decode("ascii")
    q = f"?type=png&width={int(width)}&theme={theme}"
    bgh = norm_bg(bg)
    if bgh:
        q += f"&bgColor={bgh}"
    url = "https://mermaid.ink/img/" + b64 + q
    if len(url) > 8000:
        raise RuntimeError(
            "diagram too large for the online URL (~8KB limit). "
            "Install mmdc (mermaid-cli) for an offline render of big diagrams.")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 mermaid-export/1.0"})
    try:
        data = urllib.request.urlopen(req, timeout=45).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"mermaid.ink HTTP {e.code} (often a Mermaid syntax error in the block)")
    if data[:4] != b"\x89PNG":
        raise RuntimeError("mermaid.ink did not return a PNG (syntax error or service issue)")
    with open(out, "wb") as f:
        f.write(data)
    return out


def choose_backend(name):
    if name == "mmdc":
        return "mmdc"
    if name == "ink":
        return "ink"
    return "mmdc" if shutil.which("mmdc") else "ink"


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("input")
    ap.add_argument("--index", type=int)
    ap.add_argument("--match")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--outdir", default="Assets")
    ap.add_argument("--width", type=int, default=2400)
    ap.add_argument("--bg", default="white")
    ap.add_argument("--theme", default="default")
    ap.add_argument("--backend", default="auto", choices=["auto", "mmdc", "ink"])
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"ERROR: file not found: {args.input}")

    with open(args.input, encoding="utf-8") as f:
        text = f.read()

    stem = os.path.splitext(os.path.basename(args.input))[0]
    is_mmd = args.input.lower().endswith((".mmd", ".mermaid"))
    if is_mmd:
        blocks = [{"heading": stem, "line": 1, "code": text.strip()}]
    else:
        blocks = extract_blocks(text)

    if not blocks:
        sys.exit("ERROR: no ```mermaid blocks found in this file.")

    # ---- selection ----
    if args.all:
        chosen = list(enumerate(blocks, 1))
    elif args.index is not None:
        if not (1 <= args.index <= len(blocks)):
            sys.exit(f"ERROR: --index {args.index} out of range (file has {len(blocks)} block(s)).")
        chosen = [(args.index, blocks[args.index - 1])]
    elif args.match:
        m = args.match.lower()
        hit = [(i, b) for i, b in enumerate(blocks, 1)
               if (b["heading"] and m in b["heading"].lower()) or m in b["code"].lower()]
        if not hit:
            sys.exit(f"ERROR: no block matched '{args.match}'.")
        chosen = [hit[0]]
    elif len(blocks) == 1:
        chosen = [(1, blocks[0])]
    else:
        lines = [f"  [{i}] line {b['line']:>4}  heading: {b['heading'] or '(none)'}"
                 for i, b in enumerate(blocks, 1)]
        sys.exit("Multiple mermaid blocks found - pick one with --index N or --match TEXT:\n"
                 + "\n".join(lines))

    backend = choose_backend(args.backend)
    render = render_mmdc if backend == "mmdc" else render_ink

    os.makedirs(args.outdir, exist_ok=True)
    results = []
    for idx, b in chosen:
        if args.out and len(chosen) == 1:
            out = args.out
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        else:
            base = slugify(b["heading"]) if b["heading"] and b["heading"] != stem else stem
            suffix = f"-{idx}" if len(chosen) > 1 else ""
            out = os.path.join(args.outdir, f"{slugify(stem)}-{base}{suffix}.png"
                               if not is_mmd else f"{slugify(stem)}{suffix}.png")
        try:
            render(b["code"], out, args.width, args.bg, args.theme)
            results.append((out, png_size(out), None))
        except Exception as e:
            results.append((out, None, str(e)))

    ok = True
    for out, size, err in results:
        if err:
            ok = False
            print(f"FAILED: {out} - {err}")
        else:
            print(f"EXPORTED [{backend}]: {out} ({size})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
