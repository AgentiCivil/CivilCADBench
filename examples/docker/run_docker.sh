#!/usr/bin/env bash
# Run an agent once in every workspace of a run folder, each in its own container.
#
#   ccb export workspace --out runs/my-agent --python python
#   AGENT=claude ENV_FILE=~/.config/cadbench/model.env examples/docker/run_docker.sh <model> runs/my-agent
#   ccb score runs/my-agent
#
# Isolation: --network none, with the allowlist proxy as the only way out; the workspace is the only mount with task
# content; non-root, all capabilities dropped, 8 CPUs, 16 GB of memory, 1024 processes. Time limit: 2 hours for L1 and
# L3, 6 hours for L2; when it is reached the container is stopped and what is in the workspace is scored.
#
# AGENT=claude  Claude Code in print mode. ENV_FILE holds the endpoint and the key (ANTHROPIC_BASE_URL,
#               ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY), one per line, readable by you only. It is passed with
#               --env-file and written nowhere. Web fetch, web search and sub-agents are switched off.
# AGENT=codex   Codex CLI. CODEX_HOME_SRC is a folder with the config.toml and auth.json of your endpoint; a copy is
#               mounted for the run and its auth.json is deleted afterwards.
# A workspace that already has a result is skipped. Logs go to <run_dir>/_logs/<task>/.
set -u
MODEL=${1:?model}; RUN=$(cd "${2:?run folder}" && pwd)
AGENT=${AGENT:-claude}
IMAGE=${IMAGE:-cadbench/agent}; PROXY_IMAGE=${PROXY_IMAGE:-cadbench/proxy}; PROXY=${PROXY:-cadbench-proxy}
SOCK=${SOCK:-$HOME/.cache/cadbench/proxy_sock}
LIMIT_PAGE=${LIMIT_PAGE:-7200}; LIMIT_SET=${LIMIT_SET:-21600}
PROMPT_PAGE='请阅读当前目录的 TASK.md 并完成任务，产出 output.dxf 和 NOTES.md。全程不需要向我确认，直接做完。'
PROMPT_SET='请阅读当前目录的 TASK.md 并完成任务，在 output/ 目录下产出每页的 DXF，并写出 NOTES.md。全程不需要向我确认，直接做完。'
case $AGENT in
  claude) : "${ENV_FILE:?ENV_FILE}" ;;
  codex)  : "${CODEX_HOME_SRC:?CODEX_HOME_SRC}" ;;
  *) echo "AGENT is claude or codex" >&2; exit 1 ;;
esac

mkdir -p "$SOCK"; chmod 777 "$SOCK"
# during a Codex run the logs folder holds a copy of the key: keep the other accounts of this machine out of it
mkdir -p "$RUN/_logs"; chmod 700 "$RUN/_logs"
if ! docker ps --format '{{.Names}}' | grep -qx "$PROXY"; then
  docker rm -f "$PROXY" >/dev/null 2>&1
  docker run -d --name "$PROXY" --restart unless-stopped --network bridge -v "$SOCK":/proxy "$PROXY_IMAGE" >/dev/null
  sleep 2
fi

for WS in "$RUN"/*/; do
  WS=${WS%/}; TASK=$(basename "$WS"); LOG=$RUN/_logs/$TASK
  [ -f "$WS/TASK.md" ] || continue
  [ -f "$LOG/.exit_code" ] && continue
  case $TASK in L2-*) PROMPT=$PROMPT_SET; LIMIT=$LIMIT_SET ;; *) PROMPT=$PROMPT_PAGE; LIMIT=$LIMIT_PAGE ;; esac
  NAME=cadbench-$(printf %s "$RUN/$TASK" | sha1sum | cut -c1-12)
  mkdir -p "$LOG/agent_home"; chmod -R a+rwX "$WS" "$LOG"
  COMMON=(--rm --name "$NAME" --network none -v "$WS":/work -v "$SOCK":/proxy
          --cpus 8 --memory 16g --pids-limit 1024 --cap-drop ALL --security-opt no-new-privileges)
  date +%s > "$LOG/.t_start"
  if [ "$AGENT" = claude ]; then
    timeout -k 30 "$LIMIT" docker run "${COMMON[@]}" --env-file "$ENV_FILE" \
      -e ANTHROPIC_MODEL="$MODEL" -e ANTHROPIC_SMALL_FAST_MODEL="$MODEL" -e CLAUDE_CODE_SUBAGENT_MODEL="$MODEL" \
      -e ANTHROPIC_DEFAULT_OPUS_MODEL="$MODEL" -e ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL" -e ANTHROPIC_DEFAULT_HAIKU_MODEL="$MODEL" \
      -e CLAUDE_CONFIG_DIR=/home/agent/.claude -e HOME=/home/agent -v "$LOG/agent_home":/home/agent/.claude \
      "$IMAGE" claude -p "$PROMPT" --model "$MODEL" --output-format stream-json --verbose \
      --dangerously-skip-permissions --disallowedTools WebFetch WebSearch Task Agent \
      > "$LOG/events.jsonl" 2> "$LOG/stderr.log"
  else
    cp "$CODEX_HOME_SRC"/config.toml "$CODEX_HOME_SRC"/auth.json "$LOG/agent_home/"; chmod -R a+rwX "$LOG/agent_home"
    timeout -k 30 "$LIMIT" docker run "${COMMON[@]}" -e CODEX_HOME=/home/agent/.codex -v "$LOG/agent_home":/home/agent/.codex \
      "$IMAGE" codex exec -m "$MODEL" -c model_reasoning_effort='"high"' \
      --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C /work \
      --json -o /work/last_message.md "$PROMPT" \
      > "$LOG/events.jsonl" 2> "$LOG/stderr.log"
  fi
  RC=$?
  docker kill "$NAME" >/dev/null 2>&1
  date +%s > "$LOG/.t_end"
  rm -f "$LOG/agent_home/auth.json"
  # the container writes as uid 1000: hand the files back to the account that runs this script
  docker run --rm --user 0 --network none --entrypoint chown -v "$WS":/w -v "$LOG":/l "$IMAGE" -R "$(id -u):$(id -g)" /w /l
  echo $RC > "$LOG/.exit_code"
  echo "$(date '+%F %T') $TASK exit=$RC"      # 124 = stopped at the time limit
done
