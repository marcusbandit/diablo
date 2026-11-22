# The ears

Say "Diablo", hear a rising chime, talk, stop, hear a falling chime, and he answers out loud.
It runs with the desktop session as `diablo-ears.service`, with no window: it starts after
`graphical-session.target` and stops with it. Started at boot instead, ahead of the compositor, it
got no `WAYLAND_DISPLAY` and every paste, key and window command failed until a restart. When the
session hands over no `WAYLAND_DISPLAY` or `HYPRLAND_INSTANCE_SIGNATURE`, the ears find the newest
of each in `XDG_RUNTIME_DIR` as they start (`window.fill_session`). `deploy/ears-install.sh` moves
an older install, which hung off `default.target`, over to the session.

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
keeps him listening without the name until "stop speech control", a bare "stop", "that's all",
"I'm done", or `ears.loop_idle_s` (300 s) with nothing said. Each line has to pass the voice check against his
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
3. The whole turn, Claude included, when Jev finds no plain command and the line starts with
   "Diablo" (as Whisper spells it), or when the Jev call itself failed. Any other line plays the
   low note and nothing else: speech control takes commands, and
   a sentence to someone in the room used to get Claude's "I can't find anything for me to do" a
   minute later. `ears.loop_commands_only` set to 0 sends every line to Claude, as before.

"Diablo, type ..." and "Diablo, dictate" also work from a normal turn.

### The name inside speech control

"Diablo" said alone in speech control, or said over an answer to cut it off, is a short break from
it, never a request by itself. The line ends at the first pause without asking Jev, the listening
chime plays, and the next line is the request, taken the way a woken turn takes it: plain commands,
then Claude, with no name needed, under speech control's caps. After the name said alone, words he
goes straight on with are kept; after a cut the request starts at the chime. Once that request is
handled, speech control goes on where it was. A bare "stop" or a goodbye as the request ends it, and
the name again starts another break.

The mode words end a line after 0.8 s of quiet (`EARLY_LOCAL_S`), not the full quiet plus a
second speech to text, because the ears read them on the words so far: "dictate", "speech
control" from a turn, a bare "stop" inside speech control. Jev is not asked for them. On a box
whose speech to text runs through the hub, "Diablo, dictate" used to wait so long that he said
it four times. Dictation then reads the mic from where that line stopped, so words he goes
straight on with are kept.

### One way out

A bare "stop", "that's all" or "I'm done" leaves whatever mode he is in: speech control ends with
its falling notes, and dictation ends and pastes what it has. Speech control also takes "done",
"enough" and "finished" alone; dictation does not, since those can be a sentence he dictates. The orb keeps a ring around him for
as long as a mode is on, violet for speech control and gold for dictation.

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
to the hub. A minute with nothing said, or a bare "stop", pastes what it has.

"Dictate" counts said again while he waits ("Dictate. Dictate. Dictate."), and with the text in
the same breath after a pause: "Dictate. Dear Alex," starts dictation with that as its first
piece, and "dictate hello there, dictation done" pastes at once. A line that only talks about
dictation ("dictation is broken"), or runs straight on ("dictate a mail to Alex saying..."), is
a request like any other.

A dictation or a "type" that cannot be pasted is never lost: its words go to the Diablo chat
(`kind: unpasted`), and when only the paste keys failed they stay on the clipboard too. He says
which in one short line.

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

## Several things in a row, one reply

He often says several things in a row. A turn that starts while an earlier one is still thinking,
or its answer is still unsaid, joins that turn's burst (`voice/burst.py`). Each turn still goes to
the hub on its own and its reply lands in the chat, but nothing of the burst is spoken while it
grows: no first lines past the first turn's, no answers in order. Once the newest turn is in and he
has been quiet for `ears.burst_quiet_s` (2 s), the ears send every answer, oldest first, to
`POST /v1/voice/merge`, and Haiku (`hub/voicemerge.py`) folds them into one short spoken reply,
the newest order first and the rest briefly. The merge runs while the ears listen; a turn that
starts meanwhile puts the burst back and joins it. When the hub cannot merge, the ears say the
newest answer whole and the first sentence of each earlier one.

A turn alone is as before: a quick answer is spoken at once, a slow one says its sentences while
it works and the rest when it lands. A reply he cuts off with the name stops and is never said
again, the lines queued behind it included; only what Claude writes after the cut joins the next
burst. A turn of the
burst still thinking once nothing started or landed for 6 s is left behind: the answers that are in are spoken, and it speaks alone when it lands.

## He speaks as he writes

