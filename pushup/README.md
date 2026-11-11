# pushup

A random set of home exercises about once an hour, shown as a full-screen card on Hyprland's
overlay layer, so it lands on top of fullscreen video and games.

- Twenty-six exercises, from push-ups and Bulgarian split squats to pike push-ups, cossack squats,
  hollow holds and compression holds, the L-sit's floor version. Most need nothing but a floor, a
  wall or a chair. The rest need gear, and you say what you have on the intro's moves page or with
  `pushup gear on table`: a pull-up bar, a sturdy table, two chairs, parallettes. A move whose gear
  you lack never comes up. Holds count seconds. Each plays
  as a looping pixel figure next to a front and back body map of the muscles it hits.
  "Another exercise" (or Tab) swaps to the next one with its own goal. `pushup exercises off
  burpees` takes one out of the rotation.
- Every third card is a stretch, for the front split, the side split, pike and open hips: thirteen
  holds in the split order David Thurin (Movement by David, Stay Flexy) teaches, hamstrings, then
  hip flexors, then both, then the split. His own routines are in his paid programs, so these are
  the standard stretches for each step. S starts a timer that counts each side and logs the hold.
  A hold grows five seconds for every four you've done, up to double. `pushup stretches off` takes
  stretch cards out of both modes, and a rest day then sends no cards at all.
- Picks lean toward the muscles you've worked least this week, and the receipts lead with the week
  on the body: what got worked, what got stretched, every muscle ranked, and the three that got
  least.
- Goals scale per exercise from your level: normal is 10 to 20 push-ups, 15 to 30 squats, 30 to
  60 seconds of plank. After that each exercise learns from its own sets.
- Sets you do on your own get logged in one go: `pushup log 3x10 rows`, or `pushup add`, which
  picks reps and a number of sets and starts from what you logged last time for that move.

- Training you do elsewhere counts. Log it with "I trained today" on the card (T), `pushup add`
  or `pushup train climbing 90m hard`. A session loads the body parts it works (climbing: back,
  grip and core most), and that load halves every 20 hours. The card leaves out exercises that
  lean on a sore part, lowers the goal for the rest and says why, and after a heavy session (two
  hard hours of climbing, say) it turns into a rest day: calm stretch cards only, and passing one
  doesn't count as a skip.

- Something hurts? "It hurts" (H) sits beside the skip on every card. It opens the body map, big,
  front and back: hover a muscle and it says which one and which side ("RIGHT TRICEPS"), click
  every spot that hurts, click again to let one go, then Log. The spots are kept in the log, and
  the card comes straight back eased by how hard each move leans on those muscles: sore triceps
  cut push-ups and dips, not squats. Logging with nothing picked means the move's main part.
  `pushup hurt left triceps right chest`, or just `pushup hurt arms`, does the same from a terminal. Goals for moves that lean on that part drop, and in
  the challenge so does the day's push-up target. How far depends on how hard the part got worked
  these last few days: three days of push-ups and sore arms keeps 40% of the usual, sore arms after
  an easy week keeps 80%. The card goes calm blue, says why, and a pass on it never counts as a
  skip. It holds for that day and at half strength the next. Each day in a row gets less: the third
  day only a little less, and from the fourth the card stops easing further and says to rest it or
  have it looked at. `pushup hurt off arms` when it's better.

- Every 45 to 70 minutes a card asks for a number of reps. The first sets are random within your
  level (10 to 20 on normal). After that the goal follows what you log. Fresh, it's what you can
  do plus one or two, never far past your best recent set. Tired, it's what you can still do, or
  one more. Recent reps count as fatigue that halves every two hours, and how hard it hits you is
  learned from how your sets drop through a session. A short set while tired counts as fatigue,
  not lost strength, and the next goal won't be higher than the set you just came up short on.
  The card says when it lowered the goal, and why.
- Video players get paused while the card is up and resumed after: YouTube in the browser, mpv,
  anibeam. Music players (Spotify and friends, YouTube Music, local audio files) keep playing.
- While a Discord client (Vesktop, Discord, Legcord and so on) holds a microphone stream, you're
  in a voice call, and the set waits until you hang up.
- The countdown stops behind the lock screen, and locking gives five minutes back, since the idle
  lock means you were already gone a while. A card that is up when the screen locks closes without
  counting as a skip and comes back five minutes after you unlock.
- The card is the number, a slider and a Log button. The big number follows the slider. The
  slider's scale is fixed per level (easy 0 to 20, normal 0 to 25, hard 0 to 35), and the goal is
  a notch in the track with a marker under it; the thumb turns green once you reach it. Arrow keys, drag or the wheel move it; Enter logs.
  Keys and buttons ignore the first 1.5 s so a keypress meant for a game can't dismiss it.
