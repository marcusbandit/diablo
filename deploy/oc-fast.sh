#!/bin/bash
# The fast tongue: direct conversation, thinking disabled, one locked session.
# Marcus's voice goes here and nowhere else. Answers land in fast.out for the voice loop.

STATE="$HOME/.local/state/diablo"
cd "$HOME/Projects/diablo" || exit 1
exec > >(tee -a "$STATE/fast-color.log") 2>&1
mkdir -p "$STATE/tasks"
FIFO="$STATE/fast.in"
OUT="$STATE/fast.out"
MODEL="fireworks-ai/accounts/fireworks/models/glm-5p3-flash"
OC="$HOME/.npm-global/bin/opencode"
VENVPY="$HOME/Projects/diablo/.venv-voice/bin/python"
LANGOK="$HOME/Projects/diablo/deploy/lang_ok.py"

rm -f "$FIFO" "$OUT"
mkfifo "$FIFO"
touch "$OUT"
exec 3<> "$FIFO"

SID="$(cat "$STATE/fast.session" 2>/dev/null || true)"
[ -n "$SID" ] || SID=""

echo "fast tongue online: direct talk, thinking off, model $MODEL"

boot() {
  echo "warming the tongue..."
  local raw sid
  raw="$(timeout 60 "$OC" run --auto --variant minimal --format json < /dev/null "Read PERSONA.md in this directory and adopt it as your standing instructions for every reply. Then say exactly: ready, sir." 2>/dev/null)"
  sid="$(printf '%s' "$raw" | python3 -c '
import json, sys
for line in sys.stdin:
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if isinstance(ev, dict) and ev.get("sessionID"):
        print(ev["sessionID"])
        break
' 2>/dev/null)"
  if [ -n "$sid" ]; then
    SID="$sid"
    echo "$SID" > "$STATE/fast.session"
    echo "fast session armed: $SID"
  else
    SID=""
    echo "could not arm a fast session, running sessionless"
  fi
  printf '%s' "ready, sir." > "$OUT"
}

[ -n "$SID" ] || boot

sflags() {
  if [ -n "$SID" ]; then
    printf '%s' "-s|$SID"
  fi
}

run_fast() {
  local text="$1" flags out
  flags="$(sflags)"
  if [ -n "$flags" ]; then
    out="$(timeout 45 "$OC" run --auto --variant minimal < /dev/null -s "$SID" "$text" 2>/dev/null)"
  else
    out="$(timeout 45 "$OC" run --auto --variant minimal < /dev/null "$text" 2>/dev/null)"
  fi
  printf '%s' "$out"
}

while true; do
  IFS= read -r -t 2 -u 3 msg || msg=""
  [ -z "$msg" ] && continue
  msg="${msg:0:2000}"
  echo ""
  echo ">> $msg"

  if [[ "$msg" == TASKDISPATCH:* ]]; then
    payload="${msg#TASKDISPATCH:}"
    id="${payload%%|*}"
    rest="${payload#*|}"
    name="${rest%%|*}"
    result="${rest#*|}"
    echo "(a worker finished: $name, handing the result to memory)"
    run_fast "Background task '$name' just finished. Its result, for your awareness: $result Reply with only: noted." >/dev/null 2>&1
    continue
  fi

  if [[ "$msg" == RAPHAEL:* ]]; then
    payload="${msg#RAPHAEL:}"
    rid="${payload%%|*}"
    rest="${payload#*|}"
    rname="${rest%%|*}"
    rtext="${rest#*|}"
    echo "(Raphael answers: $rname)"
    if ! "$VENVPY" "$LANGOK" <<< "$rtext" 2>/dev/null; then
      if [[ "$rname" == *"(retake)" ]]; then
        rtext="Raphael answered in the wrong tongue and his words were dropped, sir. Ask him again."
      else
        echo "(wrong language, asking Raphael to retake)"
        printf '%s|%s|%s\n' "$rid" "$rname (retake)" "Your previous answer came out in the wrong language. Answer it again, only in English, Japanese or Danish. The request was: $rtext" > "$STATE/raphael.in"
        continue
      fi
    fi
    echo "$rtext"
    printf '%s' "$rtext" > "$OUT"
    continue
  fi

  out=""
  for attempt in 1 2 3; do
    out="$(run_fast "$msg")"
    [ -n "$out" ] && break
    echo "(the tongue stalled, trying again: $attempt)"
  done
  if [ -z "$out" ]; then
    # A killed run poisons the session: start a fresh one and try once more.
    echo "(rescuing: fresh session)"
    rm -f "$STATE/fast.session"
    SID=""
    boot
    out="$(run_fast "$msg")"
  fi
  [ -n "$out" ] || out="My tongue went silent, sir. Say it again."
  for attempt in 1 2; do
    if "$VENVPY" "$LANGOK" <<< "$out" 2>/dev/null; then break; fi
    echo "(wrong language, sending it back: $attempt)"
    out="$(run_fast "Wrong language. Reply only in English, Japanese, or Danish, then answer the previous request again in that language.")"
    [ -n "$out" ] || out="Wrong language slipped out, sir. Say it again."
  done
  if ! "$VENVPY" "$LANGOK" <<< "$out" 2>/dev/null; then
    out="My tongue slipped into the wrong language, sir. Ask me again."
  fi
  echo "$out"

  if [[ "$out" == "RAPHAEL:"* ]]; then
    body="${out#RAPHAEL: }"
    body="$(printf '%s' "$body" | tr '\n' ' ' | cut -c1-3000)"
    id="msg-$(date +%s)"
    echo "(forwarding to Raphael)"
    printf '%s|%s|%s\n' "$id" "${body:0:40}" "$body" > "$STATE/raphael.in"
    out="$(run_fast "The request went up to Raphael, the coordinator. Tell him briefly it is in Raphael's hands, in character, one line.")"
    echo "$out"
  elif [[ "$out" == "TASK:"* || "$out" == "THINK:"* ]]; then
    # A tongue still speaking the old protocol: Raphael takes those too.
    body="$out"
    id="msg-$(date +%s)"
    echo "(forwarding to Raphael: ${body:0:60})"
    printf '%s|%s|%s\n' "$id" "${body:0:40}" "$body" > "$STATE/raphael.in"
    out="$(run_fast "The request went up to Raphael, the coordinator. Tell him briefly it is in Raphael's hands, in character, one line.")"
    echo "$out"
  fi

  printf '%s' "$out" > "$OUT"
done
