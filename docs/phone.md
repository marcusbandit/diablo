# The phone app

The Diablo app for Android, in `phone/`. It replaces the old fleet app.

## Goals

- One Diablo, many sections. "Diablo" is the whole of him. A section such as Workout or
  Projects is his own thread and memory set for one subject, and he can still reach
  everything else from inside it.
- Sections are data, not code. Adding, removing, renaming, sorting and recolouring a section
  is a settings change, synced through the hub, with no new build.
- Native rendering of what the agents send: Markdown, HTML, SVG (animated too), GIF and
  animated WebP, and Diablo's own objects.
- It works with what is reachable. Hosts are found over Tailscale. With no host up, the phone
  still shows every section and its history from its own store, and queues new messages.
- Kotlin, Compose and Rust. No cross-platform UI toolkit, no web app in a wrapper.

## Layers

```
phone/app       Kotlin + Jetpack Compose: screens, rendering, networking, onboarding
phone/core      Rust (uniffi): markdown parser, local store, host choice, sync merge
hub/            the hub on diablo-pi: sections API and the section conversations
```

Rust owns the logic that must be right and fast and has no UI: parsing Markdown into blocks,
the SQLite store, which host to use, and how hub and phone data merge. Kotlin owns everything
the platform does better: Compose, OkHttp, the image decoders, the WebView sandbox and the
permissions. The bridge is uniffi with Kotlin bindings generated at build time, loaded through
JNA from `libdiablo_phone.so`.

## Rendering

Every reply is Markdown. The core turns it into a list of blocks once, off the main thread, and
Compose draws the blocks:

| Content | How it is drawn |
|---|---|
| Headings, paragraphs, lists, quotes, tables, task lists, rules | Compose, from core blocks |
| Inline bold, italic, code, links, strikethrough | one AnnotatedString per paragraph |
| Code blocks | monospace, horizontal scroll, copy |
| Inline HTML (`<b>`, `<br>`, `<sub>` ...) | `AnnotatedString.fromHtml` |
| HTML blocks and documents | sandboxed WebView: no JavaScript, no network, no file access |
| SVG, static | Coil with the SVG decoder, native |
| SVG with SMIL or CSS animation | the same sandboxed WebView, since Android has no native SMIL |
| GIF, animated WebP, PNG, JPEG | Coil, `AnimatedImageDecoder` on API 28 and up |
| Diablo objects (readout, table, code, lights ...) | a renderer registry keyed by kind |

The registry is the extension point: a new object kind is one composable and one line in the
registry. Unknown kinds fall back to showing their Markdown or JSON.

## Sections

A section has an id, a name, an icon, an accent colour, a brief, a position and a hidden flag.
The brief is the section's standing instruction to Diablo ("track sets and reps, suggest the
next session"). The hub keeps the list in its settings and is the source of truth when it is
reachable; the phone keeps a copy and edits made offline are pushed when a host is back.
The newest edit wins.

On the hub each section is its own conversation, `sec-<id>`, with the section's brief and its
tagged facts at the top of each turn. The "diablo" section is the main conversation, the same
one Telegram uses. It cannot be removed.

## Hosts

The app keeps an ordered list of hosts, each a Tailscale address with the hub port:
diablo-pi first (`DIABLO_HUB_URL`, default `http://diablo-pi:8755`), then the standby on nemurenai (`DIABLO_STANDBY_URL`, default `http://nemurenai:8755`), baked in at build time from local.env.
It probes `/v1/health`, uses the first that answers, and remembers how fast each one was.
The standby answers health only while the Pi is down (docs/standby.md), so the phone moves
to it and back on its own. A send refused with 503 stays queued. The phone's token
is kept in the Android keystore-backed encrypted store, never in the SQLite file.

## Pairing

The phone never holds hub_token. It pairs: Diablo makes a one-time code (Telegram /pair, the
brain's pair_device tool, or Devices on the hub's web page), the phone sends it to
`POST /v1/pair` and gets back a long random token of its own. The code lasts ten minutes and
works once. A device is revoked on its own with `/devices revoke <n>` on Telegram, Devices on
the web page, or Unpair in the app's settings.

The code reaches the phone three ways: typed (`ABCD-EFGH`), scanned with the Scan QR code
button (Google's code scanner, no camera permission), or tapped as a link. The QR code holds
the hub's `/app/pair.html?code=` page; that page has an Open in the Diablo app button for
`diablo://pair?host=..&code=..`, which the app handles. Pairing again revokes the phone's
previous device.

The standby mirrors the Pi's database, devices included, so a paired phone's token works there
too. Pairing and revoking happen on the Pi only; the standby answers them with 503.

## Offline

The phone store holds sections, messages and an outbox. Opening a section always reads the
store first and draws instantly; the network only adds to it. A message written while no host
answers goes to the outbox and is sent in order when one does.

## Notifications

When the app is in the background, a new reply from Diablo in any section becomes an Android
notification. There is no Google push: the phone holds the hub's own event stream over the
tailnet.

- `notify/Listener.kt` is a foreground service (type `remoteMessaging`) that keeps
  `/v1/events?ping=240` open. The hub sends a keepalive every 240 seconds on that stream instead
  of every 15, so the socket is silent between events and the radio can sleep. A foreground
  service keeps network access in Doze; each event is handled under a 30 second wake lock.
- On every connect it catches up on each section, so a reply that landed while the stream was
  down still notifies. A per-section `seen:<id>` cursor in the store decides what is new, so it
  does not matter whether the screens or the listener pulled the message first.
- Only `assistant` messages notify. His own messages, from the phone or Telegram, never do, and
  neither does the section he is looking at while the app is resumed.
- Each section is its own channel (`section-<id>`), named after it and lit in its accent, and its
  unread replies stack into one notification that opens the section. Markdown is flattened to
  plain text by `notify/Plain.kt`; tables, code, pictures and drawings become a short summary.
- The quiet "Listening for Diablo" notice is the one Android requires for a foreground service.
  It sits in its own minimum-importance channel that can be switched off.
- The listener comes back after a reboot or an app update (`notify/Boot`). It runs only while the
  `notify` setting is on and Android allows notifications. Onboarding asks once, after showing
  what a notification looks like; settings holds the same switch.
