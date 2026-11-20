# CivilCADBench

**CivilCADBench: Benchmarking Multimodal LLMs for Understanding, Reconstructing, and Designing Civil Engineering
Drawing Sets**

CivilCADBench tests multimodal models on civil engineering drawing sets in three ways: answering questions about one
sheet, a few sheets or a whole set (understanding), redrawing a plotted page or a set of pages as layered DXF
(reconstruction, L1 and L2), and drawing a new page from a reference page and its context (design, L3). All scoring is
deterministic; no model acts as a judge.

This repository holds the scorers of the benchmark and a sample of the data, so that every number the scorers produce
on the sample can be reproduced. The complete benchmark will be released later.

Open `index.html` in a browser to look through the sample (items, pages, answers, probes, reconstruction tasks and
fault-injection scores). The page works offline: it loads nothing from the network.

## Contents

```
index.html                     offline viewer of the sample (uses viewer/ and data/)
README.md                      this file
SAMPLING.md                    how the sample was drawn and every replacement that was made
code/
  requirements.txt
  reconstruction/
    score.py                   page scorer for reconstruction and design outputs (DXF against ground-truth DXF)
    render.py                  monochrome plot renderer used by the scorer and offered to the agent as a preview tool
    set_metrics.py             L2 mean page score and shared-value recall; L3 design recall
    fault_injection.py         the ten fault cases applied to a ground truth
  understanding/
    score_v2.py                typed graders for probes (quantity, slots, label, chainage, endpoints) and option sets
    score.py                   helper functions that score_v2.py imports
    grade.py                   command line around the graders; composition metrics M, K, J, LC, CA
data/
  understanding/
    single_sheet/              60 items (10 per binding type), items.jsonl and pages/SS-01.png ... SS-60.png
    multi_sheet/               2 drawing units, 5 parents each, with their probes
      prompts.json             instruction lines shown to the model
      U01/ U02/                pages/pNN.png (the whole unit), parents.jsonl, probes.jsonl
  reconstruction/
    L1/                        6 single pages (2 per size tier): input.pdf, TASK.md, gt/gt.dxf, gt/meta.json
    L2/L2-01/                  one 7-page set: input.pdf, TASK.md, meta.json, gt/page_NN/
    L3/L3-01/                  one design task: TASK.md, ref/ (reference page, context pages), gt/
results/
  fault_injection/             output of fault_injection.py on the six L1 ground truths
viewer/                        data, English translations and images of index.html
```

The drawings, the task statements and the items are in Chinese, the language of the source drawing sets. The viewer
and this documentation are in English. The viewer also shows an English translation of every question, option, error
note and probe above the Chinese original (`viewer/translations.js`). The translation is a reading aid added for this
sample: it is not part of the benchmark and was never shown to a model, and the data files under `data/` hold the
Chinese originals only. Names printed on the drawings are quoted in Chinese with an English gloss.

## De-identification

The sample is de-identified: design-office, owner, project and place names were removed.

- Page images of the understanding tasks are re-rendered with everything outside the drawing area blanked: title
  block, sign-off cells, stamps and margin strips. In the two multi-sheet units the sheet-title and sheet-number cells
  are kept, because items refer to sheets by title.
- Ground-truth and reference DXF files are rebuilt into fresh files that hold the model-space entities only. The
  header is that of a new file with its save-time stamps set to constants; unused blocks and the original file
  history are gone. Each rebuilt file was compared with its original through the scorer's own entity extraction and
  is identical (sampled points, text values, dimension values).
- PDF files carry no metadata and no document ID; PNG files carry no text chunks.
- Identifiers are local to this sample (`SS-01`, `MS-U01-01`, `L1-easy-01`, ...).
- Pages, units and tasks that name a real road, bridge, station or place in the drawing body, and pages whose
  masking could not be verified, were left out and replaced by the next entry of the seeded order. `SAMPLING.md`
  lists every such replacement.

## The sample

| Part | In this sample | In the benchmark |
|---|---|---|
| Single-Sheet items | 60 (10 per binding type) | 1,800 |
| Multi-sheet parents (Few-Sheet and Full-Set) | 10, in 2 units, with 62 probes | 383 Few-Sheet, 330 Full-Set |
| Reconstruction L1 pages | 6 (2 easy, 2 medium, 2 hard) | 232 |
| Reconstruction L2 sets | 1 (7 pages) | 28 |
| Design L3 tasks | 1 | 19 |

The draw is stratified and uses a fixed seed. It never looks at model outputs. See `SAMPLING.md`.

## Running the scorers

```
pip install -r code/requirements.txt
```

All commands below are run from the repository root. No model, API or network access is involved.

### Reconstruction (L1): one page

```
python code/reconstruction/score.py <gt_dir> <output.dxf> <out_dir>
```

`<gt_dir>` holds `gt.dxf` and `meta.json`. The scorer writes `<out_dir>/score.json` (overall score 0 to 100 and the
five components: geometry F1, layer-aware F1, layer share, text F1, dimension F1) and three pictures (the rendered
output, the output coloured by layer, an overlay on the ground truth). Scoring a ground truth against itself gives 100:

```
python code/reconstruction/score.py data/reconstruction/L1/L1-easy-01/gt data/reconstruction/L1/L1-easy-01/gt/gt.dxf out/self
# out/self/score.json: "valid": true, "overall": 100.0
```

### Reconstruction of a set (L2) and design (L3)

