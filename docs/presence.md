# Diablo on the screen

An orb beside the cursor shows what he is doing while Marcus talks to him, and cards show what he
answers. It is its own Quickshell instance (`presence/`, run by `diablo-presence.service`), so a
crash here never touches banditshell, and Quickshell reloads the QML when a file changes, so
Diablo can change his own look by editing those files.

Install once: `cp deploy/diablo-presence.service ~/.config/systemd/user/ && systemctl --user enable --now diablo-presence`.

## Cards grow out of him

A card is never placed at the cursor. It opens next to the orb, below it or above it when the
screen runs out, lined up with his side, and scales up from the point nearest him
(`presence/place.js`, `cardRect`). While a card is up the orb is up too, holding it out with his
proud face, and the two hold still while the cursor is within reach of them, so a card can be
read and clicked. Once the cursor is well clear (`shouldRejoin`, 280 px) they glide after it.

`node --test tests/presence/` checks the placement; CI runs it. `scripts/presence-shots.sh`
shoots the real thing against fake state into `captures/presence/`: a throwaway Quickshell
instance with its own state dir, a fixed cursor (`DIABLO_PRESENCE_CURSOR`) and no hub feed
(`DIABLO_PRESENCE_TEST`), so it never moves the pointer or reads anything real.

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
| working, one slow moon circling | an answer is thinking in the background |
| delighted / sorry / relieved, for under two seconds | it worked / it failed / he went back |
| worried or resting, pink | the ears are untrained or off |

While he speaks, the talking face is assembled from three of Marcus's drawings: the horns
(`assets/faces/horns`), the eyes and brows for the clause he is saying (`assets/faces/eyes/<look>`)
and the mouth for the sound he is saying that instant (`assets/faces/mouths/<shape>/1-3.png`).
The voice measures where every sound and word starts in the clip itself (`voice/mouth.py`, a
phoneme aligner on the GPU, about 30 ms a sentence, run while the previous sentence plays).
Jev picks a look for every clause of the sentence in one call, beside the speech fetch
(`voice/eyes.py`), and each look starts on its clause's first word. The track is written when
the player starts and the orb plays it against the wall clock, blinking every 2 to 6 seconds
and half the time when the look changes. Clips with no text, or no aligner, move the mouth
with the loudness instead. `mouth_latency_ms` (default 60) is the player's start
delay, taken off so the mouth opens with the sound.

The moments come from `voice/feedback.py`, the same calls that play the ok, low and back
sounds. Hovering the face says the state in words.

## Cards

A card opens beside the cursor on the cursor's monitor: 26 px above and to the right of it, to the
left when the right side runs out of room, below it when there is no room above, and always inside
the screen. While the orb is up the card sits on the far side of it, so the two never overlap. The
spot is read once from Hyprland's socket when the first card goes up and kept until the last one
goes away, so a card does not move while it is read. If Hyprland does not answer within 400 ms the
card shows in the bottom right corner, as it used to.

A card stays its `ttl` (10 s, a spoken reply as long as it takes to read), longer while the
pointer is on it, and a click puts it away. Quick answers (`voice/quick.py`) make one with no
model: the time (local), the weather at home (Open-Meteo, about 80 ms) and the next bus
(Rejseplanen, about 180 ms). Home and the stop come from `~/.config/diablo/config.toml`
(`home_name`, `home_lat`, `home_lon`, `bus_stop_id`, `bus_stop_name`, `bus_line`, `bus_toward`);
the hub settings `bus.stop_id`, `bus.line` and `bus.toward` override the stop. Without them
those questions go to the hub. Every spoken reply is shown too.

Cost, measured on banditbox: a second Quickshell instance idles at about 95 MB and 0% CPU.

## A take, acted out

"Diablo, animate me" (or "Diablo act", "act this out") records Marcus: "Sir, wait for the beep",
a beep, and every piece he says is kept until a pause and "I'm done", "dictation done" or
"recording finished". A second beep, he says the last word heard, and the end phrase is cut
from the audio where the aligner finds it. The take renders on a thread (`voice/act.py`, the same
as `diablo-act`) into `~/Videos/diablo/`, and a card beside the cursor plays it once with its
sound. Click the video to play it again; **Copy** puts the file on the clipboard as a URI list,
so a chat pastes it as a video; **Save** asks where to put a copy; **Open** opens it.

