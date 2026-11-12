# The ears

Say "Diablo", hear a rising chime, talk, stop, hear a falling chime, and he answers out loud.
It runs at login on banditbox as `diablo-ears.service`, with no window.

## A turn

1. The wake word model (`voice/wake.py`) reads the raw microphone, 80 ms at a time. It
   wakes on two blocks over the threshold in a row, before any speech to text runs, so a
   word that only transcribes as "diablo" cannot wake him.
2. A resemblyzer voiceprint of the last 1.6 s is compared with the average of Marcus's own
   wake recordings. Another voice is refused without a sound. The request he speaks after
   the chime gets a second check against a print from his dictations (see below).
3. The listening chime (A5 then E6) plays. Recording starts as it fades and stops when he
   has finished (see "Letting him breathe" below), 90 s at the most. Nothing said within
   6 s plays one low note and he goes back to sleep.
4. The done chime (E6 then A5) plays. The audio goes to gvoice for text, then to the hub's
   `POST /v1/chat` with source `voice`: the same brain and conversation as the desktop app,
   so the exchange shows up there too.
5. He speaks the reply without code or links, and stops near 700 characters with "the rest
   is in the chat". Then he sleeps until the next "Diablo". One wake gives one turn.

`~/.local/state/diablo/ears.json` holds the state: asleep, waking, listening, thinking,
speaking, untrained, training or off. The service also posts it to the hub's
`POST /v1/ears/report` every 15 s and on each change, and `off` when it stops. The hub refuses to
restart itself while the ears are anything but asleep, untrained or off, or have gone quiet.

## Plain commands, no model

The ears send the open windows and media players with every line. Jev, which reads every line
anyway, picks a plain desktop command when there is one: "close this", "close Discord", "switch to
Spotify", "workspace 3", "move this to workspace 4", "pause the music", "play Spotify", "next song",
"louder", "mute". The hub answers with an `act` and no model runs; `voice/commands.py` does it
here with hyprctl, playerctl and wpctl. A command that worked says nothing, since the done chime
already played. One that failed says why. A direct light change is quiet too. `ears.speak_direct`
set to 1 makes both speak again. Measured on 24 lines against the live Jev: about 0.25 to 0.3 s a
decision, with no wrong action. Unsure lines ("show me T3 Code", "close it") went to Claude.

A line over 12 words goes to Claude, since it is usually more than one thing, except music and
sound with Jev at 0.8 or more: "play anything on Spotify, just press play" is still play. Jev is told the line came from
Whisper, often over music, and whether each player is playing or paused (a player Diablo's own
hold paused counts as playing). Whisper over a song wrote "play Spotify" as "Chase, Spotify.";
with that note Jev reads it as play at 0.71 instead of a window switch at 0.45.

## Speech control

"Diablo, speech control" (also "accessibility mode", "voice control", "command mode", "voice loop")
keeps him listening without the name until "stop speech control", "that's all", or
`ears.loop_idle_s` (300 s) with nothing said. Each line has to pass the voice check against his
own talk, so a song or a video does not count; a line runs as long as he talks, up to `ears.turn_max_s` (90 s) counted from his first word, and
a line said while gvoice dictates is ignored. Sound is not held, so music keeps playing.

A line goes, in order, to:

1. The ears themselves: "type <words>" types the words, and "dictate" starts dictation.
2. `POST /v1/voice/control`: Jev alone, on the desktop questions only, so the input stays small,
   and nothing lands in the Diablo chat. Windows (close, switch, left, right, up, down, fullscreen,
   new window, terminal), workspaces, keys (enter, escape, tab, backspace, delete word, copy, paste,
   cut, undo, redo, select all, save, find), tabs (new, close, next, previous, reopen, reload),
   scrolling, page up and down, top and bottom, clicks at the pointer, media and volume. Window
   moves press his own Hyprland binds (Super and an arrow), so they follow the layout that is on.
   Closing a window or a tab and pressing enter need Jev at 0.8; the rest act at 0.6. Measured on
   34 lines: 32 right, about 250 ms each.
3. The whole turn, Claude included, when Jev finds no plain command.

"Diablo, type ..." and "Diablo, dictate" also work from a normal turn.

### What he plays

Sound is the feedback; nothing goes to the notification centre (he asked for that, 2026-11-11),
and anything to look at will live in banditshell later. Diablo only speaks when something failed.

| Sound | Means |
|---|---|
| three notes rising / falling | speech control on / off |
| ok: two quick notes rising | an act he asked for was done |
| back: the same two falling | undo, go back, reopen, never mind |
| low single note | it did not work; he then says why |
| soft D5 to A5 | dictation is listening |
| very quiet tap | a dictated piece landed, keep going |
| A5, D5, D6 | the dictation was typed in |
| soft low lift | the answer went to the background, talk again if you like |

