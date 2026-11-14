# Diablo on the screen

An orb beside the cursor shows what he is doing while Marcus talks to him or a turn of his runs,
and cards show what he answers. Asleep he is gone and the cursor is not asked at all. Waking
brings him up beside the cursor, and 25 seconds after Marcus last talked to him or his last turn
ended (`lingerMs`) he fades out again; until then he follows the cursor with his sleepy face.
With following off he sits in the corner and goes as soon as he is done. It is its own
Quickshell instance (`presence/`, run by `diablo-presence.service`), so a crash here never
touches banditshell, and Quickshell reloads the QML when a file changes, so Diablo can change
his own look by editing those files.

Install once: `cp deploy/diablo-presence.service ~/.config/systemd/user/ && systemctl --user enable --now diablo-presence`.

## He steps aside for what Marcus watches

Once the pointer has rested for 2.5 seconds (`restMs`, about when a media player hides it) he
gets out of the way: over a fullscreen window he fades out, and over anything else he drifts to
the bottom right corner. The pointer moving brings him straight back beside it. He never steps
aside while Marcus needs him: listening, thinking or speaking to him, a card or the central card
up, or a passing moment, so a wake word or a reply arriving brings him back as well. Fullscreen
is the cursor's monitor's workspace as Quickshell's Hyprland module reports it from Hyprland's
event socket, so it costs no polling; the rest comes from the cursor ticks he already makes,
slowed to ten a second while he is aside (`Place.restSpot`).

## Cards grow out of him

A card is never placed at the cursor. It opens next to the orb, below it or above it when the
screen runs out, lined up with his side, and scales up from the point nearest him
(`presence/place.js`, `cardRect`). While a card is up the orb is up too, holding it out with his
proud face, and he follows the cursor exactly as he does alone, the card swinging after him on a
softer glide. The two hold still only while the pointer is on the card or closing in on it
(`reaching`: its last 120 ms of travel points at the card, give or take 12 degrees, from within
360 px, slower than 900 px/s), so a card can be read and clicked. A quicker stroke, or one
heading anywhere else, lets them follow. A page in a card is moved with it every frame while it glides.

`node --test tests/presence/place.test.mjs` checks the placement; CI runs it.
`scripts/presence-shots.sh` draws every scene into `captures/presence/`: by default offscreen
(`presence/preview/render.py` runs the real components with Qt, no compositor, no window, so it
works with the screen locked; CI runs it too and keeps the PNGs as an artifact). `--live` shoots
on the real compositor instead: a throwaway Quickshell instance with its own state dir, a fixed
cursor (`DIABLO_PRESENCE_CURSOR`, "x,y") and no hub feed (`DIABLO_PRESENCE_TEST`), so it never
moves the pointer or reads anything real. `DIABLO_PRESENCE_CURSOR` may also be the path of a file
holding "x,y", read on every tick while he is up, so a script can move a pretend cursor to record the
orb following it. The components stay free of Quickshell imports so the offscreen
preview can load them; what needs Quickshell lives in `shell.qml` or a file loaded on demand
(`CardActions.qml`).

## The central card

Asked to, he opens up: the orb glides to the middle of the monitor and a big card grows out of
him (`Panel.qml`, sized by `Place.panelRect`). It no longer follows the cursor. Escape, the
close button or a click anywhere outside puts him back. Three pages:

- **Now**: a card shown big. "Enlarge this" moves the newest card here.
- **History**: everything Marcus said to him on every channel, marked spoken or typed, and every
  reply, newest at the bottom (`History.qml`). It reads `GET /v1/history` with the `hub_url` and
  `hub_token` that `busy.py` reads from `config.toml` and prints to the shell; scrolling up loads
  the page above, new lines come in every 4 s while it is open (`after`, so a quiet poll is empty),
  a page that failed is asked for again, and a search field and one chip per channel narrow it.
  The wheel and the keys scroll through `WheelStep.qml`, 100 px a notch, the touchpad 1:1.
- **Settings**: whether he follows the cursor (kept in `presence-settings.json`), whether the
  ears listen for his name, live meters for the microphone and the wake score, how well he knows
  your voice, and the voice setup itself, driven through the ears' control panel on
  `127.0.0.1:8758` (`voice/ears_api.py`).

