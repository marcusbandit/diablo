# Diablo project

This repository IS the Diablo voice assistant, a product. You are not Diablo. You are a coding agent working on his code. The butler persona lives in PERSONA.md for the sessions that speak as him; do not act it out here.

## This repo is public

Nothing about the people or machines that run Diablo goes in here: no tokens, email addresses,
phone numbers, Tailscale IPs, the tailnet name, LAN addresses, MAC addresses, home paths,
places, or real mail, chats and health data, in code, tests, docs, screenshots, issues or PRs.
Per-box values live in `~/.config/diablo/local.env` (read by `hub/localenv.py`, the deploy
scripts, the systemd units and the phone build) and `~/.config/diablo/config.toml` (voice);
`scripts/setup-local.sh` writes them and `deploy/local.env.example` lists every key. Tests use
`example.com`, `100.64.0.x` and made-up names. Run `gitleaks git --staged` before each commit.

## What the product is

Diablo is a voice assistant and Telegram bot. He listens on a microphone, wakes on his name, answers in a demon butler's voice, and runs real work through a worker pool. The full architecture lives in SPEC.md; read it before changing behavior.

- `hub/`: FastAPI service on the always-on Pi. Telegram bot, shared memory (messages, facts, settings) over HTTP.
- `voice/`: the mic to action loop on the desktop box. Capture, VAD, STT, engagement, the fast tongue (voice/fast.py), tools and the agent loop (voice/agent.py), TTS. voice/stt_server.py is diablo-stt, the hub's local Whisper on the tailnet; every speech to text path is mapped in docs/stt.md.
- `pool/`: Raphael the coordinator and the worker demons (pool/pool.py), the fast tongue panel (pool/panel.py), the direct provider client with the native tool loop (pool/brains.py), the hands-on full screen agent in the terminal (pool/repl.py, bare `diablo`, -c continues, -r picks a session). Direct API calls, no opencode anywhere.
- `core/`: the rust rewrite of the brain: provider client, brain ranks, tool loop, session store, local api on 127.0.0.1:8766 with streaming turns (ndjson) and cancel. The session json format is shared with the python side, so either brain can pick up the other's sessions. Build with cargo from core/, test with `cargo test` from the repo root (workspace: core + tui).
- `tui/`: the rust terminal front end (ratatui) on the core api. Bare `diablo` prefers it and falls back to the python curses one (pool/repl.py) when the binary is missing.
- `deploy/`: systemd units, the tmux stage, desktop control.
- `onboarding/`: interactive setup for both sides.
- `phone/`: the phone app, Kotlin and Jetpack Compose over a Rust core (`phone/core`, uniffi). Design in docs/phone.md. Build and verify on the emulator through phone/scripts/.
- `presence/`: Diablo on the screen, the orb beside the cursor and his answer cards, its own Quickshell instance (`diablo-presence.service`). It reads the state files the voice side writes; docs/presence.md. The old Qt desktop app is not in this repo any more: it lives in the private archive and is being rebuilt around the orb.
- `mail/`: the older mail watchers that run beside the hub on diablo-pi. The hub's own mail handler (both inboxes sorted, Jorbis answered under approvals) is hub/mailbox.py, described in docs/mail.md.
- `reminders/`: durable reminders on diablo-pi, the `remind` CLI and `diablo-reminders.service`. Its README covers times, DST and the exactly-once rules.
- `pushup/`: the push-up breaks on banditbox, `pushup.service`, and the training log that the workout data comes from. docs/sync.md in there holds the log, overview and inbox formats.
- `room/`: the Hue lamps follow the desktop on banditbox, `room.service`. room/CLAUDE.md has its rules, including when to run the real-lamp e2e.

The old checkouts `~/Projects/pushup-break` and `~/Projects/room` link into this repo, and the units run from them. A merge to main followed by `git pull` in `~/Projects/diablo` and a restart of the unit is how a change in either reaches the desktop.

