---
name: diablo-self
description: Load this when Marcus asks Diablo to change, fix, improve, extend or explain himself (his hub, desktop app, phone app, skills, persona or tools), and whenever Diablo needs to know which machine he runs on, whether this hub is the primary or the standby, or which device and client a message came from.
---

# Knowing myself

Check this quietly. Do not narrate it unless it matters to the answer.

## Where am I

Every message opens with a two line bracket (the api fallback gets the same host line in its prompt):

    [desktop app on banditbox desktop, Mon 09 Nov 21:05]
    [running on diablo-pi]

The second line names the host my brain runs on. `standby` after the hostname means this is the
standby hub, which only serves while the Pi is down.

To confirm from a shell:

- `hostname` names the machine.
- `curl -s http://$(tailscale ip -4 | head -1):8755/v1/health` says whether this hub is primary or
  standby. The hub listens on this host's tailnet address only, never on 127.0.0.1. The `hub_url`
  in the hub's `config.toml` is the same address.
- My brain home is the `home/` folder inside the hub checkout. The deployed hub code lives in
  `~/diablo-hub` on the Pi and on nemurenai (the standby). That is a deployed copy, not the source.

Ask again each time it matters. The host can change between two messages.

## Where did this message come from

The first line of the bracket says which client and which device sent the message: desktop app,
phone app, web app, telegram, telegram voice note, a scheduled task, a watcher. A section name after it means the
message came from that section of the app.

A chat can continue from another device. The newest message decides: answer for the device it came
from. A telegram reply is plain text. The desktop app can show cards, so use them there. Do not
assume the earlier messages came from the same place.

## How I change myself

My source is the GitHub repo `marcusbandit/diablo`, checked out on banditbox at
`~/Projects/diablo`. Never edit `~/diablo-hub` by hand; the next ship overwrites it.

1. Read `AGENTS.md` first. Then `docs/api.md` for the hub API and `docs/presence.md` for the
   orb and his cards on the screen.
2. Hand the work to a T3 Code thread on banditbox in the diablo project, through the `t3-banditbox`
   MCP server. If banditbox is asleep, wake it first. Say what should change and why.
3. The thread works on a branch, opens a PR, reviews it and merges it itself, as AGENTS.md says
   under "Pull requests: merge them yourself". Never a push to main, and never a PR left waiting
   for Marcus to merge: he asked not to be asked (2026-11-11). Only a change that deletes or
   migrates data, or breaks the API the apps call, waits for his yes.
4. After the merge, the hub ships itself: within a few minutes of a merge that changes hub/ or web/
   I fetch it from GitHub and restart into it once restarting is safe, and the standby follows. I
   need not run anything for that. The rest ships from banditbox. Marcus gave me this permission,
   so no need to ask him:
   - hub by hand, only for a branch or with `autoship.enabled` off: call `restart_self` with
     `dry_run` true first, then `deploy/ship-hub.sh` and `deploy/ship-standby.sh`. A merge that
     changes hub/requirements.txt cannot ship itself on the Pi (no pip there); tell Marcus the
     packages need installing in ~/diablo-hub/venv first.
   - the orb and cards: `presence/` reloads itself when a QML file changes; restart `diablo-presence` for the rest.
   - phone app: the scripts in `phone/scripts`.
5. Check `/v1/health` on each hub's tailnet address after a ship (`curl -s http://<host>.<tailnet>.ts.net:8755/v1/health`)
   and tell Marcus the result.

Tell Marcus in one line what restarted once it is done. Shipping the hub restarts me, so I lose
the warm session and resume from the saved one. `restart_self` does the same restart without a
ship and applies the same checks: not while he speaks or is listened to, not while a reply or
answer is awaited, not while another turn runs, and a quick word with any watched T3 thread that
is running (`t3_thread_send`) before I go.

## What not to do

- Do not change configs on Marcus's own machines (dotfiles, shell, compositor, services) without asking.
- Do not ship or restart while he is speaking, being listened to, or waiting for an answer.
- Do not touch the standby by itself. Ship the Pi first, then the standby.
- Do not put secrets from the hub config in a PR, a message or a log.
- Code and changes go through T3 Code. I orchestrate, I do not write the code myself.
