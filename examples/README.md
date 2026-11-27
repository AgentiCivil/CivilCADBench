# Examples

`ccb run understanding` and `ccb run agent` evaluate a model or an agent in one command; the main
[README](../README.md) shows both. The files here are for a harness of your own: starting points to copy and change.
`docker/` also holds the two container images that `ccb run agent` uses. The rules that make numbers comparable are in
[`docs/PROTOCOL.md`](../docs/PROTOCOL.md).

| File | What it does |
|---|---|
| `openai_compatible.py` | answers exported understanding requests (tiers `single` and `few`) with any OpenAI-compatible chat endpoint; the smallest loop to adapt to another API |
| `run_codex.sh` | runs Codex CLI once in every workspace of a run folder, on this machine, inside Codex's own sandbox |
| `docker/` | agent image and allowlist proxy, used by `ccb run agent`; `run_docker.sh` starts Claude Code or Codex CLI in them without `ccb` |

## A model on the understanding tasks

```bash
ccb export understanding --tier single --out requests/
export OPENAI_API_KEY=...
python examples/openai_compatible.py requests/ --model <model> --out responses.jsonl
ccb grade understanding --tier single responses.jsonl
```

For another API, keep the loop and replace the function `call`: it gets one request (`system`, `prompt`, `images`) and
returns the final text of the model. The `full` tier needs the tool loop of `ccb run understanding`.

## An agent on the reconstruction and design tasks

```bash
ccb export workspace --out runs/my-agent
examples/run_codex.sh <model> runs/my-agent
ccb score runs/my-agent
```

Any agent that can work in a folder fits: start it in each workspace with the prompt of the protocol and let it write
`output.dxf` (L1, L3) or `output/page_NN.dxf` (L2).

## The container images

```bash
cd examples/docker
cp allowlist.example allowlist            # one regular expression per line: the host of your model endpoint
docker build -t cadbench/proxy -f proxy.Dockerfile .
docker build -t cadbench/agent -f agent.Dockerfile .
cd ../..
```

Where the public registries are slow or closed, both images take `--build-arg BASE=<image>` and mirrors for apt, pip
and npm; the Dockerfiles list the arguments.

`ccb run agent` starts the proxy and one agent container per task:

```bash
ccb run agent --level all --agent claude-code --model <model> --env-file model.env --out runs/my-agent
```

`run_docker.sh` does the same from the shell, on workspaces that were exported before:

```bash
ccb export workspace --out runs/my-agent
AGENT=claude ENV_FILE=~/.config/cadbench/model.env examples/docker/run_docker.sh <model> runs/my-agent
ccb score runs/my-agent
```

The agent container has no network. Its only way out is a Unix socket to the proxy container, which lets through the
hosts of `allowlist` and refuses everything else. Limits: 8 CPUs, 16 GB of memory, 1024 processes, non-root, all
capabilities dropped, 2 hours per L1 or L3 task and 6 hours per L2 task. Keys are passed at run time and are not
written into the run folder; the copies of the Codex configuration and key that a Codex run needs are deleted when the
task ends.

The proxy container stays up between runs. Where several people share a machine, each can run a proxy of their own:
`--proxy-name`, `--proxy-socket` and `--proxy-image` of `ccb run agent`, or `PROXY`, `SOCK` and `PROXY_IMAGE` of
`run_docker.sh`. A running container of the given name is used as it is.

These files follow the launchers of our own runs, with the base image, the package mirrors and the endpoint replaced
by public or configurable ones.
