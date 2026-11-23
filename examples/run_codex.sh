#!/usr/bin/env bash
# Run Codex CLI once in every workspace of a run folder, on this machine.
#
#   ccb export workspace --out runs/my-agent
#   examples/run_codex.sh <model> runs/my-agent
#   ccb score runs/my-agent
#
# Codex keeps to the workspace through its own sandbox (-s workspace-write). For an unattended run with no network
# and fixed resources, use the container launcher in examples/docker instead. A workspace that already has a result
# is skipped, so the script can be started again. Logs go to <run_dir>/_logs/<task>/.
# Needs GNU timeout (on macOS: brew install coreutils).
set -u
MODEL=${1:?model}; RUN=$(cd "${2:?run folder}" && pwd)
LIMIT_PAGE=${LIMIT_PAGE:-7200}       # L1 and L3: 2 hours
LIMIT_SET=${LIMIT_SET:-21600}        # L2: 6 hours
TIMEOUT=$(command -v timeout || command -v gtimeout) || { echo "GNU timeout not found" >&2; exit 1; }
PROMPT_PAGE='请阅读当前目录的 TASK.md 并完成任务，产出 output.dxf 和 NOTES.md。全程不需要向我确认，直接做完。'
PROMPT_SET='请阅读当前目录的 TASK.md 并完成任务，在 output/ 目录下产出每页的 DXF，并写出 NOTES.md。全程不需要向我确认，直接做完。'

for WS in "$RUN"/*/; do
  TASK=$(basename "$WS"); LOG=$RUN/_logs/$TASK
  [ -f "$WS/TASK.md" ] || continue
  [ -f "$LOG/.exit_code" ] && continue
  case $TASK in L2-*) PROMPT=$PROMPT_SET; LIMIT=$LIMIT_SET ;; *) PROMPT=$PROMPT_PAGE; LIMIT=$LIMIT_PAGE ;; esac
  mkdir -p "$LOG"; date +%s > "$LOG/.t_start"
  "$TIMEOUT" -k 30 "$LIMIT" codex exec -m "$MODEL" -c model_reasoning_effort='"high"' -c approval_policy='"never"' \
    -s workspace-write --skip-git-repo-check -C "$WS" --json -o "$LOG/last_message.md" "$PROMPT" \
    > "$LOG/events.jsonl" 2> "$LOG/stderr.log"
  echo $? > "$LOG/.exit_code"; date +%s > "$LOG/.t_end"
  echo "$(date '+%F %T') $TASK exit=$(cat "$LOG/.exit_code")"      # 124 = stopped at the time limit; what is in the workspace is scored
done
