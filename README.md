# Diablo

A personal assistant: one Diablo you can always reach, by voice in the room, on Telegram, or in
the phone app, that keeps working when the desktop is off. This repository holds all of him.

| Path | What it is | Runs on |
|---|---|---|
| `hub/` | The brain's home: FastAPI service, Telegram bot, memory, routing (Jev first, word tree as backup), T3 Code orchestration, sections | diablo-pi, always on; standby on nemurenai |
| `phone/` | The phone app: Kotlin and Compose over a Rust core | Android |
| `phone/core/` | Rust: Markdown parser, offline store and outbox, host choice | inside the app |
| `presence/` | Diablo on the screen: the orb beside the cursor and his answer cards (Quickshell, QML) | banditbox |
| `voice/` | Microphone, wake word, speech to text, speech. `diablo-ears` (docs/ears.md) is the voice turn that boots at login | banditbox |
| `core/`, `tui/`, `pool/` | The desktop agent and its terminal front end | banditbox |
| `mail/` | Mail watchers beside the hub | diablo-pi |
| `reminders/` | Durable reminders: the `remind` CLI and its scheduler unit, sent on Telegram | diablo-pi |
| `pushup/` | Push-up breaks and the training log, the source of the workout data | banditbox |
| `room/` | The Hue lamps follow what the desktop is doing | banditbox |
| `deploy/` | systemd units, install and ship scripts | |
| `design/` | Design tokens, the brand and his face sources | |
| `web/` | The hub's web app, served at `/app` | diablo-pi |

Design notes live in `docs/` and `SPEC.md`. Rules for coding agents are in `AGENTS.md`.

## Local values

Nothing about the people or machines that run Diablo is written in this repo. Each box keeps
its own addresses, accounts and places in `~/.config/diablo/local.env` (hub, deploy scripts,
systemd units, the phone build) and `~/.config/diablo/config.toml` (the voice side). On a fresh
clone run `scripts/setup-local.sh`: it finds what the box can tell it (Tailscale IP, tailnet,
home subnet) and asks for the rest. `deploy/local.env.example` lists every key.

## Working on it

- Hub tests: `uv run --no-project --with-requirements requirements.txt --with pytest --with httpx python -m pytest -q tests`
- Phone core tests: `cargo test -p diablo-phone`
- Phone app: `cd phone && scripts/emulator.sh && scripts/run.sh && scripts/shot.sh`
- Ship the hub to the Pi: `deploy/ship-hub.sh`, then the standby: `deploy/ship-standby.sh`

Work is tracked as GitHub issues and lands through pull requests.
