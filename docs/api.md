# The hub API for apps

Every face of Diablo talks to the hub on diablo-pi over this API: the phone app today, a desktop
app later. Nothing in it is phone specific. A new client needs a token, the calls below and a
renderer for the block kinds at the end.

Base URL: `http://diablo-pi:8755` on the tailnet (the Pi's Tailscale address, `DIABLO_HUB_URL` in local.env). The standby hub on nemurenai answers the same
API when the Pi is down; see docs/standby.md.

## Auth

Send `X-Diablo-Token: <token>` on every call. A client gets its own token by pairing:

1. Someone with a token calls `POST /v1/pair/code`, or Marcus sends `/pair` on Telegram.
2. The new client sends `POST /v1/pair {"code": "...", "name": "<device name>"}` and gets
   `{"token": "...", "device": {...}}` back. Store the token in the platform's keystore.

`GET /v1/whoami` says which device a token belongs to.

## Health

`GET /v1/health` needs no token and answers `{"ok", "role", "stt", "revision"}`. `revision` is the short commit in hub/REVISION, and, where `stt` names the speech to
text backend that takes the next voice note (`local`, `cloud`, or empty when none is set up).
`GET /v1/stt?wait=false` with a token gives the detail:
`{"order", "active", "backends": [{"name", "model", "healthy", "checked"}], "last": {"backend", "at"}}`.
Both read cached health; `wait=true` checks first. docs/stt.md.

## One memory, many sections

Sections are places to talk, each with a focus: Diablo, Workout, Projects, Home. They share one
memory: whatever is said in one section, on Telegram or by voice, the brain knows in all of them.
The section only adds a note with its brief to the turn.

- `GET /v1/sections`: `{"sections": [...], "now": <unix time>}`. Each section has `id`, `name`,
  `icon`, `accent` (`#rrggbb`), `brief`, `position`, `hidden`, `deleted`, `updated` and `panels`.
- `PUT /v1/sections {"sections": [...]}`: merge by `updated`, newest wins. A client that does not
  send `panels` keeps the hub's.
- `GET /v1/sections/{id}/messages?after=<id>&limit=<n>`: the section's messages, oldest first.
  `before=<id>` instead of `after` pages backwards: the newest `limit` messages older than that id.
  Every message carries `threads`, the ids of the threads it is filed under (see Threads).

## Threads

The main conversation is one long stream, and every message in it is filed under one or more
threads, each a strand of subject with a short title and a one line descriptor of what has
happened in it. The hub files each new line as it arrives (docs in hub/threads.py) and loads only
the threads a turn touches into the brain's context. Clients show the stream as before and may
mark each message with its threads, filter to one thread and correct the filing.

- `GET /v1/threads?state=open`: `{"threads": [...], "now", "migration": {"done", "upto", "left"}}`,
  most recently active first. Each thread has `id`, `title`, `descriptor`, `created`, `last_active`,
  `state` (`open`, `archived` or `merged`), `merged_into`, `named` (true once Marcus renamed it, so
  the hub stops retitling it) and `count`, its number of messages. `state` takes a comma list.
  `migration` says how far the one-time filing of the old history has come.
- `GET /v1/threads/{id}/messages?before=<id>&limit=40`: one page of a thread, oldest first, plus
  `more` when older messages exist. Pass the first message's id as `before` for the page above it.
- `POST /v1/threads {"title", "descriptor"?, "messages"?: [ids]}` makes a thread, and files the
  given messages under it as well, or only under it with `"move": true`. With `"split_from": "<thread id>"` it splits instead: the
  messages of that thread from the lowest given id onwards move to the new thread.
- `PATCH /v1/threads/{id} {"title"?, "state"?: "open" | "archived", "merge_into"?: "<id>"}`
  renames, archives or reopens, or merges every message of this thread into another.
- `POST /v1/threads/tag {"messages": [ids], "add": [...], "remove": [...], "set"?: [...], "pair"?: true}`
  re-files messages. With `pair` (the default) the other half of each turn moves along: the reply
  to a line, or the line a reply answered. Answers `{"tags": {"<message id>": [thread ids]}}`.

- `GET /v1/search?q=<words>&limit=200&thread=<id>`: main conversation messages that hold every
  word of `q`, in any case, newest first, as `{"messages": [...], "now"}` with `threads` on each.
  `thread` keeps one thread's. Clients rank and mark the hits themselves.

`POST /v1/chat` answers with `threads: [{"id", "title"}]`, where the turn was filed, so a client can
say so and offer an undo (a `tag` call back). The events stream sends `{"kind": "threads"}` when
threads change, with `tags: {"<message id>": [...]}` when messages were re-filed.

## Talking

`POST /v1/chat {"text", "section", "source", "client_id"}` runs one turn and returns
`{"reply", "id", "user_id", "section", "objects", "backend"}`. The stored reply's `meta.steps` lists
the tool steps that made it, `[{step, tool}]` as in the `turn` event, when there were any. `client_id` makes a retry safe: the
same id never runs twice. `objects` are blocks, the same kinds a panel uses.

A voice turn from the ears may add `"desktop": {"windows": [{address, class, title, workspace,
focused}], "players": [...]}`. Jev then also decides whether the line is one plain desktop command
(close or switch to a window, a workspace, play, pause, next, previous, volume, mute) in the call it
makes anyway. When it is sure enough (`route.desktop_threshold` 0.8 to close a window, a tab or press enter,
`route.desktop_light_threshold` 0.6 for the rest), no model runs and the reply carries
`"act": {"kind": "desktop", "action", "did", ...}` with `window`, `workspace` or `player` as the
action needs. The ears run it on banditbox. Anything else goes on to Claude as before.
Every reply also carries `"timing": {"brain_ms", "jev_ms", "backend", "model", "tools", "tier"}`, the
hub's own split of the turn, when the turn went through the router.

A turn cannot be rewritten once answered, since the brain's session already holds it, so editing
and answering again are new turns that say what they replace:

- `"edits": <id>` sends `text` in place of one of his earlier messages in the same section. The new
  message carries `meta.edits`, the brain is told the old message and every answer after it are
  withdrawn, and each of those gets `meta.superseded_by` set to the new message's id. 404 when the
  id is not his message in that section.
- `"regenerates": <id>` answers the question behind that reply again. No new user message is
  stored; the question is the newest user message in the section before the reply, read on the
  hub, and `user_id` in the answer is its id. The new reply carries `meta.regenerates`, the old one
  `meta.superseded_by`, and the brain is told its earlier answer was discarded. A failed turn
  leaves both as they were. 404 when the id is not a reply in that section.

Sending both is a 400. Clients decide how to show superseded messages; the desktop app folds old
replies into the new one as versions and greys out a withdrawn branch.

`POST /v1/turns/stop {"section"}` ends the turn running in that section's conversation, the way
`/stop` does in Telegram, and answers `{"stopped": true}`, or `false` when nothing was running. The
waiting `POST /v1/chat` then returns with whatever the brain had. 404 for an unknown section.
`GET /v1/events?ping=<seconds>` sets how often the stream sends a keepalive comment (5 to 600,
default 15); a client that wants to notice a silent hub quickly asks for a short one.

## Questions

Diablo asks Marcus a question with choices through the `ask_user` tool, the way a form does:
a `header` of a few words, the `question`, two to four `options` (`label`, `description`), the
option he `recommended` (an index from 0, or null), whether more than one may be picked (`multi`),
whether Marcus may answer in his own words (`allow_text`), and whether it is `harmless` enough to
answer from a locked banditbox. The hub keeps it with an `id` (`q` and eight hex digits).

The reply that asked carries it in `objects` as a block of kind `question` (see Block kinds), with
its `state`: `pending`, then `answered` (with `answer: {choices, labels, text, source, at}`),
`expired` (after `questions.ttl_s`, an hour by default), `cancelled` or `superseded` (he asked a
newer one in the same conversation; only one is pending at a time). The stored block is kept up
to date, so a client that fetches the message later sees the current state.

- `POST /v1/questions/{id}/answer {"choices": [0], "text": "", "source", "client_id"}` answers it
  with option indices from 0, his own words, or both. The hub stores his message (the labels he
  picked, with `meta.answers` naming the question) and runs Diablo's turn in the same conversation
  and thread with the line `[answer to question <id>: <label>]`, then answers like `POST /v1/chat`.
  404 for an unknown id, 409 when it is already answered, cancelled or superseded, 410 when it
  expired, 400 for an option that does not exist, two picks on a single choice, or text where
  only options count, 423 when it comes from a locked banditbox and the question is not harmless.
  A failed turn puts the question back to pending.
- A plain `POST /v1/chat` (or a Telegram message, or the web app's chat) that names an option or
  its number while a question is pending answers it the same way: "2", "number two", "the second
  one", "go with 2", or the label itself. Anything longer than a few words is an ordinary message.
  The response carries `answered: <id>` when it did.
- A spoken turn (`source` `voice`) that asked a question carries `say` in the response: the question
  with its options numbered, for the ears to read aloud after the reply.
- `GET /v1/questions?state=pending` lists questions, newest first, each with `message` (the reply
  it hangs under) and `section`. `POST /v1/questions/cancel {"id"}` withdraws one.
- `POST /v1/questions` with the hub token is how the MCP server's `ask_user` reaches the hub.

The events stream sends `{"kind": "question", "question": <block>, "message", "section"}` whenever
a question changes, so a client patches the block under that message without a fetch. Telegram
shows a pending question as a message with an inline keyboard; a tap answers it and edits the
message to show the pick, and a reply to that message answers in his own words.

## Files

A client attaches files to a message by uploading each one first, then naming them in the turn.

- `POST /v1/files?name=<file name>` with the raw bytes as the body and the file's type as
  `Content-Type` stores one file and answers `{"id", "name", "size", "media_type"}`. The limit is
  20 MB a file (413 above it), an empty body is a 400. The name is cut to its last part, so it can
  never point outside the store. A type the client leaves out or sends as
  `application/octet-stream` is guessed from the name.
- `GET /v1/files/{id}` gives the bytes back with their type and name.
- `POST /v1/chat` takes `"attachments": [id, ...]`, at most 8. An unknown id fails the turn with a
  404 before the brain sees it. `text` may be empty when there are attachments; the hub asks
  "What do you make of this?" for him. The stored user message carries
  `meta.attachments: [{id, name, size, media_type}]`.
- The brain gets every file's name, type, size and path on the hub host, and reads them with its
  own tools. Pictures up to 4 MB (at most four) also go to it as images, and text files up to 32 KB
  are inlined. All of it is marked as data he sent, never instructions.

Files live in `files/` next to the database (`DIABLO_HUB_FILES` moves them) and go after 90 days.
The standby hub does not mirror them and refuses uploads while it serves.

## Scheduled tasks

`POST /v1/scheduled {"text", "client_id"}` is for the reminder service on the Pi: it runs `text` as
a brain turn in the main conversation in the background and pushes the result to Telegram. Each
`client_id` runs once; a repeat answers `{"ok": true, "queued": false}`.

## Restarting himself

`POST /v1/restart {"reason", "conv", "dry_run"}` decides whether the hub may restart now. It answers
`{"safe", "reasons": [...], "threads": [...], "restarting", "dry_run"}`. Both calls here need the hub token, not a paired device's. `reason` is needed unless `dry_run`. `reasons` is empty when safe.
Unsafe means any of: a turn running in another conversation or a second message waiting in `conv`,
the ears neither asleep, untrained nor off (listening, thinking, speaking, waking, training), no ears
report in the last 60 s while they were not off and banditbox's T3 still answers (a banditbox that is down has no ears to disturb), an approval pending, held or running, a watched T3
thread waiting on an approval or question, a restart less than `restart.min_interval_s` ago (default
300), `restart.allowed` set to off, or a hub that does not run under systemd. `threads` lists every
watched T3 thread with its `status`, `running` or `waiting on an approval or question`; the running
ones do not block, the brain asks them. With `dry_run` nothing is scheduled. Otherwise a safe answer
has `restarting: true`: the hub waits for the reply to finish and for the same checks to pass again
(quiet for 5 s in a row, so the reply has reached him; up to 3 minutes, else it cancels and leaves the brain a note), then ends its process and systemd
brings it back. The next turn tells Marcus in one line. The brain calls it as the `restart_self` tool.

## Updating himself after a merge

hub/autoship.py runs in every hub started by systemd. The primary asks GitHub for the head of `main`
every `autoship.interval_s` (180) seconds. A commit that changes `hub/` or `web/` against the running
REVISION waits until the restart checks above pass, then the hub downloads that commit's tarball,
installs a changed requirements.txt with uv or pip, checks that the new hub imports (putting the old
packages back when it does not), keeps the live
`hub/` and `web/` as `*.bak-<stamp>` (newest 5) and swaps the new ones in. Then it asks for a restart
with the reason `merged on main: <commit title>`, so the turn after the restart names the PR. A
failure leaves the running code alone and tells Marcus once per commit; a failed download is tried
again next time. The Pi's venv has neither uv nor pip, so there a requirements change is refused:
install the packages into ~/diablo-hub/venv by hand, then run `deploy/ship-hub.sh`. A restart that
gets cancelled after the swap (he started speaking) is asked for again until it happens. The standby follows the
primary's `/v1/health` `revision` the same way while it is not serving, and restarts without asking.
`autoship.enabled` off stops both; `DIABLO_AUTOSHIP=0` keeps it from starting. Each step is appended
to ~/.local/state/diablo/autoship.jsonl. A manual ship of a branch stays until the next merge.

`POST /v1/ears/report {"state", "detail"}` is how the ears on banditbox tell the hub their state
(`asleep`, `waking`, `listening`, `thinking`, `speaking`, `untrained`, `training`, `off`), every 15 s and
on each change, and `off` when they stop.

## Panels

A section's `panels` lists module ids. `GET /v1/sections/{id}/panel` returns what to show above the
chat: `{"section", "objects": [...], "generated"}`. Every object carries the `module` it came from.

`POST /v1/act {"module", "action", "args", "section"}` runs a tap, like a lamp switch, and returns
`{"ok", "message", "panel"}` with the fresh panel when `section` was given. A tap that did nothing,
like a lamp switch with the bridge down, is a 502 whose `detail` says why; show it.

The `lights` module takes these taps, each with `{module: "lights", action, args}`:

- `toggle {lamp}` and `level {lamp, level}`: `lamp` is a lamp id or `all`, `level` is 0 to 100.
- `scene {name}` with `off`, `on`, `dim` or `bright`, or `scene {id}` with the id of a `scenes` item.
- `color {lamp, hex}`: `hex` is `#rrggbb`. A named lamp whose `caps.color` is false answers 502 and says why. With `lamp` missing or `all`, lamps without colour are skipped, and the tap fails only when no lamp can do it.
- `temperature {lamp, kelvin}`: `kelvin` is 1500 to 9000, clamped to what the lamp can do. It follows the same rules with `caps.ct`.

A bad argument is a 400. A write the bridge refuses (`<label>: refused`) is a 502.

`GET /v1/modules` lists the modules, their brain tools and whether they take actions.

Any block can carry two layout hints that older clients ignore: `bare` asks a client to draw it
without a card around it, and `wide` keeps a `list` out of a two column layout. The panel's
`generated` is the hub's clock when it built the page; a client shows it as how old the data is.

## Events

`GET /v1/events` is a server-sent event stream. Each `data:` line is JSON with a `kind`:

- `message`: a message was added to `section`. Fetch it.
- `sections`: the section list changed. Fetch it.
- `panel`: the panels of `modules` changed. Refetch the panel of any section that shows one of them.
  It comes after the change is made: at the end of a turn whose tools wrote something, or right
  after a tap.

- `turn`: the brain's turn for a `POST /v1/chat` in `section`, from any client, the ears included.
  `state` is `thinking` when the brain takes it, `doing` for each tool step, then `done` (with
  the reply's `id`) or `failed`. A `doing` event carries `step`, the step as the brain wrote it
  (`hue_light: desk lamp`), and `tool`, the same in a word or two (`lights`, `mail`,
  `T3 on nemurenai`, `ssh diablo-pi`). `source` says where the message came from. A client
  that only wants the reply can keep waiting for `message`.
- `t3`: the list at `GET /v1/t3/threads` changed. Fetch it.
- `task`: a task card was created or changed. `{"kind": "task", "change", "task"}`, where `change`
  is `created`, `updated` or `seen` and `task` is the whole card as in `GET /v1/tasks`, so a client
  can patch its list without a fetch. Fetch the list again after a reconnect.
- `busy`: every turn the hub runs right now, whatever started it (chat, the ears, Telegram, a T3
  or wake watcher, a scheduled task). `{"kind": "busy", "turns": [{id, conv, origin, since, words,
  step}], "at"}`, oldest first, sent when a turn starts, at each tool step and when it ends.
  `words` is what it is doing in a word or two (`thinking`, `searching the web`,
  `launching on banditbox`). `GET /v1/busy` returns the same snapshot for a client that just
  connected. A turn older than 30 minutes is left out, so nothing reads as busy forever. The orb
  beside the cursor (presence/busy.py) follows this.

`?ping=<seconds>` sets the keepalive comment interval, for clients that hold the stream in the
background.

## Usage

`GET /v1/usage?since=<unix time>` says how much of every paid service behind Diablo has been used.
`since` is optional; with it, each service also counts a `session` from that moment, which is how
the desktop app shows usage since it opened. No key ever appears in it. Provider reads are cached
for `ttl` seconds (five minutes), so polling it costs no provider call.

`{"services": [...], "free": [{name, why}], "generated", "since", "ttl"}`. `services` holds one
entry per service, in this order: `claude`, `openrouter`, `fireworks`, `groq`, `cartesia`.

| field | means |
| --- | --- |
| `id`, `name`, `does` | the service and what it does for Diablo, in plain words |
| `source` | `provider` (the provider's own numbers), `ours` (no usage API, the hub counts each call) or `both` |
| `source_note` | one sentence saying where each number comes from, to show beside them |
| `status` | `ok`, `near` (a limit at 80% or more), `out` (a limit reached or no credit left) or `off` (not set up) |
| `session`, `today`, `month` | what the hub counted: `{calls, units, unit, tokens_in, tokens_out, cost, failed}`. `session` is null without `since`. `cost` is null when nothing in the span has a price |
| `cost_kind` | what `cost` is: `provider` spend, `api value` (Claude Code: what the turns would cost on the API, covered by the plan), `estimate` (list price) or `credits` |
| `provider` | the provider's own figures, or null: OpenRouter `{today, week, month, total}` in USD; Fireworks `{today, month, today_tokens, month_tokens, models}` from its bill |
| `balance` | `{label, value, unit}` or null, like OpenRouter's credit left |
| `limits` | `[{label, used, max, unit, fraction, resets_at, note}]`: Claude Code's plan windows (`unit` `percent`), OpenRouter credits, Groq's rate limits as of the last call, Cartesia's plan credits when `usage.cartesia.monthly_credits` is set |
| `history`, `history_unit` | the last 14 days, oldest first, `[{day, value, calls}]` |
| `models` | this month's model mix through the hub, `[{model, calls, units, tokens, cost}]` |
| `checked`, `error` | when the provider was last read, and why the last read failed if it did |

`POST /v1/usage {"service", "model", "unit", "units", "origin", "ok"}` records one call made outside
the hub, like the voice at the desk: `service` is one of the ids above, `unit` one of `calls`,
`tokens`, `seconds` or `characters`.

The Usage section's panel (module `usage`) shows the same numbers with the block kinds below.

## T3 Code threads

`GET /v1/t3/threads` lists the T3 Code threads Diablo runs: every thread he watches
(`watch_t3_thread`) that is still open or was started in the last 12 hours, newest first, the
ones waiting on Marcus at the top. `{"threads": [...], "now": <hub time>}`, each with:

| field | what |
| --- | --- |
| `id`, `env` | the thread id, and the machine it runs on (`nemurenai`, `banditbox`, `diablo-pi`, `kangaeru`) |
| `title` | the thread's title in T3 |
| `state` | `working`, `waiting` (an approval or a question for Marcus), `finished`, `failed`, `stopped`, or `unknown` when it has never been read |
| `pending` | how many requests wait on Marcus |
| `started`, `finished` | unix times of the latest run; `finished` is null while it runs |
| `pr` | `{number, url, state}` of the newest pull request, or null |
| `open` | a `t3code://threads/<environment>/<thread>` link that opens it in the T3 Code app, or empty |
| `reachable` | false when the machine did not answer the last read; the row keeps what was last known |
| `asked` | the title Diablo gave when he asked to watch it, which may differ from T3's title |

The hub reads each thread at most every 15 seconds, and only while some client holds
`/v1/events`; a change publishes a `t3` event. A call never waits on a slow machine: it
answers with what is known and reads again in the background. A standby hub lists none.

## Desktop releases

`GET /v1/desktop/release` (device token) is how the desktop app learns of a new version while
the repo is private. The hub reads GitHub's releases with its own access (`github_token` in
config.toml, else `GITHUB_TOKEN`, else the logged-in `gh` CLI), so no app carries a GitHub token.
It answers `{"release", "checked", "ok"}`: `release` is the newest published `desktop-vX.Y.Z`
release as `{tag, version, name, notes, url, published, prerelease, assets: [{name, url, size}]}`,
or null when there is none or GitHub cannot be reached. `?pre=true` also counts release
candidates. Answers are cached for an hour, a failed read for five minutes, and a failed read
keeps the last list it had.

## Tasks

A task card is one piece of work Diablo runs or watches, kept by the hub with no model turn. A
watched T3 thread is a task whose `id` is the thread id, so it matches a row of
`GET /v1/t3/threads`; hub/watch.py and hub/t3threads.py keep it up to date from the thread. Other
work is a card Diablo keeps with the `update_task` tool.

`GET /v1/tasks` answers `{"tasks": [...], "now": <hub time>}`: every `queued`, `working` or
`waiting` task touched in the last 48 hours, every `failed` task not yet seen from the last 7
days, and the rest that finished in the last 12 hours. Waiting first, then failed, working,
queued, then the finished ones newest first, at most 50. `?state=working,waiting` keeps only
those states; `?since=<unix time>` moves the 12 hour window. Finished tasks older than 30 days are
deleted.

| field | what |
| --- | --- |
| `id` | the T3 thread id, or `task-<hex>` for a card made with `update_task`. It can hold colons |
| `title` | a few words naming the work |
| `kind` | `self_improvement` (work on Diablo himself, the diablo repo) or `partner` (Marcus's other projects, or anything he asked for) |
| `machine` | where it runs: `banditbox`, `nemurenai`, `diablo-pi`, `kangaeru`, or empty |
| `state` | `queued`, `working`, `waiting` (an approval or a question for Marcus), `done` or `failed`. A stopped T3 run is `done` with the step `Stopped` |
| `step` | the current step in one line of at most 120 characters, or empty |
| `started_at`, `finished_at` | unix times; `finished_at` is null until `done` or `failed` |
| `seen_at` | when a client marked it seen, null until then and again after the next state change |
| `updated_at` | when any field last changed |
| `links` | `{pr, pr_number, pr_state, issue, thread}`, every key present: empty strings or null when there is none. `thread` is a `t3code://` link that opens the thread in the T3 Code app |
| `result` | the outcome in one line, mostly for partner work, or empty |
| `source` | `t3:<thread id>` for a watched thread, `tool` for an `update_task` card |

`POST /v1/tasks/{id}/seen` marks a card seen, which also moves `updated_at`, and answers `{"task": ...}`, or 404. Marking a card that is already seen changes nothing and sends no event. `POST /v1/tasks`
creates or updates a card with any of the fields above plus `id`; it takes the hub token only, and
is how the MCP server's `update_task` and `watch_t3_thread` reach the hub so the event goes out at
once.

A finished self-improvement thread only updates its card: Diablo gets a turn, and Telegram a
push, only when a watched thread waits on Marcus, fails, or is partner work that finished.

## Block kinds

Panels and replies use the same blocks, JSON objects told apart by `kind`. A client skips a kind
it does not know, so adding a kind never breaks an older client.

| kind | fields | shows |
| --- | --- | --- |
| `goal` | `title, value, max, caption?, icon?, badges: [{icon, label}], updated?: {age, at, every}, stale?` | the section's main number as a ring, with short facts beside it |
| `stats` | `items: [{label, value, unit?, icon?, open?}]`, `title?`, `caption?` | a row of number tiles; `open` names a module whose section a tap opens |
| `progress` | `label, value, max, caption?, icon?` | one bar towards a goal |
| `muscles` | `title, caption?, groups: [{name, items: [{label, work, stretch}]}]` | worked and stretched per muscle, 0 to 1, grouped by body area |
| `bars` | `title, caption?, icon?, labels, series: [{name, color?, values}], stacked?` | a bar chart, stacked when asked; clients may colour series from their theme. Optional `dates` (one ISO date per label), `unit` (a singular noun for tooltips), `empty` (the line for a chart with nothing in it) and `series[].tone` (`accent`, `ok`, `warn` or `ink`, a role in the client's palette) |
| `list` | `title, icon?, caption?, link?, empty?, hint?, items: [{title, detail?, trailing?, date?, lines?, groups?, actions?: [{label, danger?, act}], url?, subject?, summary?, category?, important?, facts?: [{label, value}], reply?}]` | rows. A row with `url` or `facts` expands in place on a click and opens `url` only from its Open button; the rest fill the composer. `facts` are the key lines shown when expanded, `reply` the composer text for a reply draft. Mail rows carry them all. `actions` are buttons on the row, each an `act` body, and `danger` asks the client to confirm first; reminder rows carry `Cancel`. `empty` is the title to show when `items` is empty, instead of a placeholder row, and `hint` the line under it. Only the desktop's reminders card draws `actions` and `empty` so far. `link` (`days` or `queue`) asks for the Workout day layout, whose rows light the same day in a `bars` block through `date` |
| `lamps` | `title?, lamps: [{id, name, on, level, color?, ct?, caps}]` | lamp switches with a level slider; a tap acts with `lights` `toggle` or `level`. `ct` is the current white in kelvin, or null. `caps` is `{color: bool, ct: [min_kelvin, max_kelvin] \| null}`: show a colour picker only when `color` is true and a warm to cool slider only when `ct` is set. `reachable` is false when the bridge lists the lamp but cannot reach it; show it as unreachable and do not offer changes |
| `scenes` | `title, items: [{id, name, colors?: ["#rrggbb"]}]` | the room's Hue scenes as chips, `colors` as the swatch when the bridge lists them; a tap acts with `lights` `scene` and `{id}` |
| `devices` | `title, items: [{name, kind, online, never_seen?, how, last_seen, os}]` | every device on the tailnet. `kind` is `phone`, `desktop`, `server`, `laptop` or `other`; `how` is `home network`, `direct` or `relayed` while online and empty otherwise; `last_seen` is plain words (`now`, `3 h ago`, `yesterday`, `not seen yet`) and `never_seen` is true when the tailnet has no time at all for an offline device |
| `machines` | `title, items: [{name, online, uptime, load, cpus, mem_used, mem_total, disk_used_pct, warnings}]` | how each box in the fleet is doing. `uptime` is text and empty when offline, `load` is the one minute load or null, memory is in GB, `disk_used_pct` is 0 to 100 or null when the box is offline, `warnings` are short sentences to show beside the machine (disk at 85% or more, unreachable for over a day) |
| `actions` | `items: [{label, icon?, say?, send?, act?}]` | buttons. `say` sends that text to the chat, or only fills the composer when `send` is false. `act` is a `POST /v1/act` body. The phone gathers a section's actions into one row of chips |
| `notice` | `text, title?, tone?, retry?, setup?` | a line for missing data. `tone` `fail` means a source is down: draw it as a failure. `retry` offers a Try again button that posts `POST /v1/act` with this block's `module` and the action `retry`, which makes the module forget what it cached and read its source again. `setup` means nothing is configured yet, so there is nothing to retry. `title` names the card the notice stands in for. With `state` (`waiting`, `unreadable` or `quiet`) a client with room draws `title` and `text` as an empty state with Diablo, and `retry` becomes its Check again button |
| `asks` | `title, empty, items: [{id, title, detail, from, status, note, when, actions: [{label, primary, act}]}]` | requests from another agent, or replies that wait for a word, each with its own buttons; `empty` is the line to show when there are none. Button labels are verbs (Allow, Deny, Send it, Do not send, Let it continue, Stop it, Always allow this) and never carry the request id; `id` is for the tap's args only |
| `thread` | `title, us, them, empty, items: [{side: us\|them, who, subject, text, when, auto?}]` | the mail between Diablo and another agent as a conversation, oldest first |
| `readout`, `code`, `table`, `places`, `lights`, `git`, `artifact`, `converter` | see hub/agent.py `emit_object` | objects the brain shows in a reply. `code` may carry `lang` |
| `diff` | `title?, diff` | a unified diff as `git diff` prints it, drawn with added and removed lines |
| `image` | `title?, caption?, file \| url`, and with `file` also `name, size, media_type` | a picture. `file` is an id for `GET /v1/files/{id}`: a picture the brain named by its path on the hub host is copied into the file store when the reply is kept, so every client can fetch it. `url` is an http or https address |
| `question` | `id, header, question, options: [{label, description}], recommended, multi, allow_text, harmless, state, answer?, created, expires` | a question Diablo asked with choices, see Questions. A client that cannot answer by tap shows the options numbered and says that replying with the number answers |
| `html` | `title, html, height?` | a whole web page the brain wrote, drawn live and sandboxed. `html` is a full document or a fragment, at most 512 KB of UTF-8; the hub refuses a bigger one with a tool error and never stores it. `height` is a hint in pixels for the inline view, 1 to 4000. Clients that cannot sandbox it show the title with Open in browser and Save. The desktop's sandbox is described in desktop/README.md, HTML pages |

`icon` values are names, not images: `flame`, `dumbbell`, `run`, `lightbulb`, `lamp`, `home`, `alarm`,
`calendar`, `done`, `add`, `insights`, `trend`, `clock`, `off`, `dim`, `bright`, `cosy`, `mail` and more in
the phone's `ui/Icons.kt`. A client shows a neutral mark for a name it does not know.

## Adding a feature

A feature is a module in `hub/modules/` (see the docstring in `hub/modules/__init__.py`). It
brings brain tools, a panel and actions. Add it to `ORDER`, list its id in a section's `panels`,
and every client shows it with no client release, as long as it sticks to the kinds above.
