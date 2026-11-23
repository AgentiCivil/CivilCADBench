# Scoring

Every score is computed by a program in `code/`. No model acts as a judge. `ccb` imports these programs and does not
change them; the commands below call them directly.

## Reconstruction (L1): one page

```bash
python code/reconstruction/score.py <gt_dir> <output.dxf> <out_dir>
```

`<gt_dir>` holds `gt.dxf` and `meta.json`. The scorer writes `<out_dir>/score.json` and three pictures: the rendered
output, the output coloured by layer, and an overlay on the ground truth. The page score (0 to 100) is the weighted
mean of five components:

| Component | Weight | Measures |
|---|---:|---|
| geometry F1 | 0.25 | points of the output within 1 mm of points of the ground truth, whatever the layer |
| layer-aware F1 | 0.30 | the same, and the point must lie on the right one of the nine layers |
| layer share | 0.10 | share of the output that lies on the nine layers at all |
| text F1 | 0.20 | text strings matched by position and content |
| dimension F1 | 0.15 | dimension entities matched by position and displayed value |

A component whose ground truth is empty, for example dimension F1 on a page without dimension entities, is left out
and the other weights are renormalised. A file that cannot be read, or that is missing, scores 0.

```bash
python code/reconstruction/score.py data/reconstruction/L1/L1-easy-01/gt data/reconstruction/L1/L1-easy-01/gt/gt.dxf out/self
# out/self/score.json: "valid": true, "overall": 100.0
```

## Sets (L2) and design (L3)

```bash
python code/reconstruction/set_metrics.py l2 <task_dir> <output_dir>     # output_dir holds page_01.dxf, page_02.dxf, ...
python code/reconstruction/set_metrics.py l3 <task_dir> <output.dxf>
```

- **L2** reports the mean page score over the pages of the set, a missing page counting 0, and the shared-value recall:
  of the values that the ground truth prints on two or more pages of the set, the share that the output prints on each
  of these pages.
- **L3** reports the design recall (`diff_recall`): of the values that are new on the target page relative to the
  reference page, the share that the output writes. Handing in the reference page unchanged, the copy baseline, gives 0.

```bash
python code/reconstruction/set_metrics.py l3 data/reconstruction/L3/L3-01 data/reconstruction/L3/L3-01/ref/sibling.dxf
# {"diff_recall": 0.0, "n_new": 3, "content_f1": 0.8636, "copy_content_f1": 0.8636, "stale_rate": 1.0}
```

## What `ccb score` adds

`ccb score <run_dir>` runs the scorers above on every workspace of a run and writes `summary.json`. All columns are
means over the tasks of the run:

| Column | Rule |
|---|---|
| L1 `page_score`, `geometry_f1`, `text_f1` | mean over the pages; a page without a valid output counts 0 |
| L1 `dimension_f1` | the same, over the pages whose ground truth has dimension entities |
| L1 `layer_f1` | the same, over the pages whose layer ground truth is trusted (`layer_trusted` in `meta.json`) |
| L2 `page_score` | mean over all pages of all sets; on a page whose layer ground truth is not trusted the layer-aware component is left out |
| L2 `shared_value_recall` | mean over the sets |
| L3 `design_recall` | mean over the tasks |
| L3 `vs_copy` | page score of the output minus the page score of the reference page handed in unchanged |

`tasks` and `tasks_in_data` show how many tasks of the level the run covers.

## Does the page scorer react to faults?

```bash
python code/reconstruction/fault_injection.py data/reconstruction/L1/L1-easy-01/gt out/faults.json
```

The script applies ten faults to the ground truth, one at a time, and scores each against the untouched file. The
result equals `results/fault_injection/L1-easy-01.json`; the six files in that folder come from this script on the six
L1 pages of the sample. A fault that finds nothing to act on leaves the score at 100, for example the two dimension
faults on a page without dimension entities.

## Understanding

```bash
ccb selfcheck
ccb grade understanding --tier single <responses.jsonl>
ccb grade understanding --tier few    <responses.jsonl>
ccb grade understanding --tier full   <responses.jsonl>
```

`selfcheck` sends the gold answer of every item, parent and probe through its grader (all must pass) and then an
answer with one letter flipped, one digit changed or a suffix added (all must fail). The graders are the functions of
`code/understanding/grade.py`. Its `multi` and `demo` functions read one folder per drawing set, with `parents.jsonl`
and `probes.jsonl`; `ccb` writes `items.jsonl` in that form into a temporary folder and calls them unchanged. For the
multi-sheet tiers the grader also reports the composition metrics, in total and per drawing set (`sets`). A solution path is a set of probes that suffices to decide the parent. M counts the
parents with at least one fully answered path, K those with a path whose probes are all correct, J those among K
whose parent is also correct. Local coverage LC = K / M, compositional accuracy CA = J / K.

## Notes on the code

- `score.py`, `render.py` and `score_v2.py` are the scorers of the paper. `set_metrics.py` and `fault_injection.py`
  hold the functions of the paper with the run harness around them removed. `grade.py` is a thin command line.
- The renderer uses a CJK font file under `code/reconstruction/fonts/` when one is present, and the fonts of the system
  otherwise. `ccb export workspace` copies that folder into `tools/fonts/`.
- Package versions are pinned in `code/requirements.txt`.
