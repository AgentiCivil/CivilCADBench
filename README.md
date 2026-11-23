<p align="center">
  <a href="https://agenticivil.com/civilcadbench/">
    <img src="assets/banner.png" alt="CivilCADBench: one monochrome print in, one editable DXF file out" width="860">
  </a>
</p>

<h3 align="center">
  Benchmarking Multimodal LLMs for Understanding, Reconstructing,<br>and Designing Civil Engineering Drawing Sets
</h3>

<p align="center">
  <a href="https://agenticivil.com/civilcadbench/"><img alt="Project page" src="https://img.shields.io/badge/Project%20Page-1e46d6?style=for-the-badge"></a>
  <a href="https://agenticivil.com/civilcadbench/#results"><img alt="Leaderboard" src="https://img.shields.io/badge/Leaderboard-0f172a?style=for-the-badge"></a>
  <a href="https://agenticivil.com/civilcadbench/sample/"><img alt="Sample viewer" src="https://img.shields.io/badge/Sample%20Viewer-0f172a?style=for-the-badge"></a>
  <img alt="Dataset on Hugging Face: coming soon" src="https://img.shields.io/badge/Dataset-coming%20soon-9ca3af?style=for-the-badge&logo=huggingface&logoColor=white&labelColor=6b7280">
  <img alt="Paper: coming soon" src="https://img.shields.io/badge/Paper-coming%20soon-9ca3af?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=6b7280">
</p>

<p align="center">
  <b>English</b> | <a href="README_zh.md">简体中文</a>
</p>

A printed drawing keeps the lines and loses the file behind them: the layers, the dimension entities, and the link
between a printed number and the object it describes. CivilCADBench measures how much of that a model can recover
from real civil engineering drawing sets, in three tasks of rising difficulty.

<p align="center">
  <img src="assets/overview.png" alt="Overview of the three tasks" width="100%">
</p>
<p align="center"><sub><b>(a)</b> Understanding: every answer binds a number on the page to what it belongs to. <b>(b)</b> Reconstruction and design: the model returns the editable CAD file behind the pixels.</sub></p>

<table>
  <thead>
    <tr>
      <th width="13%"></th>
      <th width="29%">Understanding</th>
      <th width="29%">Reconstruction (L1, L2)</th>
      <th width="29%">Design (L3)</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th align="left">Input</th>
      <td>page images of one sheet, a few sheets or a whole set, and a question</td>
      <td>a monochrome print of one page (L1) or of a whole set (L2)</td>
      <td>a reference page with its CAD file, and the rest of the set</td>
    </tr>
    <tr>
      <th align="left">Output</th>
      <td>the set of correct options</td>
      <td>one layered DXF file per page</td>
      <td>a new page as a DXF file</td>
    </tr>
    <tr>
      <th align="left">Size</th>
      <td>1,800 + 383 questions</td>
      <td>232 pages / 28 sets</td>
      <td>19 tasks</td>
    </tr>
    <tr>
      <th align="left">Scored by</th>
      <td>exact match</td>
      <td>point-level F1 against the designers' own CAD file</td>
      <td>recall of the values that had to change</td>
    </tr>
  </tbody>
</table>

Every score is computed by a deterministic program. No model acts as a judge.

## Results