- The card talks like a coach, and sends you to the right spot: the floor for push-ups, your feet
  for squats, the wall for a wall sit, under the table for rows, and a hold reads as seconds
  ("Wall. 40 seconds of wall sit."). While you show up it's short and straight, and a run of three or
  more done cards gets said out loud in green ("4 IN A ROW"). There is no snooze. Skipping means
  holding Esc (or the skip link), longer with every skip in a row, and each skip makes the card
  rougher and redder: blue, then yellow at two, orange at three, red with scanlines and a shake
  from four. Logging a set with reps in it resets it, and the next card says so. Rest day cards
  and the challenge's bonus sets past the target are passed, not skipped, and never count.
- `spice` in the config is `mild` (clean language) or `spicy` (the same coach, swearing). The
  intro asks.
- Three endings: goal hit (bright), short (gray number), skipped (red, number struck through).
  A logged set shows today on the clock with the new set lit; in the challenge the push-ups climb
  as a staircase toward the target. A skip says what it left undone. Each ending shows a bar
  running down to its auto close; Enter closes it sooner, and Full stats (R) opens the receipts
  once the set is logged.
- The receipts (`pushup graph`) have four tabs, 1 to 4 or Tab, and `--tab` opens one directly:
  - Overview: today set by set on a clock, with the pace that finishes on time and skips as red
    crosses; the day streak, the share of cards done this week against last week, and the best
    and average set of your main move against last week; the month below, with missed days
    crossed out in the challenge and each day's skips stacked in red otherwise.
  - Body: the week on the body, every muscle ranked, and the three that got least.
  - Moves: every move from the last 30 days, its sets this week, best set, last set and two
    weeks of best sets.
  - Timeline: four weeks by muscle group, a column a day for push, pull, legs and core, warmer
    for more work and ringed where logged training hit it, each row ending in its sets and the
    move that gave most of them. A set counts for a group when it leans on it, so push-ups are
    push and not core. Under it, the last seven days set by set.

- A day counts sets, not reps, since twelve push-ups and forty seconds of plank don't add up to
  anything. Under the number sits what the sets were: 27 push-ups in 2 sets, 45 seconds of plank.
- First launch runs a seven screen intro: what it is, what it notices, a level, the coach's voice, which
  exercises are in, which flexibility goals to stretch for, and how training counts. People who
  ran an older intro get the last three once.

## The challenge

`pushup challenge start` swaps the mixed cards for a 30 day push-up challenge, after the video of
someone doing 100 a day for a month, at a slightly kinder pace.

- Push-ups, Bulgarian split squats, body rows and David's stretches. Feet-up (decline) push-ups join
  when you switch them on yourself: `pushup challenge feet-up on`, or the launcher. Nothing nags
  you about it. Once on, the card offers them first and plain push-ups one Tab away; both count.
- Push-ups have a daily target: 50 on day one, climbing to 100 over two weeks, then holding. It
  never runs more than 25 past your best day, so a short day doesn't snowball.
- A day you train counts. Log half an hour or more of real work (`pushup train bouldering 1h`,
  or "I trained today" on the card) and the challenge takes the rest of that day off: no cards
  until tomorrow, and the month chart shows the day as kept instead of a red cross. `pushup now`
  still brings a card if you want one.
- The sets are paced to finish by 03:00 (`day_ends`), counted from when you're back at the
  desk: what's left of the target, spread over what's left of the evening up to an hour before
  03:00, a set every 10 to 25 minutes. Each set is counted at the low end of its goal, so a skip
  or a short set doesn't leave you cramming at 02:59. Inside that last hour and still short, they
  come every 10 minutes.
- Past the target every card is a bonus set, every 40 to 55 minutes until you go to bed. It still
  asks, in green ("DAY 7 OF 30: BONUS SET"), but it doesn't press: a short hold passes it, a pass
  never counts as a skip, and the coach says so. Each one passed in a row pushes the next one 20
  minutes further out, up to 40. Coming up short on a bonus set gets a kind word, not a lecture.
- A day counts until 07:00, so a set at 01:00 or 04:00 belongs to the evening before. The next
  day starts the first time you're back after a night away: the screen locked or the computer
  off for two hours, or 10:00 if you never left. The idle lock already pauses the countdown.
- Bulgarians are taken slowly: two sets a day the first week, three the second, four after.
  A set starts at 5 each leg and grows by one each leg for every day you hit it, up to 15. A leg
  number on a card is always per leg, and the card says the total under it.
- Body rows are the pull to all those push-ups: lie under a sturdy table, heels on the floor, and
  pull your chest to its edge. Three sets a day, four from the third week, with the goal learned
  from your sets. Rows and Bulgarians each come round every third card, never two of them in a row,
  so a push-up card always sits between. `pushup challenge rows off` leaves them out.
