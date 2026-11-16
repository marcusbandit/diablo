#!/usr/bin/env bash
# Fake Diablo hub for the phone app. See fake-hub.py for the commands.
#   fake-hub.sh start [--port N] [--url URL] [--keep]
#   fake-hub.sh code | status | stop | log [-n N]
#   fake-hub.sh say [--in SECONDS] [--objects JSON] <section> <text>
#   fake-hub.sh task <id> <queued|working|waiting|done|failed> [--title T] [--step S]
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
export PYTHONDONTWRITEBYTECODE=1

case "${1:-}" in
  start|serve)
    if [[ -n ${DIABLO_FAKE_HUB_PYTHON:-} ]]; then
      exec "$DIABLO_FAKE_HUB_PYTHON" "$here/fake-hub.py" "$@"
    fi
    # uv keeps the hub's requirements in its cache, nothing lands in the repo.
    exec uv run --quiet --no-project --with-requirements "$repo/hub/requirements.txt" \
      python "$here/fake-hub.py" "$@"
    ;;
  ""|-h|--help)
    exec python3 "$here/fake-hub.py" --help
    ;;
  *)
    # code, say, task, status, stop, log only need the standard library.
    exec python3 "$here/fake-hub.py" "$@"
    ;;
esac
