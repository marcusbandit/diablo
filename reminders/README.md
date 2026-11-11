# diablo-reminders

Durable reminders for Diablo, Marcus's butler agent on diablo-pi. Diablo sets a
reminder with a tool call, and at the due time a small systemd unit sends it on
Telegram in Diablo's voice. The reminders live in sqlite on disk, so restarting
diablo-hub.service, a crash or a Pi reboot loses nothing and repeats nothing.

Before this, Diablo could only remind through a session cron job, which lived in
the hub's memory and died with every restart.

## How it fits together

```
Diablo (claude session in diablo-hub)
  └ MCP server hub/mcp_server.py
      └ add_reminder / list_reminders / cancel_reminder
          └ hub/modules/reminders.py runs `remind --json ...`
              └ ~/.local/state/diablo/reminders.db   (sqlite, WAL)
                    ▲
diablo-reminders.service (systemd user unit, own process)
  └ `remind run`: claims due reminders, sends them with the hub's Telegram bot
```

- Store: `~/.local/state/diablo/reminders.db`, overridden by `REMIND_DB`.
- Telegram: the scheduler reads `telegram_bot_token` and `telegram_chat_id` from
  the hub's `diablo.db` settings at every send, the same values the hub's
  `notify` tool uses, with the hub's `config.toml` as the fallback for the token.
  After a send it adds the message to the hub's history as `notify` does, so
  Diablo knows what it said when Marcus answers.
- The unit is independent of diablo-hub.service. Restarting the hub does not
  touch it; restarting it does not touch the hub.

## Tasks

A reminder can be a task instead: something Diablo does himself at the due time, such as "mail
Jorbis in five hours". Set it with `add_reminder` and `task: true`, or `remind add --task`. At the
due time the scheduler hands the instruction to the hub (`POST /v1/scheduled`, the hub's own
`hub_token` from `config.toml`), which runs it as a brain turn in the main conversation and sends
Marcus one Telegram line with the result. The hub answers at once and works in the background, so
a long task never holds up a reminder.

Tasks keep every guarantee below. The delivery id is the request's `client_id`, and the hub runs
each `client_id` once, so a retry after a lost answer never runs a task twice. A hub that is down is
an ordinary failed send and is retried with the same backoff.

## Times, recurrence and DST

All times are wall clock times in Europe/Copenhagen. A reminder keeps its wall
time, and the UTC instant is worked out per occurrence, so a daily reminder at
13:00 stays at 13:00 across both DST switches.

- Recurrence: `none`, `daily`, `weekly` (on the weekday of the first time) and
  `weekdays` (Monday to Friday; a weekend start moves to Monday).
- A time in the spring gap, such as 02:30 on the last Sunday of March, fires an
  hour on, at 03:30.
- A time in the autumn overlap, such as 02:30 on the last Sunday of October,
  fires once, at the first 02:30, summer time.
- Due strings: `2026-11-08T13:00`, `2026-11-08 13:00`, ISO with an offset or
  `Z`, a clock time `13:00` (the next time it comes round), or relative `+20m`,
  `+1h30m`, `+2d`. Without an offset it is Copenhagen time.

## Firing exactly once

Each occurrence goes through two committed steps:

1. Claim. In one `BEGIN IMMEDIATE` transaction the scheduler inserts a
   delivery row for the occurrence and moves the reminder to its next
   occurrence, or marks a one-off done. A unique key on (reminder, due time)
   means no occurrence can be claimed twice, even by two processes. A file
   lock also keeps a second `remind run` from starting at all.
2. Deliver. It marks the attempt `sending`, calls Telegram, then marks it
   `sent`.

Missed reminders: whatever came due while nothing ran is claimed on startup and
sent marked as late, saying when it was due. A recurring reminder that came due
several times while the Pi was down is sent once, for the most recent missed
time, and says how many more it missed; the series then carries on from the
next future time.

When a send fails (no network, Telegram down or refusing), the delivery goes
back to pending and is retried with backoff: 30 s, doubling, at most 30 min
apart, longer if Telegram asks for it with `retry_after`. A message that only
gets through after more than two minutes says it is late. After 24 hours of
failures it is marked `failed` and stays visible in `remind list --all`.
Cancelling a reminder also stops a delivery that is waiting for a retry.

The one gap no design closes: Telegram has no idempotency key, so if the
process dies in the instant after Telegram accepted a message and before the
`sent` mark is written, the outcome is unknown. Recovery treats `sending` as
not sent and retries it once on the next start. That trades a rare duplicate
for never silently losing a reminder, which is the right way round for a
reminder.

The Pi has no real time clock. Until NTP syncs after a boot the clock runs
behind, never ahead, so a reminder can only be late, never early.

## The remind CLI

```
remind add 2026-11-08T13:00 "Time to go home and make dinner"
remind add 08:30 "Stand up" --repeat weekdays
remind add +20m "Check the oven"
remind list            # active ones
remind list --all      # with done, cancelled and how their last delivery went
remind cancel 3
remind run             # the scheduler loop, what the unit runs
```

`--json` before the command prints JSON, which the hub shim uses. zsh
completion is `_remind` in `~/.local/share/zsh/site-functions`, bash completion
in `~/.local/share/bash-completion/completions/remind`; both complete
subcommands, `--repeat` values and, for `cancel`, the live reminder ids.

## Install on diablo-pi

```
deploy/install.sh            # default host diablo-pi
```

It copies the code to `~/.local/lib/diablo-reminders`, writes the
`~/.local/bin/remind` wrapper and both completions, installs
`deploy/diablo-reminders.service` as a systemd user unit (bandit has linger, so
it starts at boot), enables and restarts it. It needs no sudo, keeps the stored
reminders, and leaves diablo-hub alone. Running it again updates in place.

Logs: `journalctl --user -u diablo-reminders`.

Note: the diablo repo's `deploy/diablo-remind.service` belongs to the older
voice announcer `remind.py` in that repo. It is not installed on diablo-pi and
this unit has a different name, so the two do not clash.

## Wiring the tools into Diablo

The hub carries the tools itself, as the reminders module in `hub/modules/reminders.py`. It runs
`remind --json`, hands back the sentence it prints and lists the upcoming reminders in the Home
panel of the app. A missing install comes back as a plain error. The charter tells Diablo that
every remind me order goes to `add_reminder`, never a session cron job. It ships with the hub
through `deploy/ship-hub.sh`.

| tool | arguments |
| --- | --- |
| `add_reminder` | `text`, `due` (ISO like `2026-11-08T13:00`, or `+20m`), `recurrence` (`none`, `daily`, `weekly`, `weekdays`) |
| `list_reminders` | `include_past` (optional) |
| `cancel_reminder` | `id` |

## Tests

```
python3 -m unittest discover -s tests -t .
```

Standard library only. They cover due time parsing, recurrence, both DST
switches, restart recovery at every point of a delivery, exactly once firing
with concurrent schedulers, retries and backoff, the scheduler as a real
process killed with SIGKILL and started again, the CLI and the hub shim.