The spoken quick actions (`voice/panel.py`, no model turn): "open up", "expand" open it;
"go to your settings", "open your settings", "take me to your settings" open it on Settings;
"train your voice", "teach you my voice", "retrain your ears" open it on Settings at Your voice;
"show my history", "open the history" open it on History;
"enlarge this", "make it bigger" enlarge the newest card; "close", "shrink", "make it small
again" close it. "Close it" and "make it bigger" also mean a window, so they only count while the
central card is open or a card is on screen. The state is `panel.json` (`{open, view, card,
page, page_at, at}`) in the same state dir; the ears, `busy.py` and `shell.qml` all write it.
`page` asks Settings to glide to the top (`settings`) or to Your voice (`voice`), once for each
`page_at` stamp, so asking again moves it again; scrolling by hand stops it at once.

Typed, or on any channel the quick actions do not catch, the brain does the same with the
`open_settings` tool (`page` settings, voice or history). It posts to `POST /v1/screen/open`, which goes out
as a `screen` event with `open`, and `busy.py` writes it into `panel.json`, the same road
`show_on_screen` takes.

## What it reads

| File | Written by | Holds |
|---|---|---|
| `~/.local/state/diablo/ears.json` | the ears (`set_state`) | `state` plus the modes that outlast it: `loop`, `dictation`, `thinking` |
| `~/.local/state/diablo/presence.json` | `voice/presence.py` | `{"cards": [{id, kind, title, big, lines, at, ttl}]}`, the newest three |
| `~/.local/state/diablo/mouth.json` | `voice/mouth.py` | `{start, duration, cues: [[seconds, shape]]}` for the clip playing now, `start` on the wall clock |

## His face

The orb is his hand-drawn face (`assets/faces`, four frames a mood, swapped every 250 ms so the
line boils, every 700 ms while he sleeps) on a small dark disc, tinted by what he is doing.

| Face, colour | Means |
|---|---|
| asleep, grey | waiting for "Diablo" |
| listening, blue, breathing | listening |
| expectant, violet, with a ring | speech control is on |
| listening, brass, with a ring | dictating |
| thinking, orange | thinking |
| talking, green, pulsing | speaking |
| working, moons circling | one moon per thing he works on in the background, up to 8 |
| delighted / sorry / relieved, for under two seconds | it worked / it failed / he went back |
| worried or resting, pink | the ears are untrained or off |

Only his own running hub turns make him busy (`presence/busy.js`, tested in node by
`tests/presence/busy.test.mjs`). The moons count what he has in flight while one runs: each
turn, and each live task card (`GET /v1/tasks` and the `task` event, which `busy.py` follows
beside the turns), a task waiting on Marcus in blue. A task card on its own never raises him,
never gives him the busy face and never puts its title in his pill: a watched T3 thread is
not him, and its step belongs on its card.
They sit evenly around one orbit and turn together; one that ends fades where it was and the
rest glide to even spacing, all in QML (`Orb.moons`).

He acts in every awake state, not only while a measured line plays: the face is drawn from
the horns, his eyes (`assets/faces/eyes/<look>`) and his mouth, with blinks every few seconds and
small glances (`Orb.acts`, one entry per face: the eyes and mouth it rests on, the glances it
makes). Listening has wide eyes, thinking a thinking look that glances side to side, holding out
a card he looks at it, and the moments (delighted, sorry, relieved, surprised, confused) get
their own eyes and mouth. Speech with no measured track still moves the mouth, a shape every
tenth of a second. Asleep, resting and the wink keep their whole hand-drawn mood.

While he speaks, the talking face is assembled from three of Marcus's drawings: the horns
(`assets/faces/horns`), the eyes and brows for the clause he is saying (`assets/faces/eyes/<look>`)
and the mouth for the sound he is saying that instant (`assets/faces/mouths/<shape>/1-3.png`).
The voice measures where every sound and word starts in the clip itself (`voice/mouth.py`, a
phoneme aligner on the GPU, about 30 ms a sentence, run while the previous sentence plays).
Jev picks a look for every clause of the sentence in one call, beside the speech fetch
(`voice/eyes.py`), and each look starts on its clause's first word. The orb plays the track
against the wall clock, blinking every 2 to 6 seconds and half the time when the look changes.
Every line he speaks is fetched together with its track (`Speaker.fetch_face`), so a reply uses
all eight drawings. Only clips with no text, or no aligner, move the mouth with the loudness
instead, which knows three of them: rest, mid and open.