## The hub brain (switchboard, 2026-10-14)

The hub on diablo-pi no longer runs its own model loop by default. hub/brains.py drives a logged-in coding CLI the way T3 Code does, picked by the brain.backend setting:

- claude (default): one warm `claude -p --input-format stream-json` process per conversation, resumed by session id after the 15 minute idle reaper closes it. Subscription login on the Pi, no API key. Model and effort in brain.claude.model and brain.claude.effort.
- opencode: `opencode run --format json` per turn, model in brain.opencode.model, config written to the brain home as opencode.json and AGENTS.md.
- api: the old Fireworks tool loop in hub/agent.py, also the fallback when a CLI turn fails.

The system prompt is hub/charter.py, kept byte-stable so the prompt cache holds; per-turn context (origin with client and device, time, the host the brain runs on) rides in a bracket at the top of each message. hub/skills/* is copied into the brain home's .claude/skills on every start; `diablo-self` tells him where he runs and how to change himself. Tools reach the CLI over MCP: hub/mcp_server.py (stdio, the house tools from agent.TOOLS plus notify and watch_t3_thread) and one t3-<host> server per T3 Code environment. hub/t3link.py authorizes those itself: it mints a pairing code with `t3 pair` over ssh, runs T3's MCP OAuth (register, pairing-code decision, token), and keeps 30 day full-access tokens in ~/.local/state/diablo/t3-mcp.json, renewed every 6 hours when close to expiry. hub/watch.py polls watched T3 threads and gives the brain a [t3 event] turn when one needs him (the wake rule below), which is pushed to Telegram. Watches live in the database, so a restart keeps them.

Task cards (hub/tasks.py, `GET /v1/tasks`, the `task` event): every watched T3 thread is a task card the hub keeps itself, with no model turn: watch_t3_thread creates it with a kind (`self_improvement` for the diablo repo or work he started for himself, `partner` for anything else, taken from the thread's project when the argument is left out), and hub/watch.py and hub/t3threads.py update its state, current step and PR from the thread. Other work gets a card through the update_task tool. The wake rule is `watch.wakes(kind, state)`: a finished self-improvement thread only updates its card, no [t3 event] turn and no Telegram push; waiting on Marcus, failed, and finished partner work give him a turn. The charter's Task cards section tells him the card is the report, so replies about T3 work stay one short line.

He may restart himself (hub/selfrestart.py, the `restart_self` tool, `POST /v1/restart`). The hub checks first and refuses, with the reasons, while any turn runs, Marcus is speaking or being listened to (the ears report their state to `POST /v1/ears/report` every 15 s and on each change; silence or an unknown state counts as unsafe), an approval or a watched T3 thread's question is open, or the last restart was under `restart.min_interval_s` (300) ago. `restart.allowed` switches it off. A safe request waits for the reply to finish, then ends the process for systemd to bring back, the claude session resumes from its stored id, and he tells Marcus in one line afterwards. Each request and refusal is logged and appended to ~/.local/state/diablo/restarts.jsonl.

Locked mode (hub/lockguard.py): turns from banditbox, voice at his desk or the desktop app there, read the screen lock over ssh (`hyprctl -i 0 locked` plus banditshell's logind LockedHint) and carry `[banditbox screen: ...]`. While locked, or unknown on a spoken turn, only an allowlist of harmless tools runs: the MCP server, the api agent and a Claude PreToolUse hook all refuse the rest and log to ~/.local/state/diablo/locked.log. lock.enabled turns it off.

Conversations: Telegram, the fleet app and the web app's scratch chat share the main conversation; other web chats get web-<chat id>. The main conversation is filed into inline threads (hub/threads.py): each message carries thread ids in meta.threads, a Haiku call through the claude CLI picks the threads of each new line, and the turn's context holds only those threads. Each thread is a claude session lane: the warm session goes on while a turn stays in its thread, a switch takes a pre-spawned spare seeded with the retrieved context. threads.enabled turns it off and brings back the old recap. Telegram answers only the owner chat (telegram_owner, else telegram_chat_id).

## Sessions that speak as Diablo

The services that speak as him carry the persona in their system prompts, following PERSONA.md:

- pool/panel.py (diablo-fast.service): the fast tongue, one locked session, spoken replies.
- pool/pool.py (diablo-pool.service): the coordinator Raphael and the worker demons.

The voice loop's own agent (voice/agent.py) and the hub agent carry their persona inline. When a prompt tells you that you are one of those, follow PERSONA.md as your standing instructions. When you are an ordinary coding agent, ignore it and work on the code.

## Shipping the hub

hub/ in git is what runs on diablo-pi. Ship it with `deploy/ship-hub.sh` (keeps a dated backup of the live hub/, restarts diablo-hub, waits for /v1/health); `--dry-run` shows what would change. Never edit ~/diablo-hub on the Pi by hand: change it here, commit, ship. A merge to main that changes hub/ or web/ ships itself: the Pi hub fetches the commit from GitHub, checks it imports, keeps a backup and restarts through the `restart_self` checks, and the standby follows the Pi (hub/autoship.py, docs/api.md "Updating himself after a merge"). `ship-hub.sh` stays for a branch or when `autoship.enabled` is off. A requirements change is refused on the Pi, whose venv has no pip or uv: install the packages into ~/diablo-hub/venv by hand, then ship. Diablo may run it himself once `restart_self` with `dry_run` says it is safe.

hub_token in ~/diablo-hub/config.toml is for the machines only (voice, pool, core, task-notify on banditbox). Phones and browsers pair for a device token of their own (docs/phone.md, Pairing). `deploy/rotate-hub-token.sh` replaces hub_token on the Pi, the standby and banditbox and restarts what reads it; it never prints the token.

The standby hub on nemurenai runs the same hub/ with a `[standby]` table in its config.toml: it mirrors the Pi's database every minute, serves the phone only after the Pi has missed about a minute of health checks, and hands its writes back when the Pi returns. It never runs the Telegram bot or the T3 watchers. Ship it with `deploy/ship-standby.sh` after shipping the Pi. Design in docs/standby.md.

## Working on the code

- Python 3.12. The voice venv is .venv-voice. Tests: `.venv-voice/bin/python -m pytest tests/ -q` from the repo root, or `python3 -m pytest tests/ -q`.
- No comments in code. No em dashes or en dashes anywhere. Sentence case in UI strings.
- Settings and keys come from the hub's settings API and ~/.config/diablo/config.toml, never hardcoded.
- The voice loop restarts into new code on reboot_self or `systemctl --user restart diablo-voice`.
- The healer (voice/healer.py) patches crashes at runtime with backups in ~/.local/state/diablo/heal-backups; keep its smoke import list current when you add modules.

## Pull requests: merge them yourself

Marcus asked for this on 2026-11-11: Diablo should change himself without him having to say
"open a PR" or "merge it". In this repo a coding thread finishes its own work, all the way:

1. Work on a branch named `<type>/<issue>-<slug>`, never on main.
2. Run the tests that cover what you changed, then open the PR with `gh pr create`: what changed,
   why, how you checked it (test output, a screenshot for UI), and `Closes #N` when there is an issue.
3. Review it: run the `code-review` skill against the PR. Fix what it finds in new commits on the
   branch, or say in the PR why a finding stays.
4. Merge it yourself, without asking: `gh pr merge --squash --delete-branch`. Checks that ran must
   pass. Checks that never started (Actions blocked by billing, the annotation says so) do not block
   the merge; the test output in the PR body stands in for them, and the PR says so.
5. Say it is merged at the end of the thread. Diablo then ships it (`hub/skills/diablo-self`).

Leave the PR open and ask Marcus first only when the change deletes data, migrates stored data
(a database schema, a state file format), or breaks something another app relies on (the hub API
the phone and desktop apps call, the settings keys). Everything else merges.
