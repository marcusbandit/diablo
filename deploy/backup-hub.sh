#!/usr/bin/env bash
set -euo pipefail

REMOTE=diablo-pi
SNAP=/tmp/diablo-snapshot.db
BACKUP_DIR="$HOME/.local/share/diablo/hub-backups"

ssh "$REMOTE" "python3 -" "$SNAP" <<'PY'
import os, sqlite3, sys
snap = sys.argv[1]
if os.path.exists(snap):
    os.unlink(snap)
try:
    db = sqlite3.connect(os.path.expanduser('~/diablo-hub/diablo.db'))
    db.execute('VACUUM INTO ?', (snap,))
    db.close()
except sqlite3.Error as e:
    print(f'snapshot failed: {e}', file=sys.stderr)
    sys.exit(1)
PY

mkdir -p "$BACKUP_DIR"
DEST="$BACKUP_DIR/diablo-$(date +%Y%m%d-%H%M%S).db"
scp -q "$REMOTE:$SNAP" "$DEST"
ssh "$REMOTE" "rm -f $SNAP"

python3 - "$DEST" <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect(sys.argv[1])
    check = db.execute('PRAGMA integrity_check').fetchone()[0]
    if check != 'ok':
        print(f'integrity check failed: {check}', file=sys.stderr)
        sys.exit(1)
    counts = {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('messages', 'facts', 'settings')}
except sqlite3.Error as e:
    print(f'verify failed: {e}', file=sys.stderr)
    sys.exit(1)
print(f"backup ok: {sys.argv[1]} messages={counts['messages']} facts={counts['facts']} settings={counts['settings']}")
PY

find "$BACKUP_DIR" -name 'diablo-*.db' -type f | sort -r | tail -n +15 | xargs -r rm -f
