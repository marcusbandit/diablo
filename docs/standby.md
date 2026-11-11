# The standby hub

diablo-pi is the primary hub and the only writer of record. nemurenai runs the same `hub/`
code as a standby so the phone still reaches Diablo when the Pi is off. Issue #11.

## Roles

A hub is a standby when its config.toml has a `[standby]` table with `primary`, the Pi's URL.
A standby never starts the Telegram bot and never runs the T3 Code watchers, so exactly one
host polls Telegram: the Pi. Telegram is down while the Pi is down; the phone is the way in.

## States

| State | When | `/v1/health` | Writes |
|---|---|---|---|
| mirror | the Pi answers | 503 | none |
| active | the Pi missed 6 probes in a row, 10 s apart | 200 | `/v1/chat`, `PUT /v1/sections`, `/v1/facts`, `/v1/messages` |
| draining | the Pi passed 2 probes in a row while active | 503 | none |

`GET /v1/standby/status` answers in every state, without a token, and says which state the
host is in, when the last snapshot came, and how many writes wait for the Pi.

## Data flow

In mirror the standby pulls `GET /v1/standby/snapshot` from the Pi every 60 s. The Pi answers
with a `VACUUM INTO` copy of diablo.db, or 304 when the database files have not changed, and
the standby swaps the copy in. A standby that never got a snapshot never goes active.

Going active, the standby moves its message id counter to the next whole million at least a
million above the highest id it has seen. The phone keys messages by hub id and pulls with an
id cursor, so ids the Pi has never used keep its history consistent across both hosts. It also
clears the Pi's Claude session ids, which do not exist on nemurenai, and leaves each
conversation a one time handover note with its last few messages, so the fresh session knows
where it was.

The watermark lives in `standby.json` beside the database, so a restart mid outage keeps the
queue.

## Failback

Draining waits for running turns, closes the brain sessions, and posts every message from the
watermark up, every new fact and the section list to `POST /v1/standby/replay` on the Pi. The
Pi keeps the ids and times, skips rows it already has, stores a clashing id under a new one,
merges sections newest edit wins, raises the usual message events, and leaves each changed
conversation a handover note saying what the standby said. Only then does the standby take a
fresh snapshot and go back to mirror. A failed replay stays in draining and retries on the
next probe; if the Pi goes down again the standby goes active with the same queue.

## Known gap

A Telegram line the Pi stores between coming back and the replay finishing gets an id below
the phone's cursor, so the phone does not show it. It is in the Pi's history and the brain
sees it.

## Running it

- `deploy/ship-standby.sh` ships hub/ and web/ to `~/diablo-hub` on nemurenai, makes the venv,
  copies the Pi's config.toml once with the `[standby]` table added, and restarts the user unit
  `diablo-standby.service`.
- `journalctl --user -u diablo-standby` on nemurenai shows the state changes.