```
python code/reconstruction/set_metrics.py l2 <task_dir> <output_dir>     # output_dir holds page_01.dxf, page_02.dxf, ...
python code/reconstruction/set_metrics.py l3 <task_dir> <output.dxf>
```

With the ground truth as the output:

```
mkdir -p out/l2
for p in data/reconstruction/L2/L2-01/gt/page_*; do cp "$p/gt.dxf" "out/l2/$(basename "$p").dxf"; done
python code/reconstruction/set_metrics.py l2 data/reconstruction/L2/L2-01 out/l2
# {"mean_overall": 100.0, "delivered": 7, "n_pages": 7, "shared_value_recall": 1.0}

python code/reconstruction/set_metrics.py l3 data/reconstruction/L3/L3-01 data/reconstruction/L3/L3-01/gt/gt.dxf
# {"diff_recall": 1.0, "n_new": 3, "content_f1": 1.0, "copy_content_f1": 0.8636, "stale_rate": 0.0}
```

Handing in the reference page unchanged, which is the copy baseline of the design task, gives a design recall of 0:

```
python code/reconstruction/set_metrics.py l3 data/reconstruction/L3/L3-01 data/reconstruction/L3/L3-01/ref/sibling.dxf
# {"diff_recall": 0.0, "n_new": 3, "content_f1": 0.8636, "copy_content_f1": 0.8636, "stale_rate": 1.0}
```

### Fault injection

```
python code/reconstruction/fault_injection.py data/reconstruction/L1/L1-easy-01/gt out/faults.json
```

The script applies ten cases to the ground truth, one at a time, and scores each against the untouched file. About
ten seconds per page. The result equals `results/fault_injection/L1-easy-01.json`; the six files in that folder were
produced by this script on the six L1 pages of the sample. A fault that finds nothing to act on leaves the score at
100, for example the two dimension faults on a page whose ground truth has no dimension entity.

### Understanding items and probes

```
python code/understanding/grade.py demo   data/understanding
# {"gold_pass": 132, "gold_total": 132, "perturbed_fail": 132, "perturbed_total": 132}

python code/understanding/grade.py single data/understanding/single_sheet/items.jsonl <responses.jsonl>
python code/understanding/grade.py multi  data/understanding/multi_sheet/U01 <responses.jsonl> [few_sheet|full_set]
```

`demo` sends the gold answer of every item, parent and probe of the sample through its grader (all must pass) and
then an answer with one letter flipped, one digit changed or a suffix added (all must fail). `single` and `multi`
grade model responses, one JSON object per line: `{"id": "...", "response": "<final text of the model>"}`. `multi`
also reports probe accuracy by role and the composition metrics of the paper (M, K, J, local coverage LC = K / M,
compositional accuracy CA = J / K).

## Notes on the code

- `score.py`, `render.py` and `score_v2.py` are the scorers used for the paper. `set_metrics.py` and
  `fault_injection.py` hold the functions used for the paper with the surrounding run harness removed; `grade.py` is a
  thin command line written for this sample.
- The task statements (`TASK.md`) are the ones given to the agents, unchanged. `{PYTHON}` in them stands for the
  interpreter path of the run, and `tools/render_preview.py` is `code/reconstruction/render.py`.
- Font. The renderer uses a CJK font file under `fonts/` next to `render.py` when one is present, and the fonts of
  the system otherwise.
- The evaluation harness that calls the models (agent loop, image tools, budgets) is not in this repository yet. The
  input of every task is included, so any harness can be run against the sample.

## Data formats

**`single_sheet/items.jsonl`** One item per line.

| Field | Meaning |
|---|---|
| `id` | `SS-01` ... `SS-60` |
| `binding` | binding type: `table_block`, `instance`, `cross_view`, `scope`, `view`, `datum` |
| `difficulty` | `standard` or `hard` |
| `question_type`, `question` | the question as shown to the model |
| `options` | six options: `key`, `text`, `correct`, and for a wrong option `why_wrong`, the error it encodes |
| `answer` | the set of correct keys (one to four); an item is correct only on exact match of the set |
| `image`, `dpi`, `size_px` | page image and the resolution at which it was shown |

**`multi_sheet/Uxx/parents.jsonl`** A parent is posed twice: in Few-Sheet over the pages of `few_sheet.pages_in_order`
(evidence pages mixed with look-alike decoy pages), and in Full-Set over the whole unit, `pages/p01.png` onwards.
`solution_paths` lists sets of probe ids; each set suffices to decide the parent. Options carry `cross_page`, true when
the option cannot be decided from one page.

**`multi_sheet/Uxx/probes.jsonl`** Short-answer questions attached to a parent.

| Field | Meaning |
|---|---|
| `role` | `read`: the probe names the object and asks for a printed value. `bind`: it refers to the object through a property printed elsewhere |
| `tiers` | where the probe is posed: `few_sheet`, `full_set` |
| `pages` | unit pages that hold the evidence |
| `decides` | the parent options this probe decides |
| `on_decoy_page` | true when the probed value sits on a decoy page |
| `response_spec` | answer format requested from the model |
| `grader_id`, `gold_answer`, `tolerance` | grader, gold answer and numeric tolerance |

**Reconstruction** `gt/meta.json` gives the page size in mm, the plotting dpi, the offset of the ground truth and the
layer classes. The nine layer names of the standard are listed in every `TASK.md`. In `L2-01/meta.json` each page
names its output file and its ground-truth folder.
