#!/bin/sh
# Diablo's tmux stage: panes tailing the services, rebuilt any time a pane
# is missing. Run by autostart and kept honest by the stagehand.
STATE="$HOME/.local/state/diablo"
tmux has-session -t diablo 2>/dev/null || tmux new-session -d -s diablo -n voice

window_exists() {
  tmux list-windows -t diablo -F '#W' 2>/dev/null | grep -qx "$1"
}

ensure_pane() {
  name="$1"; log="$2"
  if ! window_exists "$name"; then
    mkdir -p "$STATE"
    touch "$STATE/$log"
    tmux new-window -t diablo -n "$name" "tail -f $STATE/$log"
    tmux set-option -w -t "diablo:$name" automatic-rename off 2>/dev/null
  fi
}

ensure_pane voice voice-color.log
ensure_pane fast fast-color.log
ensure_pane pool pool.log
if ! window_exists talk; then
  tmux new-window -t diablo -n talk "$HOME/Projects/diablo/.venv-voice/bin/python $HOME/Projects/diablo/deploy/tui.py"
fi
window_exists thinking && tmux kill-window -t diablo:thinking
window_exists raphael && tmux kill-window -t diablo:raphael
window_exists workers && tmux kill-window -t diablo:workers
tmux select-window -t diablo:voice