<table>
  <thead>
    <tr>
      <th rowspan="2" align="left" width="26%">Model</th>
      <th colspan="3">Reconstruction</th>
      <th>Design</th>
      <th colspan="3">Understanding</th>
    </tr>
    <tr>
      <th align="right" >L1&nbsp;page</th>
      <th align="right" >L1&nbsp;layer&nbsp;F1</th>
      <th align="right" >L2&nbsp;set</th>
      <th align="right" >L3&nbsp;recall</th>
      <th align="right" width="11%">Single</th>
      <th align="right" width="11%">Few</th>
      <th align="right" width="11%">Full</th>
    </tr>
  </thead>
  <tbody>
    <tr><td>GPT-6 Astra</td><td align="right"><b>91.2</b></td><td align="right">59.9</td><td align="right"><b>87.8</b></td><td align="right">47.9</td><td align="right"><b>93.3</b></td><td align="right"><b>95.3</b></td><td align="right"><b>94.5</b></td></tr>
    <tr><td>GPT-6.1 Sol</td><td align="right">90.4</td><td align="right">58.9</td><td align="right">85.9</td><td align="right"><b>49.1</b></td><td align="right">91.5</td><td align="right">92.2</td><td align="right">89.7</td></tr>
    <tr><td>GPT-5.6 Sol</td><td align="right">59.8</td><td align="right">40.8</td><td align="right">36.6</td><td align="right">40.3</td><td align="right">72.2</td><td align="right">75.5</td><td align="right">69.4</td></tr>
    <tr><td>DeepSeek-V4.1-Flash</td><td align="right">81.3</td><td align="right">63.2</td><td align="right">55.4</td><td align="right">37.8</td><td align="right">60.4</td><td align="right">63.8</td><td align="right">58.7</td></tr>
    <tr><td>GLM-5.3-Flash</td><td align="right">76.1</td><td align="right"><b>70.8</b></td><td align="right">47.2</td><td align="right">19.2</td><td align="right">57.4</td><td align="right">60.2</td><td align="right">52.8</td></tr>
    <tr><td>Qwen3.8-27B</td><td align="right">36.2</td><td align="right">30.1</td><td align="right">14.5</td><td align="right">6.3</td><td align="right">58.2</td><td align="right">61.4</td><td align="right">53.6</td></tr>
    <tr><td>Qwen3.5-9B</td><td align="right">5.6</td><td align="right">1.8</td><td align="right">0.0</td><td align="right">0.0</td><td align="right">34.3</td><td align="right">43.1</td><td align="right">8.2</td></tr>
  </tbody>
</table>

<sub>Reconstruction: page score and layer-aware F1 on L1, set score on L2. Design: recall of the values that had to change. Understanding: accuracy on Single-Sheet, Few-Sheet and Full-Set. All on a 0 to 100 scale; best in bold.</sub>

