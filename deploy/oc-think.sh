#!/bin/bash
# The deep mind: tasks dispatched by the fast tongue, each its own opencode session.
# Status files live in ~/.local/state/diablo/tasks for the dashboard and the voice loop.

STATE="$HOME/.local/state/diablo"
cd "$HOME/Projects/diablo" || exit 1
exec > >(tee -a "$STATE/think-color.log") 2>&1
mkdir -p "$STATE/tasks"
FIFO="$STATE/think.in"
MODEL="fireworks-ai/accounts/fireworks/models/glm-5p3-flash"
OC="$HOME/.npm-global/bin/opencode"
VENVPY="$HOME/Projects/diablo/.venv-voice/bin/python"
LANGOK="$HOME/Projects/diablo/deploy/lang_ok.py"

rm -f "$FIFO"
mkfifo "$FIFO"
exec 3<> "$FIFO"

echo "deep mind online: tasks run here in their own sessions, model $MODEL"

declare -A PIDS
declare -A NAMES

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
text = "\n".join(chunks) if chunks else re.sub(r"\x1b\[[0-9;]*m", "", raw)
text = re.sub(r"\x1b\[[0-9;]*m", "", text)
text = re.sub(r"\s+", " ", text).strip()
print(text[:1500])
PYEOF
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
      echo -n "done" > "$STATE/tasks/$tid.status"
      echo "$(date +%H:%M:%S) task finished: ${NAMES[$pid]}"
      result="$(clean_out "$STATE/tasks/$tid.out")"
      if ! "$VENVPY" "$LANGOK" <<< "$result" 2>/dev/null; then
        echo "$(date +%H:%M:%S) task result in the wrong language, asking for it again"
        timeout 300 "$OC" run --auto --format json -m "$MODEL" < /dev/null --title "$name" "You are the deep mind. Wrong language: report only in English, Japanese, or Danish. The task was: $prompt" > "$STATE/tasks/$tid.out" 2>&1
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
    fi
  done
}

while true; do
  IFS= read -r -t 2 -u 3 msg || msg=""
  if [ -n "$msg" ]; then
    id="${msg%%|*}"
    rest="${msg#*|}"
    name="${rest%%|*}"
    prompt="${rest#*|}"
    echo "$(date +%H:%M:%S) task started: $name ($id)"
    echo -n "running" > "$STATE/tasks/$id.status"
    echo -n "$name" > "$STATE/tasks/$id.name"
    printf '%s' "$prompt" > "$STATE/tasks/$id.prompt"
    timeout 300 "$OC" run --auto --format json -m "$MODEL" < /dev/null --title "$name" "You are the deep mind, dispatched by the fast tongue. Do this work yourself with tools, then report concisely: $prompt" > "$STATE/tasks/$id.out" 2>&1 &
    PIDS[$!]=$id
    NAMES[$!]=$name
  fi
  reap
  for pid in "${!PIDS[@]}"; do
    if [ "$(cat "$STATE/tasks/${PIDS[$pid]}.status" 2>/dev/null)" = "canceled" ]; then
      kill "$pid" 2>/dev/null
    fi
  done
done
