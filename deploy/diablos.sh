#!/bin/bash
# The Diablo workers: a pool of worker demons in Raphael's service.
# Every task runs in its own opencode session, all of them in parallel.
# More information drops into a task's .inbox file while it runs; the
# worker folds it in during a continuation round before reporting done.
# Status files live in ~/.local/state/diablo/tasks for the board and the voice loop.

STATE="$HOME/.local/state/diablo"
cd "$HOME/Projects/diablo" || exit 1
exec > >(tee -a "$STATE/workers-color.log") 2>&1
mkdir -p "$STATE/tasks"
FIFO="$STATE/think.in"
MODEL="fireworks-ai/accounts/fireworks/models/glm-5p3-flash"
OC="$HOME/.npm-global/bin/opencode"
VENVPY="$HOME/Projects/diablo/.venv-voice/bin/python"
LANGOK="$HOME/Projects/diablo/deploy/lang_ok.py"
ROUND_TIMEOUT="${DIABLO_ROUND_TIMEOUT:-600}"
MAX_WORKERS="${DIABLO_MAX_WORKERS:-4}"

rm -f "$FIFO"
mkfifo "$FIFO"
exec 3<> "$FIFO"
echo $$ > "$STATE/workers.pid"

echo "diablo workers online: up to $MAX_WORKERS in parallel, round timeout ${ROUND_TIMEOUT}s, model $MODEL"

declare -A PIDS
declare -A NAMES
declare -A PROMPTS
QUEUE=()

clean_out() {
  # Strip ANSI, keep the model's own text from the json events, flatten to one line.
  python3 - "$1" << 'PYEOF'
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
chunks = []
for line in raw.splitlines():
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if ev.get("type") == "text" and isinstance(ev.get("part"), dict):
        t = ev["part"].get("text", "")
        if t.strip():
            chunks.append(t.strip())
text = " ".join(chunks) if chunks else re.sub(r"\x1b\[[0-9;]*m", "", raw)
text = re.sub(r"\x1b\[[0-9;]*m", "", text)
text = re.sub(r"\s+", " ", text).strip()
# The newest text is the final report, so when it all will not fit, keep the tail.
print(text[-1500:])
PYEOF
}

PERSONA="You are a Diablo, a worker demon dispatched by Raphael, the Lord of Wisdom. Your report is spoken aloud: plain sentences written for the ear, no markdown, no lists, no dashes."

inbox_lines() {
  # Print and drain a task's inbox, one message per line.
  local f="$STATE/tasks/$1.inbox"
  [ -f "$f" ] || return 0
  local out
  out="$(cat "$f")"
  : > "$f"
  [ -n "$out" ] && printf '%s' "$out"
}

start_round() {
  local id="$1" name="$2" prompt="$3" mode="$4" out_file
  out_file="$STATE/tasks/$id.out"
  local round_prompt
  if [ "$mode" = "continue" ]; then
    local notes
    notes="$(inbox_lines "$id")"
    round_prompt="$PERSONA The task '$name' is already underway. While it ran, more information arrived from Marcus: $notes The transcript so far is at $out_file (opencode json events, oldest first). Read its end, fold the new information in, continue the work, and report concisely."
    echo "$(date +%H:%M:%S) continuation round: $name ($id)"
  else
    round_prompt="$PERSONA Do this work yourself with tools, then report concisely: $prompt Never end with an ANSWER line, a plain report is all Raphael needs."
    echo "$(date +%H:%M:%S) task started: $name ($id)"
    echo -n "running" > "$STATE/tasks/$id.status"
    echo -n "$name" > "$STATE/tasks/$id.name"
    printf '%s' "$prompt" > "$STATE/tasks/$id.prompt"
    : > "$STATE/tasks/$id.inbox"
  fi
  timeout "$ROUND_TIMEOUT" "$OC" run --auto --format json -m "$MODEL" < /dev/null --title "$name" "$round_prompt" >> "$out_file" 2>&1 &
  local pid=$!
  PIDS[$pid]=$id
  NAMES[$pid]="$name"
  PROMPTS[$pid]="$prompt"
}