The listening and heard chimes stay as before; a turn Jev ended early on a plain command plays
only its ok, not the heard chime as well. `diablo-ears chimes` plays them all.

"Reopen it" brings back the last app closed by voice: the ears remember the last ten, by desktop
entry or, failing that, by the command line they ran with.

### Going back

The ears keep the last 30 things they did on the desktop, with what undoes each, in
`~/.local/state/diablo/desktop-history.json`. "Go back", "undo that" or "put it back" walks them
back one at a time: a move or a switch puts the focus back where it was, a window moved to a
workspace goes back to its old one, a closed app is started again, a new tab is closed and a
closed tab reopened, tab, track, volume, mute and play flip back, and typed or dictated text
gets the app's own ctrl+z. Enter and clicks cannot be undone, and he says so. With nothing done
in the last five minutes, "undo" is the focused app's ctrl+z.

"Press control 1", "hit ctrl shift t": any key combo, pressed at once without Jev. "Go to the
YouTube tab" brings the browser forward and uses its `%` tab search, so any open tab can be named.

### T3 Code threads

"Go to the working Diablo thread", "go to thread Diablo purpose": when the line names a thread or
a chat, or T3 Code has the focus, the hub hands Jev the unsettled top-level threads on every T3
host (`hub/t3threads.py` `voice_threads`, the newest twelve, running ones first) and the ears
open the one Jev picks with its `t3code://threads/...` link. The list is kept warm: the hub
refetches it in the background when the ears wake, and a stale one is used while the fresh one
loads, so the pick never waits on T3.

### Dictation

"Dictate" plays the listening chime, and from then on each pause of `ears.dictation_pause_s`
(1.2 s) closes a piece, which is checked against his voice and turned to text at once. When a
piece ends in "dictation done" (or "done dictating", "dictation over"), the phrase is cut and the
whole text is pasted into the window that had focus when dictation began, then the clipboard is
put back. Typed key by key with wtype, it lost the last letter and full stop of every sentence in
T3. Terminals get ctrl+shift+v, by gvoice's own `terminal-classes`. He says "Dictating." first. Nothing goes
to the hub. A minute with nothing said gives up.

## He listens while he thinks

An answer not back within 1.5 s stays thinking in the background, and the ears go straight back to
listening for his name. "Diablo, lights off" while he works on something else happens at once,
since Jev answers it before any lock. The late answer is spoken when it lands, between his turns,
and he can cut it off with the name as usual. "Never mind", "cancel that" or "stop thinking"
stops what is still thinking (`POST /v1/turns/stop`) and drops its answer.

A second spoken request that needs Claude while Claude is busy on the main conversation either
waits its turn or runs at once beside the first:

- It waits when it points back at something, with words like it, that, them, there, then, also, the
  one, or a leading "and" (`hub/brains.py` `POINTS_BACK`): "move that program to workspace 3".
- It runs beside when Jev, asked in the call it already makes, is sure (`voice.parallel_threshold`,
  0.7) that it is its own thing: "open Spotify" while "open Blender" still runs. It gets its own
  Claude, forked from the busy session with `--fork-session` so it knows the conversation, and
  closed after the one turn. The busy session stays the conversation's session.
- Unsure means wait. Measured on 10 pairs: the word rule and Jev together got all 10 right.

`voice.parallel` set to 0 makes every second request wait, as before.

## Where the time goes

Every turn keeps a timeline from the moment his name ended: the wake word firing, the checks, the
listening chime, his first and last word, the quiet wait, the voice check, speech to text, the hub
(with its own split: Jev and the whole hub turn) and then either the desktop act or the voice fetch
and the speaking. Loop lines count from his first word. Turns go to
`~/.local/state/diablo/turns.jsonl`, newest 2000 kept. `diablo-ears timings` draws the last ten as a
waterfall; `--last N` shows more, `--html FILE` writes a page.

## Letting him breathe

A breath or a moment to think is quiet in the middle of a sentence, and the ears used to
close the turn after 1.2 s of it, with a 30 s cap that cut long requests off mid-sentence.
Measured on 889 of his dictation clips (9.3 h, `~/.local/share/gvoice/clips`, Silero at
0.5, 14,467 pauses between speech): the median pause is 0.38 s, 17% last over 1.2 s, 12%
over 1.5 s, 7% over 2 s and 3.4% over 3 s. Six clips in ten hold a pause past 1.2 s.

`voice/endturn.py` decides. After `ears.silence_s` of quiet the ears transcribe what they
have (the same speech to text as the final one, and the words are reused, so a finished
sentence is no slower than before). If the words end a sentence, the turn ends. If they
trail off, the turn stays open until `ears.silence_open_s` of quiet: a comma, a dash, three
dots, no full stop, or a last word like and, but, because, or, to, the, uh, um. A full stop
or question mark after a number, or after "please", ends it. If the check fails or
`ears.adaptive` is 0, the base quiet alone decides.

