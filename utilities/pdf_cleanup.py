"""
pdf_cleanup.py  —  Cross-platform deletion of pdf-analysis temp files.

Replaces shell-specific delete commands (Remove-Item / rm) so the orchestrator
can use the same invocation on Windows (PowerShell) and Ubuntu (Bash).
The command always starts with `python ...`, matching the `python *` allowlist
on both platforms, and accepts multiple paths in a single call — no `;` chaining
needed.

Usage:
    # Exact-path mode — pass any number of files to delete
    python pdf_cleanup.py <file1> [<file2> ...]

    # Orphan-cleanup mode — glob _*.txt and _*.json under a directory
    python pdf_cleanup.py --orphans <raw_dir>

Missing files are silently skipped. Non-zero exit only on argument errors.
"""
import sys
import os
import glob


def _delete(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        print(f"Failed: {path} ({e})", file=sys.stderr)
        return False


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python pdf_cleanup.py <file1> [<file2> ...]", file=sys.stderr)
        print("       python pdf_cleanup.py --orphans <raw_dir>", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--orphans":
        if len(sys.argv) < 3:
            print("Usage: python pdf_cleanup.py --orphans <raw_dir>", file=sys.stderr)
            sys.exit(1)
        raw_dir = sys.argv[2]
        targets = (
            glob.glob(os.path.join(raw_dir, "_*.txt"))
            + glob.glob(os.path.join(raw_dir, "_*.json"))
        )
        deleted = sum(_delete(p) for p in targets)
        if deleted:
            print(f"Cleaned up {deleted} orphaned temp file(s).")
        return

    deleted = sum(_delete(p) for p in sys.argv[1:])
    if deleted:
        print(f"Deleted {deleted} file(s).")


if __name__ == "__main__":
    main()