dispatch_task() {
  local id="$1" name="$2" prompt="$3"
  mkdir -p "$STATE/tasks"
  [ -f "$STATE/tasks/$id.inbox" ] || : > "$STATE/tasks/$id.inbox"
  start_round "$id" "$name" "$prompt" "first"
}

reap() {
  for pid in "${!PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      tid="${PIDS[$pid]}"
      wait "$pid" 2>/dev/null
      status="$(cat "$STATE/tasks/$tid.status" 2>/dev/null)"
      if [ "$status" = "canceled" ]; then
        echo "$(date +%H:%M:%S) task finished canceled: ${NAMES[$pid]}, work discarded"
        unset "PIDS[$pid]"
        unset "NAMES[$pid]"
        continue
      fi
      if [ -s "$STATE/tasks/$tid.inbox" ]; then
        # The dead pid must leave the pool before its continuation takes a slot.
        tname="${NAMES[$pid]}"
        tprompt="${PROMPTS[$pid]}"
        unset "PIDS[$pid]"
        unset "NAMES[$pid]"
        unset "PROMPTS[$pid]"
        start_round "$tid" "$tname" "$tprompt" "continue"
        continue
      fi
      echo -n "done" > "$STATE/tasks/$tid.status"
      echo "$(date +%H:%M:%S) task finished: ${NAMES[$pid]}"
      result="$(clean_out "$STATE/tasks/$tid.out")"
      if ! "$VENVPY" "$LANGOK" <<< "$result" 2>/dev/null; then
        echo "$(date +%H:%M:%S) task result in the wrong language, asking for it again"
        timeout 300 "$OC" run --auto --format json -m "$MODEL" < /dev/null --title "${NAMES[$pid]}" "$PERSONA Wrong language: report only in English, Japanese, or Danish. The task was: ${PROMPTS[$pid]}" > "$STATE/tasks/$tid.out" 2>&1
        result="$(clean_out "$STATE/tasks/$tid.out")"
        if ! "$VENVPY" "$LANGOK" <<< "$result" 2>/dev/null; then
          result="The work ran, but my report came out in the wrong language and was discarded. Ask and I will redo it."
        fi
      fi
      printf '%s' "$result" > "$STATE/tasks/$tid.clean"
      printf 'TASKDISPATCH:%s|%s|%s\n' "$tid" "${NAMES[$pid]}" "$result" > "$STATE/fast.in"
      "$VENVPY" "$HOME/Projects/diablo/deploy/task-notify.py" "$tid" "${NAMES[$pid]}" < "$STATE/tasks/$tid.clean" >/dev/null 2>&1 || true
      unset "PIDS[$pid]"
      unset "NAMES[$pid]"
      unset "PROMPTS[$pid]"
    fi
  done
}

active_count() {
  local n=0
  for pid in "${!PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null && n=$((n + 1))
  done
  printf '%s' "$n"
}

pump() {
  # Move queued work into free worker slots.
  while [ "${#QUEUE[@]}" -gt 0 ] && [ "$(active_count)" -lt "$MAX_WORKERS" ]; do
    local next="${QUEUE[0]}"
    QUEUE=("${QUEUE[@]:1}")
    IFS='|' read -r id name prompt <<< "$next"
    dispatch_task "$id" "$name" "$prompt"
  done
}

while true; do
  IFS= read -r -t 2 -u 3 msg || msg=""
  if [ -n "$msg" ]; then
    id="${msg%%|*}"
    rest="${msg#*|}"
    name="${rest%%|*}"
    prompt="${rest#*|}"
    QUEUE+=("$id|$name|$prompt")
  fi
  reap
  pump
  for pid in "${!PIDS[@]}"; do
    if [ "$(cat "$STATE/tasks/${PIDS[$pid]}.status" 2>/dev/null)" = "canceled" ]; then
      kill "$pid" 2>/dev/null
    fi
  done
done