On single pages the best model reaches a geometry F1 of 95.5 and a layer-aware F1 of 59.9: it traces the drawing and
does not recover the file. All columns and the case studies are on the
[leaderboard](https://agenticivil.com/civilcadbench/#results).

<p align="center">
  <img src="assets/qualitative_l1.webp" alt="Two L1 pages: input print, source file, and model outputs coloured by layer" width="100%">
</p>
<p align="center"><sub>Two L1 pages; colours are layers, the number after a model name is its page score. Both GPT-6 models redraw the pier detail line for line (geometry F1 100), but few of their lines carry the layer of the source file (layer-aware F1 12 and 11).</sub></p>

## Quickstart

```bash
git clone https://github.com/AgentiCivil/CivilCADBench
cd CivilCADBench
pip install -e .
```

Python 3.12 or later is needed. This repository ships a sample of the benchmark (60 questions, 2 multi-sheet units, 6 L1 pages, 1 L2 set, 1 L3 task),
so everything below runs right after the clone. Check the installation by scoring the ground truth against itself:

```bash
ccb selfcheck
# reconstruction  8/8 tasks at 100.0 with the ground truth as output
# understanding   132/132 gold answers pass, 132/132 perturbed answers fail
```

## Evaluate your model or agent

CivilCADBench never calls your model. It hands you the inputs as files, you hand back the outputs as files, and the
scorer does the rest. Any model behind any API and any agent framework fits, as long as it can read and write files.

<p align="center">
  <img src="assets/workflow.svg" alt="ccb exports the inputs, your model or agent produces the outputs, ccb scores them" width="100%">
</p>

### Understanding: one answer per question

```bash
ccb export understanding --tier single --out requests/
```

`requests/requests.jsonl` holds one request per line and no answers (shown here on several lines):

```json
{
  "id": "SS-01",
  "kind": "item",
  "tier": "single",
  "system": "...",
  "prompt": "...",
  "images": ["images/SS-01.png"]
}
```

Send each request to your model and write its final text to `responses.jsonl`:

```json
{"id": "SS-01", "response": "... 答案：A,C"}
```

```bash
ccb grade understanding --tier single responses.jsonl
# prints JSON: items, answered, correct, accuracy, and accuracy by binding type
```

`--tier` is `single`, `few` or `full`. [`examples/openai_compatible.py`](examples/openai_compatible.py) does the
middle step for any OpenAI-compatible endpoint; for another API, replace one function in it.

### Reconstruction and design: one workspace per task

```bash
ccb export workspace --level L1 --out runs/my-agent
```

This creates one folder per task. A folder holds what the agent may see and nothing else:

```
runs/my-agent/L1-easy-01/
  TASK.md            the task statement: coordinates, the nine layers, what to deliver
  input.pdf          the monochrome print
  tools/             render_preview.py, to plot a DXF the way the input was plotted
```

Start your agent inside the folder with the fixed prompt in [`docs/PROTOCOL.md`](docs/PROTOCOL.md). It delivers
`output.dxf` (L1, L3) or `output/page_NN.dxf` (L2). Then score the whole run:

```bash
ccb score runs/my-agent
# writes runs/my-agent/summary.json: page score, geometry / layer / text / dimension F1,
# shared-value recall (L2), design recall (L3)
```

A task without a valid output scores 0. [`examples/`](examples) has a launcher for Codex CLI and a container launcher
for Codex CLI and Claude Code that cuts the network and applies the limits below.

### Protocol for comparable numbers

- **Workspace.** A fresh copy per task; one run per task and model; the agent submits once.
- **Outside the workspace.** The source CAD file, the ground truth and the scorer.
- **Reasoning effort.** High, for every model.
- **Network.** None, apart from the model endpoint; no web search.
- **Sandbox.** 8 CPUs, 16 GB of memory, 1024 processes, non-root.
- **Time limit.** 2 hours for L1 and L3, 6 hours for L2; at the limit, the files in the workspace are scored.
- **Reruns.** Only after an infrastructure failure, never after a low score.
- **Understanding.** Single-Sheet and Few-Sheet in one turn; in Full-Set the model opens the pages of the set with tools.

Details and the exact prompts: [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Data

<table>
  <thead>
    <tr>
      <th width="18%"></th>
      <th width="17%">Single-Sheet<br>questions</th>
      <th width="26%">Few-Sheet&nbsp;and&nbsp;Full-Set<br>questions</th>
      <th width="13%">L1 pages</th>
      <th width="13%">L2 sets</th>
      <th width="13%">L3 tasks</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th align="left">This repository</th>
      <td align="center">60</td>
      <td align="center">10, with 62 probes</td>
      <td align="center">6</td>
      <td align="center">1&nbsp;(7&nbsp;pages)</td>
      <td align="center">1</td>
    </tr>
    <tr>
      <th align="left">Full benchmark</th>
      <td align="center">1,800</td>
      <td align="center">383 and 330</td>
      <td align="center">232</td>
      <td align="center">28</td>
      <td align="center">19</td>
    </tr>
  </tbody>
</table>

The full benchmark will be published on Hugging Face. Drawings, questions and task statements are in Chinese, the
language of the source drawing sets; the [sample viewer](https://agenticivil.com/civilcadbench/sample/) shows an
English translation next to every question.

- [`docs/PROTOCOL.md`](docs/PROTOCOL.md): the files exchanged with a model or an agent, the prompts and the rules
- [`docs/DATA.md`](docs/DATA.md): layout and file formats of items, parents, probes and tasks; de-identification
- [`docs/SCORING.md`](docs/SCORING.md): the scorers one by one, the columns of `ccb score`, and the fault-injection test
- [`SAMPLING.md`](SAMPLING.md): how the sample was drawn

## Repository layout

```
civilcadbench/     the ccb command line
code/              the scorers, as used for the paper
examples/          model and agent adapters, container sandbox
data/              the sample
docs/              protocol, data formats, scoring
index.html         offline viewer of the sample (uses viewer/ and data/)
results/           fault-injection scores of the six sample pages
```

## Citation

The citation will be added with the paper.

## Contact

Questions and bug reports: please open an [issue](https://github.com/AgentiCivil/CivilCADBench/issues).
