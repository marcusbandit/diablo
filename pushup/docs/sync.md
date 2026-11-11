# Sync with nemurenai

The training data lives on banditbox and is mirrored to nemurenai and diablo-pi by Syncthing, so
the Diablo hub on either box can show it and add to it, and the app shows it from the hub. Two
folders, and every file in them has one writer, so nothing ever conflicts:

| folder id | path on every box | banditbox | nemurenai | diablo-pi | what |
| --- | --- | --- | --- | --- | --- |
| `pushup` | `~/.local/state/pushup` | send only | receive only | receive only | `log.jsonl` and `overview.json` |
| `pushup-inbox` | `~/.local/state/pushup-inbox` | receive only | send and receive | send and receive | training and sets from other devices |

nemurenai and diablo-pi also pass the files on between each other, so the Pi stays current from
nemurenai while banditbox is off. The Diablo hub writes its own inbox file,
`diablo-<hostname>.jsonl`, through its `workout_log` tool (hub/modules/workout.py).

`pushup` carries an allow-list `.stignore` on both boxes, so the daemon's own state files
(`next_due`, `day_start` and the rest) stay local:

    !/log.jsonl
    !/overview.json
    *

`pushup sync` writes the overview at once and lists what the inbox holds.

## log.jsonl

One JSON object a line, appended by pushup on banditbox only. Never edit it from elsewhere.

- A card's set: `{"ts", "exercise", "reps", "did", "result"}`. `reps` is the goal, `did` what got done;
  `result` is `done`, `skip`, `pass` (a rest day or bonus card let go, never held against you) or `lost`
  (the card died). Holds count seconds.
- A set logged by hand: `{"ts", "exercise", "did", "result": "manual", "source"}`.
- A part that hurts: `{"ts", "result": "hurt", "part", "spots"}`, and `"result": "healed"` when it's
  better. `part` is `push`, `pull`, `legs` or `core`; `spots`, when they were picked, is a list of
  `[muscle, side]` pairs like `["triceps", "left"]`, side being the person's own left or right.
- Training: `{"ts", "result": "session", "activity", "minutes", "effort", "source"}`. `effort` is `easy`,
  `normal` or `hard`.

Exercise and activity names are the keys of `EXERCISES`, `STRETCHES` and `ACTIVITIES` in `catalog.py`,
which also maps each to the muscles it works.

## overview.json

Rewritten after every log line and every 15 minutes by the daemon, so a reader needs none of
pushup's rules. `schema` goes up when a field changes meaning.

- `schema`: 1. `generated`: unix time. `day`: the date the day counts as; a day runs until 07:00.
- `mode`: `mixed` or `challenge`.
- `today`: `sets`, `line` (the day in words: "10 sets: 99 push-ups in 7 sets, ..."), `trained` (sessions).
- `streak`: days in a row with a set or training. `last`: the last set, `{ts, exercise, did}`.
- `week`: the last seven days on the body. `work` and `stretch` are 0 to 1 per muscle, `work_raw` in
  push-up equivalents, `stretch_raw` in seconds held, `neglected` the three muscles that got least.
- `timeline`: four weeks by muscle group. `first` is the first date, `days` the count. `work` and `sets`
  hold a list per group (`push`, `pull`, `legs`, `core`), one number a day. A set counts for the groups
  it mainly works. `sessions` is `[{day, what, groups}]` with `day` an index into those lists, `moves`
  the move behind most of each group's sets, `recent` the last seven days newest first as
  `{date, sets, lines}`.
- `challenge`: null outside the challenge, otherwise `day`, `days`, `start`, `done` and `target` (push-ups),
  `left`, `deadline` (unix time), `bss` and `rows` (`sets` of `cap` today, `goal`), `feet_up`,
  `stretching`, `history` (`[{day, done, target, trained}]`), `trained` (today's sessions that count as
  the day's training, described; while it isn't empty no cards come).

## The inbox

Each writer keeps its own file, `~/.local/state/pushup-inbox/<writer>.jsonl` on nemurenai, and only
appends to it. pushup reads every file there as part of its log: the cards steer around muscles a
session tired, a heavy session makes a rest day, and the receipts and the overview count it all.

    {"id": "hc-7f3a", "ts": 1793900000, "result": "session", "activity": "climbing", "minutes": 90, "effort": "hard", "source": "health-connect"}
    {"id": "ph-0912", "ts": 1793903600, "result": "manual", "exercise": "body rows", "did": 10, "source": "phone"}

- Only `session` and `manual` lines count. A `ts` in the future or before 2020, more than a day of
  `minutes`, or a `did` that isn't a whole number from 1 to 1000 makes the line count for nothing.
- `id` makes a line count once, across files too. Give every line one; a line without it is matched
  on its time, result and name.
- A session of an activity this box already logged that day, with `pushup train`, counts once: the
  one logged here wins.
- `exercise` goes through the catalog's names and aliases, so `rows` is body rows. Use the
  `EXERCISES` and `STRETCHES` keys where you can.
- An `activity` pushup doesn't know counts as `other` and keeps its own name for display, so map
  Health Connect's types to the `ACTIVITIES` keys where one fits. A missing or unknown `effort` is `normal`.
- `source` defaults to the file's name.