The clock is the playback's own. His voice plays in the ears' process on one PipeWire stream
(`voice/playback.py`), whose callback knows when each buffer's first sample reaches the
speakers, device latency included. The track's `start` is the moment the first sample is heard,
the talking face comes on at that moment and goes off when the last sample (or a cut) has been
heard, so it rests in the gap between his first line and the answer that follows the work.
The stream opens when a reply starts fetching, so a sleeping sink wakes during the fetch, and
closes a few seconds after the last clip. If it cannot open, the clip plays through pw-play and
`mouth_latency_ms` (default 60) stands in for the start delay.

The orb steps the track once per drawn frame (`FrameAnimation`), and moves it by the delay saved
in `~/.config/diablo/lipsync.json` (`{"delay_ms": n}`, positive is later, kept per box): what is
left after the playback clock (the screen, the compositor, what reads as in step) is tuned by eye.
`diablo-ears mouth` opens the tuner (`presence/tuner.qml`), as does "Tune his mouth" in Settings:
his face, big, saying one line over and over on the same voice stream, a slider, left and right
for 10 ms, Shift for 50, Enter saves, Esc leaves. `diablo-ears mouth show` and `set MS` read and
write the same file. The reference for "in step" is `diablo-act` on the same audio: a screen
recording of the orb should match its video. On the desktop box the orb lands about 27 ms behind it
with no delay.

`.venv-voice/bin/python tests/live_tts_sync.py` checks it live: it speaks four short replies
(one line, a first line and an answer 2.5 s later, one cut off) while recording the default
sink's monitor, and fails when the face or the mouth is more than 50 ms off the sound.

The moments come from `voice/feedback.py`, the same calls that play the ok, low and back
sounds. Hovering the face says the state in words.

## Cards

A card stays its `ttl` (10 s, a spoken reply as long as it takes to read), longer while the
pointer is on it, and a click puts it away. Quick answers (`voice/quick.py`) make one with no
model: the time (local), the weather at home (Open-Meteo, about 80 ms) and the next bus
(Rejseplanen, about 180 ms). Home and the stop come from `~/.config/diablo/config.toml`
(`home_name`, `home_lat`, `home_lon`, `bus_stop_id`, `bus_stop_name`, `bus_line`, `bus_toward`);
the hub settings `bus.stop_id`, `bus.line` and `bus.toward` override the stop. Without them
those questions go to the hub. Every spoken reply is shown too.

Cost, measured on the desktop box: a second Quickshell instance idles at about 95 MB and 0% CPU.

## A take, acted out

"Diablo, animate me" (or "Diablo act", "act this out") records Marcus: "Sir, wait for the beep",
a beep, and every piece he says is kept until a pause and "I'm done", "dictation done" or
"recording finished". A second beep, he says the last word heard, and the end phrase is cut
from the audio where the aligner finds it. The take renders on a thread (`voice/act.py`, the same
as `diablo-act`) into `~/Videos/diablo/`, and a card beside the cursor plays it once with its
sound. Click the video to play it again; **Copy** puts the file on the clipboard as a URI list,
so a chat pastes it as a video; **Save** asks where to put a copy; **Open** opens it.


## Showing things on request

The brain shows things here from any channel with the `show_on_screen` tool
(`hub/mcp_server.py`): a title, a big figure, lines, or a whole HTML page, small or large. It
posts to `POST /v1/screen` (hub token only), which goes out as a `screen` event. `busy.py`, the
same process that follows the hub's turns, writes a small one into `presence.json` beside the
ears' own cards and a large one into `panel.json`, which opens the central card on Now. An HTML
page is saved under `pages/` in the state dir, the newest 20 kept, and the card carries its path.
"Enlarge this" then moves a small one into the central card like any other.

## HTML pages in a card

A card or the central card can hold a whole HTML page (`page` on the card, a file under
`pages/`). Quickshell cannot host Qt WebEngine (it crashes on start), so the page lives in its
own small GTK process, `webcard.py`: a layer-shell window with WebKit in it, started with the
first page and kept. `shell.qml` keeps a spot free for the page (`Card.pageSlot`, 190 px under
the title; `Panel.pageArea`, below the text in Now) and, once the card has finished growing,
writes one JSON line to the web view's stdin with the page and that spot in the monitor's
coordinates. It moves with the orb and hides when the card goes.

Pages load as a string with no base URL, so they reach no local file; scripts, WebGL and https
resources work, links open in the browser, and new windows are refused. Two things keep the
layer surface from ever mapping on Hyprland, so the view avoids them: a GTK CSS provider and a
fade in from opacity 0. `webcard.py --snapshot PAGE OUT.png` renders a page once, to check it.