While the turn is held open the state stays `listening` and `ears.json`, `GET /v1/ears` and
`GET /v1/ears/live` carry `"holding": true`; the Voice page then reads "Still listening.
Take your time." No sound is added.

| Hub setting | Default | What |
|---|---|---|
| `ears.silence_s` | 2.0 | quiet that ends a turn whose words are finished |
| `ears.silence_open_s` | 3.5 | quiet that ends a turn whose words trail off |
| `ears.silence_short_s` | 1.0 | quiet that ends a short command: 4 words or fewer, said in under 1.8 s, not ending on "and", "the" or a comma |
| `ears.turn_max_s` | 90 | longest turn, in seconds |
| `ears.adaptive` | 1 | 0 reads no words and always waits the base quiet |

Change one with `PUT /v1/settings/ears.silence_s` and `{"value": "2.4"}`. The ears reread the
hub's settings every minute, so it is live within one. The same keys in
`~/.config/diablo/config.toml` (as an `[ears]` table or quoted flat keys) are the fallback
when the hub is down. The old `silence_tail_s` only counts when no `ears.silence_s` is set,
and the hub always sends one, so change the hub setting. A restart of `diablo-ears` is needed once, to
load this code; the settings need none after that.

## He never answers the speakers

The mic hears the room, and banditbox has no echo canceller, so `voice/speakers.py` listens
to the default sink's monitor, the exact audio going out, through `parec`.

- A second wake detector runs on that audio. A mic wake within 1.5 s of the speakers
  scoring half the threshold or more is refused: a video, a voice note, or his own voice
  from another app saying "Diablo".
- Music in the mic drags his voiceprint down: wakes with Spotify on scored 0.753 and 0.738
  against a 0.754 minimum. When the speaker tap carried sound (`MUSIC_RMS`, 0.01) in the 2 s
  before the name, the wake check and that turn's request check get the barge-in slack of 0.1.
  The window ends at the name, so his own chimes never count; the voice loop gets none.
- He can be cut off: the wake detector keeps listening while he speaks. Marcus saying
  "Diablo" over him stops the reply at once and starts a new turn with the listening chime.
  His own reply saying "Diablo" never counts, because the speaker tap heard it first. The
  voice check gets 0.1 of slack there, since the clip is Marcus mixed with him.
- While he listens, from before the listening chime to the done chime, every MPRIS player
  that was playing (Spotify, Firefox, AniBeam) is paused, then exactly those resume. A
  request to pause the music runs after that, so it sticks.
- While he speaks, other sound is held for the whole reply, by the same mechanism and the
  same crash file (`voice/speakers.py`, `SoundHold`). It is reference counted, so his
  listening turn, which can start while a reply still plays, joins the reply's hold, and the sound goes back once, to what it
  was, and only where he has not touched it since (a player he stopped stays stopped, a
  volume he moved stays moved). After the last reply releases it waits `speech_hold_gap_s`, so
  a second reply, or his next turn after cutting one off, never lets the sound pop back
  up in between. His listening hold gives the sound back at once, so a request to pause
  the music is never undone by a late resume; the thinking time before a reply is not
  bridged. If the ears die inside a hold, the next start gives the sound back from
  `~/.local/state/diablo/media-held.json`; a stop (SIGTERM) gives it back on the way out.
  Only replies played on this computer hold anything; Telegram and the phone never reach
  the speaker.

  The hub settings that steer it, all optional:

  | Setting | Values | Default |
  |---|---|---|
  | `speech_hold` | `gvoice` (his rules in `~/.config/gvoice/settings.json`), `dim`, `pause`, `off` | `gvoice` |
  | `speech_hold_dim_to` | 0 to 100, the level `dim` and `pause` use for sound that cannot pause | gvoice's `dim_to` |
  | `speech_hold_gap_s` | seconds a released hold waits for the next taker, 0 to 30 | `1.5` |

  `dim` dims everything to the level, `pause` pauses players and dims the rest. Both keep
  an app he marked "nothing" in gvoice out of it. Set one with
  `PUT /v1/settings/speech_hold`; the ears read it within a minute.
- The setup pauses the players, and throws out any take while the computer played sound,
  asking for it to be paused.

## Setup, in the desktop app

`deploy/ears-install.sh` installs openWakeWord into `.venv-voice`, the `diablo-ears` command
with zsh and bash completion, and enables the service. Until a model exists the service waits
and sends one desktop notification per boot that points at the app.

The setup page (in the old desktop app, being rebuilt around the orb) shows his state, how well he knows the
voice, live bars for the mic, the speakers and the wake score against its threshold, the last
wakes and why each was answered or refused, and the setup itself: what he is saying, a dot
per take of the name, the sentence to read, the quiet countdown, the learning's progress and
the three tries. He speaks every prompt too.

