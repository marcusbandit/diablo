# room

Marcus's Hue lamps follow what his desktop is doing. Read README.md first; it has the scenes,
the hold rule and every command.

## Working here

- Push policy: push as you see fit (personal project, 2026-11-05). Every change goes through a
  branch and a PR with a `code-review --comment` pass, then squash merge.
- `python3 -m pytest -q` covers the decision logic and must pass. CI runs the same.
- Anything that changes what the lamps do gets run on the real lamps: `python3 scripts/e2e.py`
  (about 40 s, restores every lamp afterwards). Marcus is often in the room; say so before running it.
- Walkthrough changes get screenshots of every page: `scripts/shot.sh NAME PAGE`. It renders on a
  headless Broadway display and the app saves its own PNG, so nothing opens on his desktop.
- The installed service runs this checkout (`~/.local/bin/room` links to `bin/room`). After merging,
  `systemctl --user restart room` picks the change up.
- `room/context.py` decides the scene and has no I/O; keep it that way so the tests stay cheap.
- No em or en dashes anywhere, including in tests. Zen window titles contain one; tests build it
  from its code point (see ZEN in tests/test_context.py) instead of typing it.