- Once the day's push-ups are in, every fifth card is a stretch from David Thurin's own free videos (Movement by David), none of
  the easy ones: front split practice, middle split practice, the PNF pancake, deep pigeon and the
  lunge with a foot grab. His rules, from "The Scientific Way To Get Your Splits" and "Stretching
  is Bodybuilding": 30 second sets hard enough that you want to quit before the end, never into
  pain, two sets, half a minute of rest between. The PNF ones split each set the way his PNF video
  does: sink in, squeeze without moving, relax and go deeper. The timer calls every phase with a
  chime. Holds don't grow longer; the progress is depth.
- The first challenge card comes after one page on what the challenge is. The receipts lead with
  the month: today's count against the target and every day's bar.

    pushup challenge                 day, count, target, Bulgarians, feet up
    pushup challenge start [--over]  start, or with --over begin again from day 1
    pushup challenge feet-up on|off
    pushup challenge rows on|off     body rows at the table edge, on unless you say otherwise
    pushup challenge off             back to the mixed cards

### In the banditshell launcher

`install.sh` links `banditshell-plugin/` into `~/.config/banditshell/plugins/pushup` (the plugin
host is banditshell PR #423). Type `pu` in the launcher: today's count and the next set (Enter
opens the month), a set now, logging, the day's Bulgarians and rows, feet up on and off, pause. Typing a
number logs it: `pu 15` is 15 push-ups, `pu 8 d` feet-up push-ups, `pu 6 b` Bulgarians, 6 each leg,
`pu 3x10 rows` three sets of body rows. Pull-ups, dips and pike ups work the same way. A plain
search for overview, status, stats, progress or muscles, no `pu` needed, offers the training
overview and the muscle groups over time.
`pushup status --json` is what it reads.

## Install

    ./install.sh

Links `pushup` into `~/.local/bin`, installs zsh and bash completion, and enables the
`pushup.service` user unit. Needs `gtk4-layer-shell`, `python-gobject`, `playerctl`, PipeWire.

## Use

    pushup status        next set, next goals, recovery, today's count (--json for the launcher)
    pushup challenge     the push-up challenge: start, off, feet-up on|off
    pushup now           a set right now
    pushup pause 2h      quiet for a while (30m, 2h, tomorrow)
    pushup resume
    pushup level hard    easy 5-12, normal 10-20, hard 15-30, or two numbers
    pushup spice mild    mild or spicy coach
    pushup stats 14      reps per day
    pushup graph         the receipts; --tab overview|body|moves|timeline opens one
    pushup log 3x10 rows record sets you did on your own: pushup log 20, pushup log 30 squats.
                         Short names work: rows, pullups, dips, pike ups
    pushup train climbing 90m hard
                         record training: easy, normal or hard, --yesterday for yesterday
    pushup hurt arms     a part hurts: smaller goals and target for it; off arms when better
    pushup add           the logging card: pick an activity or exercise, how long, how hard
    pushup exercises     which exercises and stretches are in; pushup exercises off burpees
    pushup stretches off stretch cards out of both modes; on brings them back
    pushup check         what it sees: Discord call, lock, players and what it would pause
    pushup intro         the first-run walkthrough again
    pushup sync          write the overview for nemurenai now, and show what came in

Settings live in `~/.config/pushup/config.toml`. History is in `~/.local/state/pushup/log.jsonl`:
sets carry `exercise`, `reps` (the goal) and `did`, training sessions carry `activity`, `minutes`,
`effort` and `source`.

## Sync with nemurenai

The log and an `overview.json` (today, the week on the body, four weeks by muscle group, the
challenge) go to nemurenai over Syncthing, for the Diablo app. Training the app drops in
`~/.local/state/pushup-inbox/` on nemurenai comes back and counts here like anything logged on
this box: sessions from Health Connect make the cards steer around tired muscles and can turn a
day into a rest day. Formats and the folder setup are in [docs/sync.md](docs/sync.md).

## Development

`scripts/shot.sh NAME [overlay args]` renders a card in a GTK Broadway display and screenshots it
headless, so nothing shows up on the desktop. Figures hold still there (`PUSHUP_STILL`, a share of
the loop), since a stream of animated tiles never lets the headless browser settle.
`python3 figure.py captures/sheet.png [names]` draws every move as a row of frames, for checking
poses by eye; the moves are keyframes in `figure.py` with elbows and knees from two-bone IK.
`python3 figure.py check` fails if any elbow or knee bends backwards anywhere in a loop, or if a
foot slides along the floor: a foot that moves between keyframes lifts and steps instead.
`musclemap.py` draws the body map; muscles per move live in `catalog.py`.

`scripts/live.sh` shows a real card for five
seconds without grabbing the keyboard. `scripts/challenge-e2e.sh` runs the challenge daemon against
throwaway state with sets seconds apart.
