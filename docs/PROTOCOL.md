# Evaluation protocol

CivilCADBench exchanges files with the system under test: `ccb export` writes the inputs, a model or an agent writes
the outputs, `ccb grade` and `ccb score` read them. `ccb run understanding` and `ccb run agent` do the three steps in
one command, with the prompts and limits of this page. This page lists the files and the rules that make two sets of
numbers comparable, for `ccb run` and for a harness of your own.

## Understanding

```bash
export OPENAI_API_KEY=...
ccb run understanding --tier all --model <model> --base-url https://<host>/v1 --out runs/<name>
```

`ccb run understanding` sends the requests of a tier to an OpenAI-compatible chat completions endpoint, the images as
PNG in the order of the request, and grades the answers. Per tier it writes `runs/<name>/understanding/<tier>/` with
`responses.jsonl`, `result.json` and, for `full`, one file per request in `traces/`. A call that gets HTTP 429 or a
5xx answer is tried up to three times in all. A request that still fails is listed in `errors.log` and taken up when
the same command is started again; finished requests are kept.

The same steps as files, for a harness of your own:

```bash
ccb export understanding --tier single --out requests/      # single | few | full
```

The folder holds `requests.jsonl` and `images/`. One request per line:

| Field | Meaning |
|---|---|
| `id` | id of the item, parent or probe |
| `kind` | `item` (Single-Sheet), `parent` or `probe` (Few-Sheet, Full-Set) |
| `tier` | `single`, `few` or `full` |
| `system` | system prompt |
| `prompt` | user message: a JSON object with the instruction, the question and the options or the answer format |
| `images` | page images, relative to the folder, in the order in which they are shown |

A request never holds an answer, an error note or the role of a page. `--parents-only` leaves out the probes.

Write one line per request to a responses file, with the final text of the model unchanged:

```json
{"id": "SS-01", "response": "... 答案：A,C"}
```

```bash
ccb grade understanding --tier single responses.jsonl
```

**Answer formats.** Items and parents have six options, of which one to four are correct. The answer is read from the
last line that starts with `答案：`, and it is correct only if the set of letters equals the gold set. A probe is a
short answer: the value of the last `{"answer": ...}` object in the response, checked by the grader named in the probe
(quantity with unit, named slots, label, chainage, pair of endpoints). A request without a response counts as wrong.

**Tiers.**

- `single`: one page image and one item, answered in one turn.
- `few`: the pages of the parent, evidence pages mixed with look-alike pages of the same set, in a fixed order that is
  not the page order. Parents and probes are answered in one turn each.
- `full`: every page of the drawing set. The model does not receive the pages in the message: it opens them with a
  tool, `view_page` (a whole page or a crop), and has a `calculate` tool for arithmetic. `ccb run understanding`
  offers both tools and stops a request at 16 model turns, 24 page views, 40 tool calls, 8,192 generated tokens per
  turn or 65,536 per request; when a limit is reached, the model gets one closing turn without tools. A view is at
  most 1,568 pixels on its long edge and is cut from the released page image, whereas our runs rendered it from the
  vector drawing, so a zoomed view shows less detail. The limits can be changed with `--steps`, `--views`,
  `--tool-calls`, `--step-tokens` and `--total-tokens`; say so with your results. The system prompt of the full-tier
  requests names the two tools: adapt that sentence if your harness offers other ones, and say so with your results.

**Rules.** One response per request. No answer, evidence page or error note may reach the model. A request is repeated
only after an infrastructure failure (endpoint error, time-out of the service), never after a wrong answer.

## Reconstruction and design

```bash
ccb run agent --level all --agent claude-code --model <model> --env-file model.env --out runs/<name>
```

`ccb run agent` writes one workspace per task into the run folder, starts the agent once in each with the prompt
below, stops it at the time limit and scores what it delivered. The agent is Claude Code (`--agent claude-code`),
Codex CLI (`--agent codex`) or a command of your own (`--agent-cmd`). Each task runs in a container that applies the
limits of the table below; the two images are built from `examples/docker`. Logs go to `runs/<name>/_logs/<task>/`.
A task that was interrupted starts again from a fresh workspace when the same command is started again; finished
tasks are kept. With `--local` the agent runs on this machine without a container: nothing keeps it from the network
or from the ground truth, so these numbers are not protocol results, and `ccb report` says so.

The same steps as files, for a harness of your own:

```bash
ccb export workspace --level L1 --out runs/my-agent        # L1 | L2 | L3 | all
```

One folder per task:

| File | L1 | L2 | L3 |
|---|---|---|---|
| `TASK.md` | task statement: paper coordinates in mm, the nine layers, what to deliver | same, with the list of pages | same, with the description of the page to draw |
| `input.pdf` | one raster page | the raster pages of the set | |
| `ref/` | | | the reference page as DXF and PDF, the other pages of the set as PDF, and their index |
| `tools/render_preview.py` | plots a DXF the way the input was plotted | same | same |

`--python` sets the interpreter that `TASK.md` names (default `python`). It needs ezdxf, pymupdf,
opencv-python-headless, scikit-image, numpy, scipy, pillow and matplotlib. The ground truth and the scorer are not in
the workspace and must not be reachable from it.

Start the agent inside the folder with this prompt, unchanged:

- L1 and L3: `请阅读当前目录的 TASK.md 并完成任务，产出 output.dxf 和 NOTES.md。全程不需要向我确认，直接做完。`
- L2: `请阅读当前目录的 TASK.md 并完成任务，在 output/ 目录下产出每页的 DXF，并写出 NOTES.md。全程不需要向我确认，直接做完。`

The agent delivers `output.dxf` (L1, L3) or `output/page_NN.dxf` (L2) and a short `NOTES.md`.

```bash
ccb score runs/my-agent        # writes runs/my-agent/summary.json and runs/my-agent/_scores/
```

**Rules.**

| | |
|---|---|
| Workspace | a fresh copy per task; one run per task and model; the agent submits once |
| Tools | shell, files and images inside the workspace; no web search, no web fetch |
| Network | none, apart from the model endpoint |
| Sandbox | 8 CPUs, 16 GB of memory, 1024 processes, non-root |
| Time limit | 2 hours for L1 and L3, 6 hours for L2; at the limit the run is stopped and the files in the workspace are scored |
| Reasoning effort | high |
| Missing output | a task without a valid output scores 0 on the page score and on every component; a missing page of a set scores 0 |
| Reruns | only after an infrastructure failure, never after a low score; say which runs were repeated and why |

`ccb run agent` applies these limits. `examples/docker` holds the two images it uses and a shell launcher with the
same limits.

## What to report with a result

- the output of `ccb report runs/<name>`: the scores, how many tasks and requests they cover, and whether the run kept
  to this page
- the model: name, version or weights, serving framework, context length, reasoning effort or thinking budget
- the agent: name and version, start command, tools that were switched off
- the limits that were applied, and every deviation from this page
- for understanding: tier, image resolution as sent, and for `full` how the model looked at the pages
- the runs that were repeated, and why
