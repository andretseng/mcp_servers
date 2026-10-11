# Task: deterministic waveform / timing-diagram geometry extractor (vector PDFs)

## Why
VLMs misread timing diagrams (edge direction, which marker an arrow starts from). Datasheet figures are VECTOR
drawings, so exact geometry is available. Build a small, GENERIC, deterministic tool that turns a vector figure
into structured facts, then answers timing questions from those facts. It will later run on confidential PDFs
locally, so: no network, no external services, stdlib + PyMuPDF (`fitz`) + shapely + numpy ONLY (already installed).

## Input you may use (only these)
- `drv8311_p15_p34.pdf` (2 pages, public TI datasheet). Page index 0 = SPI timing diagram (Figure 7-1).
  Page index 1 = Dead time (Figure 8-21 is the lower figure; Figure 8-20 above it is a schematic with a tiny inset).
- `tests/expected.json`, `tests/check.py` (dev oracle). Do NOT read anything outside this directory.

## Deliverables (all in this directory)
1. `vecgeo.py`  library + CLI: `python3 vecgeo.py <pdf> <page_index> --bbox x0 y0 x1 y1 > facts.json`
   Extract (PDF points, fitz coordinates): stroked paths/segments, dashed vertical markers, arrows (endpoints +
   arrowheads), text words with bbox; group segments into signal LANES (top to bottom) and stitch each lane into a
   polyline; classify per lane the transitions (rising/falling edge at x, data-bus change "X"-crossing, entry to /
   exit from high-impedance "Z" if shaded/flat), and relate each timing label (e.g. tSU_nSCS) to the two vertical
   markers its horizontal arrow spans, and each marker to the lane transitions lying on it.
2. `answer.py` : `python3 answer.py <pdf> <page_index> --bbox ... ` prints JSON answers per the schema below,
   derived ONLY from the extracted geometry and naming conventions you document (e.g. `*_SDI`, `*_SDO`, `*nSCS`,
   `*SCLK` in a parameter name hints which lane it belongs to). Output "UNKNOWN" for a field when the geometry is
   ambiguous. A wrong answer is worse than UNKNOWN.
3. `NOTES.md` : approach, assumptions, limitations, failure modes. Be honest.

## Answer schema (answer.py stdout)
```
{"params": {"<label>": {"start": {"lane": int, "kind": "rising|falling|transition|to_highz|from_highz|UNKNOWN",
                                   "level_pct": number|null},
                         "end":   {...same...}}},
 "sampling_edge": {"lane": int, "kind": "rising|falling|UNKNOWN"},   # SPI page only: edge the setup/hold arrows straddle
 "first_mover_lane": int|"UNKNOWN"}                                    # dead-time page only
```
Lane index = signal lane top to bottom, 0-based, counting only waveform lanes. "level_pct" only when a "NN%"
text label sits on the transition (dead-time figure).

## Rules (hard)
- NO hard-coded coordinates, page numbers, label strings from this PDF, or expected answers. Generic heuristics only.
  If you hard-code anything, say so in NOTES.md (it will be audited and a hidden, different public figure will be tried).
- No subprocess calls (especially never run `claude`, `codex`, `curl`, `pip`). No network. Read only inside this dir.
- <= ~500 lines total. Prefer simple, readable code. No new dependencies.
- Do not modify `tests/`. Do not git commit.

## Acceptance (run `python3 tests/check.py`)
It runs your `answer.py` on both figures and compares with `tests/expected.json`. Target: all fields correct (or UNKNOWN
where noted as acceptable). Report the check output verbatim.

## Stop conditions (important)
- Time-box: stop after the checker passes, OR after ~6 substantive iterations without clear progress.
- If you cannot make it robust, say so plainly in NOTES.md and your final message: what works, what doesn't, and why.
  A frank negative result is a valid, useful outcome. Do not game the checker.
