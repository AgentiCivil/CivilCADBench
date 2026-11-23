# Data

This repository holds a sample of the benchmark under `data/`. The full benchmark has the same layout.

```
data/
  understanding/
    single_sheet/              items.jsonl and pages/SS-01.png ...
    multi_sheet/
      prompts.json             instruction lines shown to the model
      items.jsonl              one parent per line, with its probes
      images/<parent id>/      few_sheet/01.png ... and full_set/p01.png ...: every image the parent needs
  reconstruction/
    L1/<task>/                 input.pdf, TASK.md, gt/gt.dxf, gt/meta.json
    L2/<task>/                 input.pdf, TASK.md, meta.json, gt/page_NN/
    L3/<task>/                 TASK.md, ref/ (reference page, context pages), gt/
```

These folders hold the questions together with their answers, and the tasks together with their ground truth. Do not
hand them to a model or an agent: `ccb export` writes the inputs without the answers (see [PROTOCOL.md](PROTOCOL.md)).

Every entry is self-contained. An item, a parent or a task can be copied out together with the files it names, and no
file is shared between two entries: a page that two parents show is stored once under each of them.

| Part | In the sample | In the benchmark |
|---|---|---|
| Single-Sheet items | 60 (10 per binding type) | 1,800 |
| Multi-sheet parents (Few-Sheet and Full-Set) | 10, from 2 drawing sets, with 62 probes | 383 Few-Sheet, 330 Full-Set |
| Reconstruction L1 pages | 6 (2 easy, 2 medium, 2 hard) | 232 |
| Reconstruction L2 sets | 1 (7 pages) | 28 |
| Design L3 tasks | 1 | 19 |

The sample is a stratified draw with a fixed seed that never looks at model outputs; [`SAMPLING.md`](../SAMPLING.md)
lists the draw and every replacement.

## Language

The drawings, the task statements and the items are in Chinese, the language of the source drawing sets. The viewer
and this documentation are in English. The viewer also shows an English translation of every question, option, error
note and probe above the Chinese original (`viewer/translations.js`). The translation is a reading aid added for this
sample: it is not part of the benchmark and was never shown to a model, and the data files under `data/` hold the
Chinese originals only. Names printed on the drawings are quoted in Chinese with an English gloss.

## File formats

**`single_sheet/items.jsonl`** One item per line.

| Field | Meaning |
|---|---|
| `id` | `SS-01` ... `SS-60` in the sample, `SS-0001` ... `SS-1800` in the benchmark |
| `binding` | binding type: `table_block`, `instance`, `cross_view`, `scope`, `view`, `datum` |
| `difficulty` | `standard` or `hard` |
| `question_type`, `question` | the question as shown to the model |
| `options` | six options: `key`, `text`, `correct`, and for a wrong option `why_wrong`, the error it encodes |
| `answer` | the set of correct keys (one to four); an item is correct only on exact match of the set |
| `image`, `dpi`, `size_px` | page image and the resolution at which it was shown |

**`multi_sheet/items.jsonl`** One parent per line. A parent is posed in Few-Sheet over a handful of pages (evidence
pages mixed with look-alike decoy pages) and, when `tiers` says so, in Full-Set over every page of its drawing set.

| Field | Meaning |
|---|---|
| `id` | `MS-Uxx-NN` |
| `set` | label of the drawing set the parent was built on. A label only: no file is looked up through it |
| `tiers` | where the parent is posed: `["few_sheet", "full_set"]`, or `["few_sheet"]` alone |
| `question_type`, `question_style`, `question` | the question as shown to the model |
| `options` | as for a Single-Sheet item, plus `cross_page`, true when the option cannot be decided from one page |
| `answer` | the set of correct keys; exact match of the set |
| `few_sheet` | `images`: the pages shown, in the order shown, as paths below `multi_sheet/`. `dpi`: the resolution of each image. `pages`: the page number of each image. `evidence_pages` and `decoys`: which of them hold the evidence and which are look-alikes |
| `full_set` | `images`: every page of the set, in order, and `page_count`. `null` for a parent posed in Few-Sheet only |
| `probes` | the probes of this parent, see below |
| `solution_paths` | sets of probe ids; each set suffices to decide the parent |

Page numbers count the pages of the drawing set and match `full_set/pNN.png`. For a parent posed in Few-Sheet only,
they count the images of `few_sheet.images` from 1.

**`probes`** Short-answer questions attached to a parent.

| Field | Meaning |
|---|---|
| `id` | `MS-Uxx-NN-R1` ... for `read` probes, `-B1` ... for `bind` probes |
| `role` | `read`: the probe names the object and asks for a printed value. `bind`: it refers to the object through a property printed elsewhere |
| `tiers` | where the probe is posed: `few_sheet`, `full_set` |
| `pages` | pages that hold the evidence |
| `decides` | the parent options this probe decides |
| `on_decoy_page` | true when the probed value sits on a decoy page |
| `response_spec` | answer format requested from the model |
| `grader_id`, `gold_answer`, `tolerance` | grader, gold answer and numeric tolerance |

**Reconstruction** `gt/meta.json` gives the page size in mm, the plotting dpi, the offset of the ground truth and the
layer classes. The nine layer names of the standard are listed in every `TASK.md`. In `L2-01/meta.json` each page
names its output file and its ground-truth folder.

## De-identification

The sample is de-identified: design-office, owner, project and place names were removed.

- Page images of the understanding tasks are re-rendered with everything outside the drawing area blanked: title
  block, sign-off cells, stamps and margin strips. On the pages of the multi-sheet parents the sheet-title and sheet-number cells
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