Claude's text reaches the hub token by token, and every sentence comes to the ears as its own
`{"say"}` line the moment it ends (docs/api.md, Talking). A first line that arrives while the ears
still wait on a quick answer is said after 0.15 s instead of after the whole 1.5 s wait, and without
the thinking chime, since his voice says enough. The ears speak the lines as one reply: each is
fetched from Cartesia while the one before plays, one line ahead and no more, so a cut wastes at
most one fetch, and a quarter second of breath sits between them. The first line takes the eyes Jev
has ready and never waits on them; the mouth is measured for every line. The reply stays open while
the next line comes within 8 s (`FOLLOW_S`) and no tool has started. A tool starting (the hub's
`{"step"}` line) ends it once the lines in hand are said and no next line came within 1.5 s
(`WORK_GRACE_S`, so a quick tool still leaves one reply), so the ears go back to listening while the
work runs instead of sitting in speaking; the lines written after the work are the next reply. Saying his name stops the line playing and drops
every line behind it. A reply streamed past 700 characters ends with "the rest is in the chat", and
the final answer never says a sentence a line already said. The lines streamed and the rest of the
answer share the 700 characters: the rest gets what the lines left, in whole sentences, and "the
rest is in the chat" when not even one fits. List items are said one by one, without their bullets.

## A beat while Claude starts

When a spoken line goes to Claude, and not to a plain command, a direct answer or a desktop act, the
hub's stream says so with `{"brain": "claude"}` and the ears play a short line from the phrase bank
at once: "Very well.", "At once.", "Allow me.", "Certainly." or "But of course." (`voice/phrases.py`
`BEAT`). The clips are cached per voice id under `~/.local/share/diablo/phrases`, and the ears fetch
any missing one for the current voice themselves, so no beat costs a speech call. It plays on its
own thread, so his sentences keep streaming in behind it, and only for a turn alone: not in a burst,
not over something he is already saying, and not with `ears.beat` set to 0. When his first sentence
then opens with an acknowledgement of its own ("On it", "Right away, sir"), that part is not said
again: "On it, checking now." comes out as "Checking now.", and a line that was nothing else is
skipped. The charter tells him the beat already plays, so a spoken turn opens with substance.

## Where the time goes

Every turn keeps a timeline from the moment his name ended: the wake word firing, the checks, the
listening chime, his first and last word, the quiet wait, the voice check, speech to text, the hub
(with its own split: Jev and the whole hub turn) and then either the desktop act or the voice fetch
and the speaking. Loop lines count from his first word. Turns go to
`~/.local/state/diablo/turns.jsonl`, newest 2000 kept. `diablo-ears timings` draws the last ten as a
waterfall; `--last N` shows more, `--html FILE` writes a page.

A streamed turn adds its own points: `brain_back` when the hub said Claude has it, `beat_audio`
when the beat was first heard, `first_sentence` when his first sentence came in and `first_audio`
when the first sample of any reply reached the speakers. `diablo-ears timings --medians` prints the
median from his last word (`speech_end`) to each of them and to the end of the spoken answer, over
the last 200 turns or `--last N`, counting only the turns that reached that point. Turns logged
before these points existed show up as not measured.

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
| `ears.own_voice_end` | 1 | 0 lets any voice keep a turn open, as before (next section) |

Change one with `PUT /v1/settings/ears.silence_s` and `{"value": "2.4"}`. The ears reread the
hub's settings every minute, so it is live within one. The same keys in
`~/.config/diablo/config.toml` (as an `[ears]` table or quoted flat keys) are the fallback
when the hub is down. The old `silence_tail_s` only counts when no `ears.silence_s` is set,
and the hub always sends one, so change the hub setting. A restart of `diablo-ears` is needed once, to
load this code; the settings need none after that.

## Only his voice keeps a turn open

Any voice used to break the quiet. With people talking in the room a turn stayed open until
`ears.turn_max_s`, and Whisper wrote their sentences into the request. `voice/ownvoice.py`
now judges whose talk it is while he speaks:

- Every 0.64 s of new speech, the last 1.6 s of speech is scored on a thread of its own, so
  the mic loop never waits. A window at 0.75 or more against `talk.npy` is his for certain,
  and those windows make a print of the turn itself: the same mic, room and music. Once there
  is one, a window is someone else's when it is under 0.68 against it and under `talk_min`
  plus 0.06 against `talk.npy`; before that, when it is under `talk_min` less 0.05. On 1.6 s windows strangers in a room scored 0.50 to
  0.70 against `talk.npy`, too close to his 0.66 to 0.90, but against his windows in the same
  turn 95% of theirs sat at 0.66 or under and 99% of his at 0.78 or over.
- With sound from the speakers during the window, or before the name, `talk.npy` alone never
  calls a window someone else's: his talk over a song scored 0.43, and a song alone 0.44 to
  0.56. The turn's own print still can, with the barge-in slack of 0.1.
- Each window votes on the speech in it; most votes wins, and a tie is his. Their talk counts
  as quiet. Talk that runs straight on from theirs, with no pause, is theirs until a window
  says otherwise, or a stranger who never pauses would hold the turn open. Quiet that comes
  from their talk ends a turn 1.6 s later than real quiet: when he picks up again over them,
  his voice needs about that long to win a window back.
- Before speech to text their stretches are cut out, but only where two windows called the
  speech someone else's and none called it his, keeping 0.16 s next to any other speech; a
  stretch under 0.5 s stays. So his reply straight after a stranger, which only the newest
  window has heard, is never cut. The end of turn
  check and the voice check read the cut audio. A turn with nothing of his left counts as
  nothing said.
- Unknown is his: a window the voiceprint could not score within 3 s, one with too little
  speech in it after resemblyzer's own trim, and one that came while the voiceprint thread
  was busy, which is skipped rather than queued in front of the request's own voice check.
