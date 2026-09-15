"""
pdf_extract.py  —  Extract page text from a PDF and write to a file.
Usage: python pdf_extract.py <pdf_path> <start_page> <end_page> <out_file>
Pages are 1-indexed (inclusive). Output is UTF-8 text with --- PAGE N --- headers.
"""
import sys
import fitz

def main():
    if len(sys.argv) < 5:
        print("Usage: python pdf_extract.py <pdf_path> <start> <end> <out_file>", file=sys.stderr)
        sys.exit(1)
    pdf_path = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    out_file = sys.argv[4]

    doc = fitz.open(pdf_path)
    lines = []
    for i in range(start - 1, end):
        lines.append(f"\n\n--- PAGE {i+1} ---\n")
        lines.append(doc[i].get_text())
    doc.close()

    with open(out_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"Extracted pages {start}-{end} to {out_file}", file=sys.stderr)

if __name__ == "__main__":
    main()
