"""
pdf_toc.py  —  Extract or search a PDF's TOC.

Usage:
    python pdf_toc.py <pdf_path>                           # Print full TOC JSON to stdout
    python pdf_toc.py <pdf_path> <out_json_path>           # Write full TOC JSON to file
    python pdf_toc.py <pdf_path> --search <query> <out>   # Find one chapter, write small JSON

Full-TOC output shape:
    {"page_count": N, "toc": [[level, title, page], ...]}

--search output shape (written to <out>):
    {
      "query": "...",
      "matched_title": "...",
      "level": 1,
      "chapter_start": N,   # 1-based page number
      "chapter_end": N,
      "page_count": N,
      "sub_sections": [[level, title, page], ...]
    }
    On no match: {"error": "No chapter matching '...'", "query": "..."}

File-output mode is preferred — avoids capturing output into a shell variable
($x = python ...) which breaks the PowerShell(python *) / Bash(python *) allowlist.
"""
import sys
import json
import fitz


def _search_chapter(toc, query, page_count):
    q = query.lower()
    match_idx = None
    for i, (level, title, page) in enumerate(toc):
        if q in title.lower():
            match_idx = i
            break
    if match_idx is None:
        return {"error": f"No chapter matching '{query}'", "query": query}

    level, title, chapter_start = toc[match_idx]

    chapter_end = page_count
    for level2, title2, page2 in toc[match_idx + 1:]:
        if level2 <= level:
            chapter_end = page2 - 1
            break

    sub_sections = [
        [lv, tt, pg]
        for lv, tt, pg in toc[match_idx + 1:]
        if pg <= chapter_end and lv > level
    ]

    return {
        "query": query,
        "matched_title": title,
        "level": level,
        "chapter_start": chapter_start,
        "chapter_end": chapter_end,
        "page_count": page_count,
        "sub_sections": sub_sections,
    }


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python pdf_toc.py <pdf> [<out_json>]\n"
            "  python pdf_toc.py <pdf> --search <query> <out_json>",
            file=sys.stderr,
        )
        sys.exit(1)

    pdf_path = sys.argv[1]

    if len(sys.argv) >= 4 and sys.argv[2] == "--search":
        query = sys.argv[3]
        out_path = sys.argv[4] if len(sys.argv) >= 5 else None

        doc = fitz.open(pdf_path)
        toc = doc.get_toc()
        page_count = doc.page_count
        doc.close()

        result = _search_chapter(toc, query, page_count)
        payload = json.dumps(result, ensure_ascii=False, indent=2)

        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(payload)
            if "error" in result:
                print(f"Search result (no match): {result['error']}", file=sys.stderr)
            else:
                print(
                    f"Chapter found: '{result['matched_title']}' "
                    f"pages {result['chapter_start']}–{result['chapter_end']} "
                    f"({len(result['sub_sections'])} sub-sections) → {out_path}",
                    file=sys.stderr,
                )
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(payload)
        return

    out_path = sys.argv[2] if len(sys.argv) >= 3 else None

    doc = fitz.open(pdf_path)
    result = {"page_count": doc.page_count, "toc": doc.get_toc()}
    doc.close()

    payload = json.dumps(result, ensure_ascii=False)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload)
        print(
            f"TOC written to {out_path} ({result['page_count']} pages, {len(result['toc'])} entries)",
            file=sys.stderr,
        )
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(payload)


if __name__ == "__main__":
    main()
