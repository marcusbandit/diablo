#!/bin/bash
# desktop: reliable hands for the Diablo workers on banditbox.
# Wayland (Hyprland) first, X11 through XWayland as fallback, every action
# retried. Workers call this instead of poking input tools directly.
#
#   desktop doctor                what works, what is missing
#   desktop launch <app...>       start an app detached from the caller
#   desktop type <text>           type text into the focused window
#   desktop paste [text]          clipboard then ctrl+v, defaults to the text
#   desktop key <combo>           a key press, e.g. desktop key Return
#   desktop click <x> <y>         move the mouse and press left
#   desktop move <x> <y>          absolute mouse move
#   desktop scroll <dx> <dy>      scroll the focused window
#   desktop windows               list open windows
#   desktop activate <class>      raise and focus the first window matching

set -u

RETRIES=3
BACKOFF=0.5

err() { printf 'desktop: %s\n' "$1" >&2; }

# A worker may run from a service with no graphical session in its env; find one.
find_env() {
  local uid
  uid="$(id -u)"
  if [ -z "${WAYLAND_DISPLAY:-}" ]; then
    local wl
    wl="$(ls /run/user/$uid/wayland-* 2>/dev/null | grep -v lock | head -1)"
    [ -n "$wl" ] && export WAYLAND_DISPLAY="${wl##*/}"
  fi
  if [ -z "${DISPLAY:-}" ]; then
    [ -S "/tmp/.X11-unix/X0" ] && export DISPLAY=":0"
  fi
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "/run/user/$uid/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus"
  fi
  export XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-wayland}"
}

ydotool_ready() {
  command -v ydotool >/dev/null || return 1
  local uid sock
  uid="$(id -u)"
  for sock in "${YDOTOOL_SOCKET:-}" "/run/user/$uid/.ydotool_socket" "/run/user/$uid/.ydotool/socket" "/tmp/.ydotool_socket"; do
    [ -n "$sock" ] && [ -S "$sock" ] || continue
    export YDOTOOL_SOCKET="$sock"
    return 0
  done
  return 1
}

retry() {
  # retry <label> <command...>: run it, on failure sleep and try again.
  local label="$1"
  shift
  local attempt
  for attempt in $(seq 1 "$RETRIES"); do
    if "$@"; then
      return 0
    fi
    err "$label failed (attempt $attempt/$RETRIES)"
    sleep "$BACKOFF"
  done
  err "$label gave up after $RETRIES attempts"
  return 1
}

have() { command -v "$1" >/dev/null; }

cmd_doctor() {
  local ok=0
  find_env
  echo "session: ${XDG_SESSION_TYPE:-unknown}, WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset}, DISPLAY=${DISPLAY:-unset}"
  if ydotool_ready; then
    echo "ydotool: yes, socket $YDOTOOL_SOCKET"
  else
    echo "ydotool: no daemon socket found (is ydotoold running?)"
    ok=1
  fi
  for tool in wtype wl-copy hyprctl wmctrl; do
    if have "$tool"; then
      echo "$tool: yes"
    else
      echo "$tool: no"
    fi
  done
  if [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]; then
    echo "graphical env: found"
  else
    echo "graphical env: none found"
    ok=1
  fi
  return $ok
}

