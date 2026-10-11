# Vector timing extraction

`python3 vecgeo.py input.pdf PAGE --bbox x0 y0 x1 y1` prints geometry facts.
`python3 answer.py input.pdf PAGE --bbox x0 y0 x1 y1` prints inferred answers.
Coordinates are PDF points in PyMuPDF page space (y increases downward). Without
a crop, the entire page is used; one figure per crop is recommended. Neither program
renders images, uses OCR/network/services/subprocesses, or reads expected answers.
Dependencies are the standard library, PyMuPDF, and Shapely.

## Geometry

The library retains drawing primitives, styles, words, and bounding boxes. It
clips straight stroked segments to the crop. Dashed near-vertical strokes become
markers. Filled triangles become arrowheads only when a matching-color solid
shaft reaches their base. Opposed collinear heads form an interval, including
two shafts interrupted by a label. Single heads, including axis arrows, remain
in the facts but cannot define a two-ended timing interval.

After removing markers and arrow shafts, nearby stroke endpoints are snapped.
Long connected traces with horizontal portions and vertical extent become
waveform candidates. Components with coincident vertical bands and overlapping
horizontal spans become one lane. Lanes are ordered by their upper y coordinate.
The `polylines` field preserves separate bus traces; forcing a two-trace bus into
one polyline would discard geometry. Interior X crossings do not join the traces.
Opposite-slope intersections are bus transitions. Other slopes are rising or
falling according to the left-to-right stitched trace and downward PDF y axis.

A `Z` word inside shading identifies a high-impedance region. Adjacent same-color
fill pieces are united to find its boundaries. A `Z` word lying directly on a
flat trace also supplies a region. Shading alone does not imply impedance:
shaded unknown-data intervals are left unclassified. Bus crossings or scalar
edges at the region boundaries become entry/exit events.

Timing words starting with lowercase `t` and an alphabetic character are paired
with a nearby horizontal arrow containing their horizontal center. Arrow tips
must align with unique dashed markers and lie within their vertical spans.
Each marker records every lane edge it geometrically intersects; edges outside
its vertical extent are excluded. For a vertical step, overlapping y intervals
(including a shared endpoint) establish association; the midpoint need not lie
on the marker. Percentage annotations require a short
horizontal tick crossing the edge and a nearby percentage word at that height.
The marker must align with that tick intersection to receive `level_pct`.

## Naming conventions and ambiguity

Interval start/end mean left/right arrow tips, not PDF path drawing order.
A uniquely intersected event needs no naming inference. Serial parameter
suffixes ending in `DO` denote an output data bus, and `DI` an input data bus.
When lane names are absent, exactly one bus with an explicit high-impedance
region is treated as the output, and exactly one remaining bus as the input.
An endpoint on the named bus takes precedence. If it does not intersect that
bus, a unique scalar control edge takes precedence over other bus crossings.
This serial convention is an assumption, not a fact implied by geometry alone;
it can be wrong in a nonserial figure or one with a bidirectional input bus.
No such scalar preference is applied without a resolved DI/DO suffix.

`tSU` and `tHD` are conventional setup/hold prefixes. Matching suffixes and
a shared setup-end/hold-start marker identify the sampling edge, provided that
marker intersects exactly one rising/falling edge and all identified sampling
pairs agree. For figures without a recognized bus, first mover means the lane
whose drawn slope begins first, rather than whose 50% threshold is first.
Equal-time beginnings are ambiguous. These structural tests decide which
summary field to emit; there is no page-number switch.

Unresolved event fields are `"UNKNOWN"`; missing percentages are null. Flat
data-window markers do not manufacture transitions. In particular, this figure
has setup/hold endpoints on flat data envelopes that remain unknown.

## Limits and hard-coding audit

There are no embedded PDF coordinates, page numbers, full parameter labels,
expected answers, filename dispatches, or accesses to tests in the programs.
Generic thresholds are embedded: endpoint tolerance is 0.8 times median stroke
width, some associations allow twice that tolerance, waveform width must exceed
one quarter of crop width, waveform height must exceed four tolerances, and text
associations allow up to two word heights. Those choices are heuristics and can
fail with thin strokes, large unrelated content in a crop, or cramped figures.

This is a small straight-line timing recognizer, not a general PDF interpreter.
Curves, rectangles, and other primitives are retained as raw paths but are not
flattened into waveform segments. Hollow arrowheads, separately drawn dash
pieces, solid reference markers, rotated axes, ambiguous arrowhead pairings,
branching traces, unlabeled high impedance, percentage words without ticks,
unusual parameter typography, multiple same-name timing labels, and complex
schematics are not reliably handled. Branching or overlapping components can
be mistaken for waveforms. The answer layer does not use explicit lane names.
An unrecognized bus can cause the wrong summary-field type to be emitted.
Tolerance is inferred from PDF stroke widths, not mathematically exact joins.
No robustness claim extends to unseen public or confidential PDFs.

Development uses the oracle for inspection/checking. The PNGs were viewed during
development; the extractor uses vectors. Checks cover scale/translation and
synthetic ambiguous markers. Only the supplied checker launches answer processes.

## Round 2 findings and changes

The new vector propagation case's rising step was already classified correctly.
Its dashed marker starts at the step's bottom endpoint, below the step midpoint.
Checking only that midpoint incorrectly discarded the association. Marker
matching now compares vertical intervals for vertical steps. This includes
endpoint contact without enlarging tolerances or assuming a threshold percentage.
Nonvertical interpolation is clamped to the actual edge endpoints, and percentage
ticks must also lie within the marker's vertical extent.

The other new crop contains only an embedded raster image, with no vector paths
or text. Its timing label and percentages cannot be recovered under vector-only
rules. It still returns empty parameters and UNKNOWN first mover. Facts include
image placement metadata and a diagnostic also emitted to stderr by both CLIs.
JSON stdout is preserved. No pixels are inspected, and no answer is borrowed
from nearby prose, expected answers, or the other propagation figure.

No new PDF-specific constants or naming rules were introduced. A dashed marker
collinear with a vertical step and touching its endpoint is assumed to reference
that step; multiple associated lane events remain ambiguous. Remaining limits
above still apply, and raster-only figures require a different input or task scope.
Validation passed 20 synthetic rising/falling checks for endpoint contact, interior
overlap, disjoint markers, and reversed stroke order, plus scale/translation checks
on all three vector development figures. The raster case remains unsolved.