- Normal turns, speech control lines and dictation pieces all use it. A dictation piece needs
  1.6 s of speech before it gets a window, so short pieces are as before; a long one ends on
  his own pause, and the room's talk is not typed.

Measured with `scripts/ownvoice_eval.py` on 12 of his held out dictations of 5 to 14 s,
each mixed in a room with a LibriSpeech stranger or a song, cleaned with RNNoise and replayed
through `record()` in real time with a probe that reads every line as finished. Seconds from
his last word to the end of the turn, median and worst:

| Case | Before | After | Cut off | His speech kept | Their speech left in |
|---|---|---|---|---|---|
| him alone | 2.2, 2.2 | 2.2, 2.2 | none | all | |
| a stranger talks on for 10 s after him | 12.2, 15.5 | 6.2, 9.2 | none | all | 80% |
| a stranger 9 dB under him, and on for 10 s | 8.4, 15.5 | 4.6, 12.3 | none | all | 96% |
| a song 9 dB under him, and on for 10 s | 2.5, 12.8 | 2.5, 12.8 | 1 of 12, both | 94%, both | 99% |

Before, a stranger who kept talking held the turn until they stopped, 90 s at the most. With
them talking on, a turn now ends about 4 s past the usual quiet: the 1.6 s above, and the
first second of their talk, which still counts as his while the windows over it are mostly
him. That first second and their last 1.3 s, which fewer than two windows have heard, are
what is left of them in the audio; the shorter turn is most of the gain. The one cut off
under the song is a clip with a 6.7 s pause of his own, the same before and after; under a
song the print abstains, so nothing changes there.

`ears.own_voice_end` set to 0 turns it off. Each turn's timeline notes `own_windows`,
`own_others` (the windows that were someone else) and `own_cut_s`.

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
  | `speech_hold_gap_s` | seconds a released hold waits for the next taker while the conversation goes on, 0 to 30 | `1.5` |

  `dim` dims everything to the level, `pause` pauses players and dims the rest. Both keep
  an app he marked "nothing" in gvoice out of it. Set one with
  `PUT /v1/settings/speech_hold`; the ears read it within a minute.
- The setup pauses the players, and throws out any take while the computer played sound,
  asking for it to be paused.

## Other sound goes quiet while he talks

`voice/duck.py` turns every other app's stream down while a conversation is on: from the
listening chime through thinking and the reply, and the whole of speech control and dictation
to Diablo. The sound comes back the moment the conversation ends, with no linger. It works per
stream (PipeWire sink inputs through `pactl`), so his master volume is never touched, and it
looks again every 0.25 s, so a video or song that starts mid-conversation goes down too.
Diablo's own voice and chimes (`Diablo`, `pw-play`, `diablo-voice`) are never touched, and
neither is an app his gvoice rules mark "nothing".

- Afterwards each stream gets back the exact volume it had, only where nobody moved it: a
  volume he changed meanwhile stays where he put it, also when the level changes mid-conversation.
- WirePlumber saves each app's last volume. A stream that closes while down leaves its app
  saved low, so that app is kept as an orphan, and its next stream that starts at the low level
  is lifted back, which mends the saved volume too. A new stream of an app that is down and
  opens mid-conversation at the low level is taken as down already, not lowered twice.
- What is down, and the orphans, are in `~/.local/state/diablo/ducked.json`: a crash gives it
  all back on the next start, a stop gives it back on the way out, and a `pactl` that does not
  answer keeps the list to try again. The ears own it, so a hub restart cannot strand anything.
- The holds above leave the dims to it: a stream it turned down is never dimmed by a hold as
  well, since two owners of one volume would each give back the other's level. Pauses still
  happen as before.
- A paused video never plays again before its sound is back (Marcus: "There shall never be a
  moment where video is playing that was playing before, where there is no audio"). A hold that
  is about to resume a player first has the duck give the sound back and waits for it. When the
  conversation is over, the hold skips its gap and the duck's end hands straight on to it, so
  the sound and the video come back in one step, the sound first. A player resumed in the
  middle of a conversation, such as after he stops talking and Diablo thinks, gets its sound
  back and stays up for the rest of that conversation.
- Settings, in `~/.config/diablo/duck.json` (`{"on": true, "level": 20}`), on the central card's
  Settings under Ears: the switch and the level (10, 15, 20 or 30 percent of each stream's own
  volume). `POST /v1/ears/duck` on 127.0.0.1:8758 takes `{"on", "level"}`.
- "Don't turn it down", "stop ducking", "keep the music up", "leave the sound alone" keep the
  sound up for the rest of that conversation: no duck, and the holds take nothing either, no
  pause, no dim, no mute. The ears handle it with no model; a line that asks for more than that
  keeps the sound up and still goes on to the brain. The brain's
  `keep_sound_up` tool does the same from any channel (`on` false turns it down again): the hub's
  `POST /v1/ears/duck` sends a `duck` event, and `presence/busy.py` hands it to the ears. Asked
  between conversations, it holds for the next one if that starts within 10 minutes.

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
