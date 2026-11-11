"""The setup for the ears, run inside the ears service and drawn by the desktop app.

A Session walks Marcus through it in Diablo's own voice: what it is for, then after each
chime his name 20 times (plainly, from across the room, quickly), two sentences full of
near misses, ten seconds of the quiet room, the learning, and three tries. Every step is
published through snapshot(), which the service serves at GET /v1/ears for the Voice page
of the desktop app. Stopping keeps every take already saved,
and a session with retrain=True skips straight to the learning.

The learning itself runs as a separate process in a capped systemd scope
(`python -m voice.ears learn`), so the service stays small; it reports progress as JSON
lines on stdout.
"""

import json
import logging
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import chime, wake

log = logging.getLogger("diablo.train")

# Where the takes of the name come from. Each place is said from where Marcus would really
# call him, so the model learns his voice through the room, not one way of saying it.
PLACES = [
    {"id": "desk", "label": "At your desk", "count": 6, "move": False,
     "intro": "Let's start at your desk. After each tick, say my name the way you'd call me."},
    {"id": "bed", "label": "From your bed", "count": 5, "move": True,
     "intro": "Now go to your bed and call me from there. Lying down is fine."},
    {"id": "far", "label": "Across the room", "count": 5, "move": True,
     "intro": "Now go as far from the microphone as the room allows. By the door, perhaps."},
    {"id": "anywhere", "label": "Anywhere, any way", "count": 4, "move": True,
     "intro": "Last few. Anywhere you like, any way you'd say it: tired, quick, loud, half asleep."},
]
# Sentences full of near misses, two per session, never repeated until all are used.
SENTENCES = [
    "The diabolical fable of Pablo and Diana sat on the table, a viable deal, though the dial was broken.",
    "Diego drew a diagram of the bubble, then said the able one should have known better than that.",
    "I'd rather dial the bureau than argue with Pablo about a table for two.",
    "Fabio played Diablo three all night, then said the diagonal route was not viable.",
    "Dee, a blow to the head is no excuse for a diabolical plan like that.",
    "The idea below the table was viable, but Diana had a dialogue ready.",
    "Via blog posts and a dial-up modem, the tableau of the old web came back.",
    "Dial a number, any number, and ask if the able bodied crew can come by.",
    "That violin solo was diabolically hard, but Pablo nailed it at the audition.",
    "A dialect from the valley, a bowl of soup, and a nap before the evening.",
    "Diabetes, dialysis and diagnosis are three words the doctor said in one breath.",
    "The bubble tea place below my flat has a tableau of Diablo masks on the wall.",
    "Bella, deal or no deal, the price was below what the dealer asked.",
    "Diablo is a word I'll say in this sentence on purpose, then never again.",
    "Dee asked whether a blow-dryer was allowed in the hostel bathroom.",
    "The diagonal lines on the diagram looked like a pattern from a fable.",
    "Ask Pablo, he's able to fix the dial before the guests arrive at seven.",
    "Yeah, below the bridge, the river was too high to cross on foot.",
    "Diana bought a tablet, a ladle and a bottle of olive oil in the market.",
    "If the deal's viable, I'll call you, and if not, I'll call it a day.",
    "Every time I hear the word diabolo I think of that juggling toy.",
    "The lobby of the hotel smelled of diesel and old cigarette smoke.",
    "Hey, about the table, it's fine where it is, leave it by the window.",
    "Dial back the volume a bit, the neighbours already complained once.",
]
FREE_TOPICS = [
    "what you ate today", "the last thing you watched", "your plans for the weekend",
    "something that annoyed you this week", "the room you're in", "a game you like",
]
MOVE_S = 12
# Near words said in his own voice, as things that must never wake him. Diplo is always in:
# it woke him on 2026-11-09. The rest rotate like the sentences.
NOT_NAME = ["Diego", "Pablo", "Diabolo", "Tableau", "Viable", "Diana", "Dial up", "Deeper",
            "Bye bro", "The audio", "Dee", "Diagram", "Fabio", "Below"]
NOT_NAME_EACH = 6
NOT_NAME_S = 2.2
NOISE_S = 10.0
# How often the words of a sentence are checked while it is read.
HEAR_EVERY_S = 0.7


def _norm(word):
    import re

    return re.sub(r"[^a-z0-9']", "", word.lower())


