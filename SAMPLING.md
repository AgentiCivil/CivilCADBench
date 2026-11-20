# How the sample was drawn

The sample is a fixed-seed stratified draw from the benchmark as evaluated in the paper. The draw never reads a model
output or a score. Within each stratum the entries are sorted by identifier and shuffled with Python's
`random.Random("<seed>:<stratum>")`, seed 20261123; the sample takes the first entries of that order.

An entry is skipped only when it fails a de-identification check that masking cannot repair, and the next entry of the same
order takes its place. Every skip is listed below with its reason. Positions are positions in the seeded order;
identifiers are those of this sample (source identifiers carry abbreviations of the projects and are not given).

The de-identification checks applied to each entry:

1. The title block, sign-off cells, stamps and margin strips are blanked, and the masked page is rendered again.
2. OCR is run on the masked page and searched for design-office, owner and project names and for place-name
   patterns (road, bridge, river, village, station, district names; "to X" direction labels).
3. The masked page is inspected by eye.
4. The text of the item, its options and its answers is searched with the same patterns.
5. For DXF files, every text entity, block name, layer name, style name and header variable is searched likewise.

## Single-Sheet: 10 items per binding type

| Binding type | Items in the stratum | Entries examined | Accepted | Replaced |
|---|---|---|---|---|
| table / block | 187 | 13 | SS-01 to SS-10 | 3 |
| instance | 879 | 16 | SS-11 to SS-20 | 6 |
| cross-view | 362 | 12 | SS-21 to SS-30 | 2 |
| scope | 94 | 16 | SS-31 to SS-40 | 6 |
| view | 91 | 16 | SS-41 to SS-50 | 6 |
| datum | 185 | 13 | SS-51 to SS-60 | 3 |

Replaced entries, by position in the seeded order of the stratum:

| Binding type | Position | Reason |
|---|---|---|
| table / block | 6, 8 | no sheet frame found by the masking tool, so the title block could not be isolated and verified |
| table / block | 9 | a real road name is printed in the drawing body |
| instance | 3 | sheet frame detected on the drawing's own grid; the mask could not be verified |
| instance | 4 | site plan whose rotated labels could not be verified by OCR |
| instance | 6 | a real place name is printed in the drawing body |
| instance | 12 | no sheet frame found by the masking tool |
| instance | 14, 15 | title block sits inside the sheet frame; masking left office and project names on the page |
| cross-view | 4 | page contains an aerial photograph of the real site |
| cross-view | 6 | a real bridge name is printed in the drawing body |
| scope | 2 | no sheet frame found by the masking tool |
| scope | 3, 7 | a real road name or place-name direction labels are printed in the drawing body |
| scope | 4, 5, 11 | page is a topographic map of the real site |
| view | 2, 8, 12, 14 | title block sits inside the sheet frame; masking left office and project names on the page |
| view | 9, 15 | the question or the page names a real place or bridge |
| datum | 10, 11, 12 | a real road, bridge or place name, or a city coordinate system, is printed in the drawing body |

Pages that show real geography (site plans, topographic maps, alignment plans with place names) are therefore
under-represented in the sample relative to the benchmark. This affects the scope and view types most.

## Multi-sheet: 2 units, 5 parents each

Units are drawn from the 31 units that hold at least five parents posed in both Few-Sheet and Full-Set. A unit is
accepted only when every page of it passes, because Full-Set shows the whole unit.

| Position | Decision | Reason |
|---|---|---|
| 1 | replaced | traffic-sign drawings; destination names on most pages |
| 2 | replaced | road and interchange names and a city coordinate system in the drawing body |
| 3 | replaced | place names on nearly every page and inside the items |
| 4 | accepted as U01 | 27 pages |
| 5, 6, 7 | replaced | real road names throughout the drawing body |
| 8 | accepted as U02 | 19 pages |

Inside each accepted unit the first five parents of the seeded order are taken, with all their probes (31 probes per
unit). No parent was replaced. The pages keep their sheet-title and sheet-number cells; all other cells of the title
block are blanked.

## Reconstruction and design

| Stratum | Size | Accepted | Replaced (position: reason) |
|---|---|---|---|
| L1 easy | 108 | positions 3 and 5 | 1: the page names the tunnel. 2: the page names a metro station and a city elevation datum. 4: the rebuilt DXF could not be verified as identical to the original |
| L1 medium | 96 | positions 1 and 2 | none |
| L1 hard | 28 | positions 1 and 2 | none |
| L2 sets | 28 | position 3 | 1: site plans of a recognisable public square. 2: general-arrangement pages with real road names |
| L3 tasks | 19 | position 7 | 1 to 6: the context pages given to the agent carry real road, station or tunnel names, or the ground truth could not be verified |

L3 context pages are part of the task input, so a task is accepted only when all of them pass. The accepted task
`L3-01` has 14 context pages.

## What was changed in the accepted files

- Page images: areas outside the drawing area set to white. Drawing content is untouched.
- DXF files: rebuilt from the model-space entities into a fresh file; save-time stamps set to constants. Sampled
  geometry, text values and dimension values are identical to the originals (checked with the scorer's extraction,
  tolerance 1e-6 mm).
- PDF files: metadata and document ID removed; pages are pixel-identical to the originals.
- Items, options, answers, probes and task statements: unchanged, except for identifiers.