The setup runs inside the service (`voice/ears_train.py`, a `Session`), which hands it the
mic. The learning runs as `python -m voice.ears learn` in a capped scope and reports JSON
lines. The page talks to the service's control panel, `voice/ears_api.py`:

| Call | What |
|---|---|
| `GET /v1/ears` | state, model numbers, live levels, last wakes, the session |
| `POST /v1/ears/train` | `{"fresh", "retrain"}` starts a session |
| `POST /v1/ears/train/stop` | stops it, takes kept |
| `POST /v1/ears/listen` | `{"on"}` turns listening for the name on or off |

It binds to 127.0.0.1:8758, and POSTs need `X-Diablo-Ears: 1`, so a web page cannot start a
session. `diablo-ears train` and `status` still work from a terminal
over the same API.

## What the setup records

1. The name from places you choose (desk, bed, across the room, anywhere), with a countdown
   to get there. Each take starts at a 90 ms tick and gets a verdict: started early, still
   talking at the end, two sounds, too short, too long next to your usual length, or
   nothing heard. The page draws the take with the name marked.
2. Near words in your own voice, Diplo always and five more from a rotating list.
3. Ten seconds of noise you make: rubbing the mic, tapping, typing.
4. Two sentences from a pool of 24 that never repeats until it runs out, each word lighting
   up green as gvoice hears it and red when passed over, then ten seconds of free talk.
5. Ten seconds of the quiet room.

The service also keeps a 2 s clip of everyday sound about every 20 s while asleep, when
something happened and nothing scored like the name (`samples/ambient`, newest 4000 kept).
Everything from this mic that is not the name is what stops the model from learning "this
microphone" instead of "Diablo".

RNNoise (`voice/denoise.py`) cleans the mic stream before anything listens, and every
recording before training, so training hears what the live mic hears.

## His dictations

gvoice keeps the audio of every dictation in `~/.local/share/gvoice/clips`, and the text in
`confidence.jsonl`. A drop-in, `~/.config/systemd/user/gvoice.service.d/keep-clips.conf`,
raises its budget from 1 GB to 200 GB, so nothing gets pruned. Every training takes in
the clips it has not seen yet (`voice/dictation.py`): each is cleaned with RNNoise, then
kept as wake features and resemblyzer pieces under `wake/dictation/`. The audio itself
stays with gvoice.

A clip is left out when its text says Diablo, when it has under a second of speech or no
text, or when its voice sits under 0.70 against the average of all his clips. The text
gvoice writes down misses words, so every usable clip is also scored by a wake model that
never trained on it, and the 3 s around its five highest moments go back through whisper
(`double_check`). The first scan, 902 clips and 9.2 h, kept 798 clips and 8.3 h: 43 said
the name (9 of them only found by listening again), 46 had too little speech, and 15 did
not sound like him.

- Four in five clips train as negatives, a window every 0.32 s. The fifth is held out.
  The threshold must give zero wakes on it too, and `talk_fires` in the metrics counts them.
- A second voiceprint, `talk.npy`, is the average of up to 20,000 1.6 s pieces of his
  talk. It checks the request he speaks after the chime, before anything goes to speech
  to text: under `talk_min` plays the low note and he sleeps. One word is too little for
  resemblyzer, so the wake word itself stays checked against the print of his takes.
  Measured against 40 LibriSpeech strangers, his own "Diablo" scored 0.59 on the talk
  print and a stranger's half second 0.49, while 1.6 s of his talk sits near 0.83 and
  1.2 s of a stranger's near 0.52.

## Training and the false wake budget

Everything lives in `~/.local/share/diablo/wake/`:

| What | Where |
|---|---|
| his recordings of the name | `samples/pos/*.wav` |
| his near miss sentences, the room | `samples/speech`, `samples/room` |
| near misses in the piper voice | `samples/confusable` |
| 214 h of other audio as features, negatives | `acav_head.bin`, the first 600k rows of openWakeWord's ACAV100M set |
| 11 h more, never trained on | `validation_set_features.npy` |
| his dictations, as features | `dictation/*.wake.npy`, `*.voice.npy`, `index.json` |
| the model and its numbers | `diablo.onnx`, `diablo.json`, `speaker.npy` |

Each recording is placed 60 times into 2 s clips with speed, gain, reverb and room or pink
noise. The model is a small MLP over 16 openWakeWord embedding frames, trained on the GPU.
A fifth of the recordings are held out first to measure recall. The threshold is the lowest
one that never fires on the 11 h validation set or on any hard negative, plus 0.05, so the
measured false wakes are zero by construction. `diablo-ears status` prints the numbers and
`diablo-ears meter` shows the live score.