def align(sentence, heard):
    """Marks each word of the sentence as heard ("hit"), passed over ("miss") or not yet ("").

    The heard words are walked in order. Each one is matched to the next sentence word
    within a few places; the sentence words it jumped over are misses. Extra heard words
    are ignored, so a stumble never blocks the rest from lighting up.
    """
    from difflib import SequenceMatcher

    words = sentence.split()
    want = [_norm(w) for w in words]
    marks = [""] * len(words)
    at = 0
    for got in (_norm(w) for w in heard.split()):
        if not got:
            continue
        for j in range(at, min(at + 4, len(want))):
            if want[j] and (want[j] == got or SequenceMatcher(None, want[j], got).ratio() >= 0.8):
                for k in range(at, j):
                    marks[k] = "miss"
                marks[j] = "hit"
                at = j + 1
                break
    return marks


def pick_not_name(n=NOT_NAME_EACH, rng=None):
    # Diplo is always in: it woke him on 2026-11-09.
    return _pick("notname", NOT_NAME, n, rng, always=("Diplo",))


WORD_S = 2.6
PASSAGE_S = 9.0
ROOM_S = 10.0
TRIES = 3
TRY_S = 25.0
# Digital level of the computer's own output during a take above which the take is thrown out.
BLEED_RMS = 0.004
BLEED_LINE = "I can hear the computer playing something. Pause it, and we'll go again."
BLEED_TRIES = 3
# Output buffered behind the chime still reaches the tap this long after pw-play exits.
TAP_GRACE_S = 0.3
REPO = Path(__file__).resolve().parent.parent
# Lines of the learning that move its progress bar: four fits of six logged steps each,
# then the calibration line. See wake.train and wake._fit.
LEARN_TICKS = 25


class Stopped(Exception):
    pass


def _pick(name, pool, n, rng=None, always=()):
    """n items from a pool not used before, plus always; the pool starts over when it runs out."""
    import random

    rng = rng or random.Random()
    path = wake.SAMPLES / f"{name}-used.json"
    try:
        used = set(json.loads(path.read_text()))
    except (OSError, ValueError):
        used = set()
    want = n - len(always)
    fresh = [t for t in pool if t not in used and t not in always]
    if len(fresh) < want:
        used, fresh = set(), [t for t in pool if t not in always]
    picked = rng.sample(fresh, want)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(used | set(picked)), indent=1))
    return list(always) + picked


def pick_sentences(n=2, rng=None):
    return _pick("speech", SENTENCES, n, rng)


def saved():
    return {kind: len(list((wake.SAMPLES / kind).glob("*.wav"))) for kind in ("pos", "speech", "room")}