cmd_launch() {
  [ $# -gt 0 ] || { err "launch what?"; return 1; }
  find_env
  local app="$*"
  if have hyprctl && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    # Hyprland spawns it in the session, detached from us, with the right env.
    retry "launch ($app)" hyprctl dispatch exec "$app" >/dev/null
  else
    retry "launch ($app)" env setsid nohup "$@" >/dev/null 2>&1
  fi
  echo "launched: $app"
}

cmd_type() {
  [ $# -gt 0 ] || { err "type what?"; return 1; }
  find_env
  if ydotool_ready; then
    retry "type" ydotool type -- "$*"
  elif have wtype; then
    retry "type" wtype -- "$*"
  else
    err "no ydotool or wtype for typing"
    return 1
  fi
}

cmd_key() {
  [ $# -gt 0 ] || { err "which key?"; return 1; }
  find_env
  if have wtype && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    retry "key $1" wtype -k "$1"
  elif ydotool_ready; then
    retry "key $1" ydotool key "$1"
  else
    err "no wtype or ydotool for keys"
    return 1
  fi
}

cmd_click() {
  [ $# -ge 2 ] || { err "click needs x y"; return 1; }
  ydotool_ready || { err "ydotool needed for clicking"; return 1; }
  find_env
  retry "move" ydotool mousemove -a "$1" "$2" || return 1
  sleep 0.15
  retry "click" ydotool click 0xC0
}

cmd_move() {
  [ $# -ge 2 ] || { err "move needs x y"; return 1; }
  ydotool_ready || { err "ydotool needed for moving"; return 1; }
  find_env
  retry "move" ydotool mousemove -a "$1" "$2"
}

cmd_scroll() {
  [ $# -ge 2 ] || { err "scroll needs dx dy"; return 1; }
  ydotool_ready || { err "ydotool needed for scrolling"; return 1; }
  find_env
  retry "scroll" ydotool scroll "$1" "$2"
}

cmd_paste() {
  local text="${*:-}"
  [ -n "$text" ] || { err "paste what?"; return 1; }
  find_env
  have wl-copy || { err "wl-copy missing"; return 1; }
  retry "clipboard" bash -c 'printf %s "$1" | wl-copy' _ "$text" || return 1
  sleep 0.2
  if have wtype && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    retry "paste" wtype -M ctrl -k v -m ctrl
  elif ydotool_ready; then
    retry "paste" ydotool key 29:1 47:1 47:0 29:0
  else
    err "no way to press ctrl+v"
    return 1
  fi
}

cmd_windows() {
  find_env
  if have wmctrl && [ -n "${DISPLAY:-}" ]; then
    wmctrl -l
  elif have hyprctl && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    hyprctl clients | grep -E "class|title|initial" 
  else
    err "no window list available"
    return 1
  fi
}

cmd_activate() {
  [ $# -gt 0 ] || { err "activate what?"; return 1; }
  find_env
  if have hyprctl && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    # Hyprland has no raise-by-class dispatch; focus the first matching client
    # through its IPC, falling back to a workspace jump.
    local address
    address="$(hyprctl clients -j | python3 -c '
import json, sys
target = sys.argv[1].lower()
try:
    clients = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for c in clients:
    if target in (c.get("class") or "").lower() or target in (c.get("title") or "").lower():
        print(c["address"])
        break
' "$1")"
    if [ -n "$address" ]; then
      retry "activate $1" hyprctl dispatch focuswindow "$address" >/dev/null
      echo "focused: $1"
      return 0
    fi
    err "no window matching $1"
    return 1
  elif have wmctrl && [ -n "${DISPLAY:-}" ]; then
    retry "activate $1" wmctrl -a "$1"
    echo "focused: $1"
  else
    err "no hyprctl or wmctrl for activation"
    return 1
  fi
}

find_env

case "${1:-}" in
  doctor) shift; cmd_doctor "$@" ;;
  launch) shift; cmd_launch "$@" ;;
  type) shift; cmd_type "$@" ;;
  paste) shift; cmd_paste "$@" ;;
  key) shift; cmd_key "$@" ;;
  click) shift; cmd_click "$@" ;;
  move) shift; cmd_move "$@" ;;
  scroll) shift; cmd_scroll "$@" ;;
  windows) shift; cmd_windows "$@" ;;
  activate) shift; cmd_activate "$@" ;;
  ""|help|-h|--help)
    sed -n '2,14p' "${0}"
    [ -n "${1:-}" ] || exit 0
    ;;
  *) err "unknown: desktop $1"; sed -n '2,14p' "${0}"; exit 1 ;;
esac