class Session:
    """One setup run. The service owns the mic and hands it over while this is active."""

    def __init__(self, cfg, mic, settings, live=None, tap=None, fresh=False, retrain=False,
                 speaker=None, learn=None, places=None):
        from .ears import _Quiet

        self.cfg, self.mic, self.settings, self.live, self.tap = cfg, mic, settings, live, tap
        self.fresh, self.retrain = fresh, retrain
        if speaker is None:
            from .tts import Speaker

            speaker = Speaker(cfg, _Quiet())
            speaker._gap = lambda: 0.3
        self.speaker = speaker
        self.learn = learn or learn_in_scope
        self.places = [p for p in PLACES if not places or p["id"] in places] or list(PLACES)
        self.cancel = threading.Event()
        self.ready = threading.Event()
        self.skip = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.state = {
            "active": False, "stage": "starting", "said": "", "passage": "", "recording": False,
            "take_s": 0.0, "take_left": 0.0, "group": "", "round": 0,
            "rounds": sum(p["count"] for p in self.places),
            "places": [{k: p[k] for k in ("id", "label", "count")} for p in self.places],
            "moving": False, "move_left": 0.0, "last": None, "prompt": "",
            "word": "", "notname": [], "heard": "", "marks": [],
            "takes": [], "tries": [], "progress": 0.0, "log": [], "metrics": None, "error": "",
            "paused": [], "retrain": retrain, "started": time.time(),
        }

    # ------------------------------------------------------------ state

    def _set(self, **changes):
        with self.lock:
            self.state.update(changes)

    def _append(self, key, item):
        with self.lock:
            self.state[key] = self.state[key] + [item]

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    @property
    def active(self):
        return self.state["active"]

    # ------------------------------------------------------------ control

    def start(self):
        self._set(active=True)
        self.thread = threading.Thread(target=self._run, name="ears-train", daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.cancel.set()
        self.speaker.stop_now()

    def go(self):
        """Marcus is at the next place: start the takes now instead of after the countdown."""
        self.ready.set()

    def skip_place(self):
        """Leave out the rest of this place."""
        self.skip.set()
        self.ready.set()

    def _check(self):
        if self.cancel.is_set():
            raise Stopped()

    # ------------------------------------------------------------ steps

    def say(self, text):
        self._check()
        self._set(said=text)
        state, _, _ = self.speaker.say(text, self.settings.get(), interruptible=False)
        if state == "unspoken":
            # No voice available: give the words on screen time to be read.
            self.cancel.wait(min(len(text) / 18, 4))
        self._check()

    def _record(self, seconds, on_block=None):
        """A take that starts with a 90 ms tick, or None when the computer played sound.

        The long listening chime is not used here: Marcus reacts to a sound starting, and
        a take that began after a 0.7 s chime ended had already lost the start of the name.
        """
        self._check()
        # Wait for the tick to have sounded: pw-play takes a moment to start, and a take
        # timed from its launch caught the tick itself or counted it as bleed.
        chime.play("tick").wait()
        began = time.monotonic()
        self.mic.drain()
        self._set(recording=True, take_s=seconds, take_left=seconds)
        blocks, need, got = [], int(seconds * wake.RATE), 0
        try:
            while got < need:
                self._check()
                stamp, block = self.mic.read(timeout=2.0)
                if stamp < began:
                    continue
                blocks.append(block)
                got += len(block)
                self._set(take_left=max(seconds - got / wake.RATE, 0.0))
                if on_block is not None:
                    on_block(blocks)
        finally:
            self._set(recording=False, take_left=0.0)
        if self.tap is not None and self.tap.level(since=began + TAP_GRACE_S, until=time.monotonic()) > BLEED_RMS:
            return None
        return np.concatenate(blocks)[:need]

    def _clean_take(self, seconds, on_block=None):
        for _ in range(BLEED_TRIES):
            audio = self._record(seconds, on_block)
            if audio is not None:
                return audio
            chime.play("never")
            self.say(BLEED_LINE)
        raise RuntimeError("The computer kept playing sound during every take. Stop it, a game, a call or a tab, and start again. The takes so far are kept.")

    def _move(self, place):
        """Says where to go, then counts down until the takes start there."""
        self.ready.clear()
        self.skip.clear()
        self._set(moving=True, move_left=float(MOVE_S))
        self.say(place["intro"] + f" I'll tick in {MOVE_S} seconds, or press I'm there.")
        end = time.monotonic() + MOVE_S
        while time.monotonic() < end and not self.ready.is_set():
            self._check()
            self._set(move_left=max(end - time.monotonic(), 0.0))
            self.ready.wait(0.1)
        self._set(moving=False, move_left=0.0)

    def _typical(self):
        good = [t["seconds"] for t in self.state["takes"] if t["ok"]]
        return float(np.median(good)) if len(good) >= 3 else None

    def record_names(self):
        names = list((wake.SAMPLES / "pos").glob("*.wav"))
        # Numbered after the highest one, so a deleted take never gets a good one overwritten.
        n = max((int(p.stem) for p in names if p.stem.isdigit()), default=-1) + 1
        done_all = 0
        for place in self.places:
            self._set(stage="names", group=place["id"])
            if place["move"]:
                self._move(place)
            else:
                self.say(place["intro"])
            done = 0
            while done < place["count"]:
                if self.skip.is_set():
                    self.skip.clear()
                    break
                self._set(round=done_all + 1)
                audio = self._clean_take(WORD_S)
                verdict = wake.judge(audio, self._typical())
                take = {k: verdict[k] for k in ("ok", "code", "why", "detail", "seconds", "start", "end", "db", "floor", "envelope")}
                take.update(place=place["id"], take_s=WORD_S)
                if not verdict["ok"]:
                    self._append("takes", take)
                    self._set(last=take)
                    chime.play("never")
                    self.say(verdict["why"])
                    continue
                wake.write_wav(wake.SAMPLES / "pos" / f"{n:03d}.wav", audio)
                take["file"] = f"{n:03d}.wav"
                self._append("takes", take)
                self._set(last=take)
                n += 1
                done += 1
                done_all += 1
                self.cancel.wait(0.35)

    def record_not_name(self):
        """Near words in his voice: things that sound a bit like the name and must not wake him."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._set(stage="notname", group="", round=0)
        self.say("Back at your desk. Now some words that are not my name. Say each word on screen after the tick.")
        for i, word in enumerate(pick_not_name()):
            self._set(word=word, round=i + 1)
            for attempt in range(3):
                audio = self._clean_take(NOT_NAME_S)
                verdict = wake.judge(audio)
                # Two sounds is right for "Dial up"; only nothing, early or late is redone,
                # and never more than three times: any take of a near word still teaches.
                if verdict["code"] not in ("nothing", "early", "late") or attempt == 2:
                    break
                chime.play("never")
                self.say(verdict["why"].replace("my name", "the word"))
            wake.write_wav(wake.SAMPLES / "notname" / f"{stamp}-{i}.wav", audio)
            self._append("notname", {"word": word, "seconds": verdict["seconds"], "envelope": verdict["envelope"]})
            self.cancel.wait(0.3)
        self._set(word="")

    def record_noise(self):
        """Ten seconds of the sounds he makes at the desk, none of them the name."""
        self._set(stage="noise", round=0)
        self.say("Now make some noise for ten seconds, without talking. Rub the microphone, tap the desk, type, roll your chair.")
        audio = self._clean_take(NOISE_S)
        wake.write_wav(wake.SAMPLES / "noise" / f"{time.strftime('%Y%m%d-%H%M%S')}.wav", audio)

    def _listen_along(self, sentence):
        """An on_block hook that transcribes what has been said so far, every HEAR_EVERY_S."""
        from .ears import transcribe

        busy = threading.Event()
        last = [0.0]
        # Only the newest sentence may write: a slow transcription of the last one is dropped.
        self._hearing = gen = object()

        def publish(text):
            if self._hearing is gen:
                self._set(heard=text, marks=align(sentence, text) if sentence else [])

        def hear(blocks):
            if busy.is_set() or time.monotonic() - last[0] < HEAR_EVERY_S:
                return
            busy.set()
            last[0] = time.monotonic()
            audio = np.concatenate(blocks)

            def work():
                try:
                    publish(transcribe(audio, self.cfg))
                except Exception as error:
                    log.debug("listen along: %s", error)
                finally:
                    busy.clear()

            threading.Thread(target=work, daemon=True).start()

        def final(audio):
            # The last word lands after the last check: hear the whole take once more.
            deadline = time.monotonic() + 3
            while busy.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                publish(transcribe(audio, self.cfg))
            except Exception as error:
                log.debug("listen along: %s", error)

        return hear, final

    def record_rest(self):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._set(stage="speech", round=0, group="")
        self.say("Now read each sentence on screen out loud, at your normal pace, after the tick. The words light up as I hear them.")
        for i, line in enumerate(pick_sentences()):
            self._set(passage=line, prompt="sentence", round=i + 1, heard="", marks=[""] * len(line.split()))
            self.cancel.wait(1.5)
            hear, final = self._listen_along(line)
            audio = self._clean_take(PASSAGE_S, hear)
            final(audio)
            wake.write_wav(wake.SAMPLES / "speech" / f"{stamp}-{i}.wav", audio)
            self.cancel.wait(1.0)
        import random

        topic = random.choice(FREE_TOPICS)
        self._set(passage=f"Talk about {topic}. Anything at all, for ten seconds.", prompt="free", round=3,
                  heard="", marks=[])
        self.say(f"Now just talk for ten seconds, about {topic}. Anything at all.")
        hear, final = self._listen_along("")
        audio = self._clean_take(ROOM_S, hear)
        final(audio)
        wake.write_wav(wake.SAMPLES / "speech" / f"{stamp}-free.wav", audio)
        self._set(stage="room", passage="", prompt="", round=0)
        self.say("Last one. Stay quiet for ten seconds while I listen to the room.")
        audio = self._clean_take(ROOM_S)
        wake.write_wav(wake.SAMPLES / "room" / f"{stamp}.wav", audio)
        chime.play("done")

    def run_learning(self):
        self._set(stage="learning", progress=0.0)
        self.say("Give me a minute while I learn it.")
        ticks = 0
        for event in self.learn(self.cancel):
            if "log" in event:
                line = event["log"].strip()
                self._append("log", line)
                if line.startswith("step ") or line.startswith("calibrating"):
                    ticks += 1
                    self._set(progress=min(ticks / LEARN_TICKS, 0.98))
            if "metrics" in event:
                self._set(metrics=event["metrics"], progress=1.0)
            if "error" in event:
                raise RuntimeError(event["error"])
        self._check()
        metrics = self.state["metrics"]
        if not metrics:
            raise RuntimeError("The learning ended without a model.")
        if metrics["validation_fires"]:
            self.say("I learned it, but I still woke on other sounds. More takes would help.")
        elif metrics["held_out_recall_clean"] < 0.8:
            self.say("I learned it, but I missed some of your own takes. A few more rounds would help.")
        else:
            self.say("Done.")
        return metrics

    def try_it(self):
        from .ears import speaker_ok

        self._set(stage="trying")
        detector = wake.Detector()
        self.say("Say my name whenever you like, and I'll chime when I hear it. Three tries.")
        self.mic.drain()
        end, history = time.monotonic() + TRY_S, []
        while len(self.state["tries"]) < TRIES and time.monotonic() < end:
            self._check()
            try:
                _, block = self.mic.read(timeout=2.0)
            except queue.Empty:
                continue
            history = (history + [block])[-20:]
            woke = detector.feed(wake.to_int16(block))
            if self.live is not None:
                self.live.score(detector.last, detector.threshold)
            if not woke:
                continue
            ok, voice = speaker_ok(np.concatenate(history), self.cfg)
            chime.play("listen" if ok else "never")
            self._append("tries", {"score": round(detector.last, 3), "voice": voice, "ok": ok})
            detector.reset()
            self.mic.drain()
        heard = sum(1 for t in self.state["tries"] if t["ok"])
        self.say("That works. From now on, say my name, wait for the chime, and talk." if heard else
                 "I didn't hear it that time. Watch the meter while you say my name to see how close it gets.")

    # ------------------------------------------------------------ the run

    def _run(self):
        from . import speakers

        try:
            if not self.retrain:
                if self.fresh:
                    # Set aside, never deleted: a start over that gets stopped loses nothing.
                    old = wake.SAMPLES / "pos"
                    if old.exists() and any(old.iterdir()):
                        old.rename(wake.SAMPLES / f"pos.before-{time.strftime('%Y%m%d-%H%M%S')}")
                have = saved()["pos"]
                with speakers.Hush() as hush:
                    self._set(paused=list(hush.paused), stage="intro")
                    if hush.paused:
                        self.say("I've paused what was playing, so I only hear you. It comes back when we're done.")
                    self.say(
                        "I'm going to learn how you say my name, so I answer you and nobody else, "
                        "and never to a word that only sounds like it. It takes about three minutes."
                        if not have else
                        f"I already have {have} recordings of my name. Let's add more and learn again."
                    )
                    self.record_names()
                    self.record_not_name()
                    self.record_noise()
                    self.record_rest()
            self.run_learning()
            if not self.retrain:
                self.try_it()
            self._set(stage="done")
        except Stopped:
            self._set(stage="stopped", said="Stopped. Every take so far is kept.")
        except queue.Empty:
            self._set(stage="failed", error="No audio from the microphone. The takes so far are kept.")
        except Exception as error:
            log.exception("setup failed")
            self._set(stage="failed", error=str(error))
        finally:
            self._set(active=False, recording=False)


def learn_in_scope(cancel):
    """Runs `python -m voice.ears learn` in a capped scope and yields its JSON events."""
    command = [
        "systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=8000M", "-p", "MemorySwapMax=0",
        sys.executable, "-m", "voice.ears", "learn",
    ]
    proc = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    def stopper():
        while proc.poll() is None:
            if cancel.wait(0.5):
                proc.kill()
                return

    threading.Thread(target=stopper, daemon=True).start()
    try:
        for line in proc.stdout:
            try:
                yield json.loads(line)
            except ValueError:
                continue
    finally:
        proc.wait()
    if proc.returncode and not cancel.is_set():
        yield {"error": f"The learning stopped with exit code {proc.returncode}."}


def learn_main():
    """`python -m voice.ears learn`: train from the saved takes, one JSON event per line."""
    from .config import load_config

    def emit(event):
        print(json.dumps(event), flush=True)

    try:
        wake.synth_confusables(load_config()["piper_dir"], progress=lambda s: emit({"log": s}))
        metrics = wake.train(progress=lambda s: emit({"log": s}))
        emit({"metrics": metrics})
        return 0
    except Exception as error:
        emit({"error": str(error)})
        return 1
