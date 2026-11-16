"""Whose talk is it? Only Marcus's keeps a turn open.

record() in voice/ears.py ends a turn after a stretch of quiet, and any voice used to break
the quiet: with people talking in the room a turn stayed open until ears.turn_max_s and
Whisper wrote their sentences into the request. A Judge listens beside record(). Every
STRIDE of new speech it scores the last WINDOW of speech against his voice on a thread of its
own, so the mic loop never waits, and each window votes on the speech in it. Speech the votes
call someone else's counts as quiet for the end of the turn and is cut before speech to text.

    ears.own_voice_end   1 judges the voice, 0 counts any speech, as before (default 1)

Unknown is his: a window the voiceprint could not score, a tie between windows, a stretch
too short to judge. The one exception is speech that runs straight on from someone else's,
with no pause between: it is theirs until a window says otherwise, or a stranger talking
without a break would hold the turn open forever. So that this never cuts him off when he
picks up again over them, quiet that comes from other people's talk ends a turn LAG_S later
than real quiet, which is about how long his voice takes to win a window back.
"""

import logging
import threading

import numpy as np

log = logging.getLogger("diablo.ownvoice")

RATE = 16000
FRAME = 512
STEP_S = FRAME / RATE
# 1.6 s of speech, the length the talk print was made of, and a new window every 0.64 s of it.
WINDOW = 50
STRIDE = 20
# Quiet longer than this between two speech frames starts a new stretch.
RUN_GAP = 8
LAG_S = 1.6
# Speech kept on each side of a cut next to his, cuts shorter than MIN_CUT are not made, and
# a cut leaves a short silence so the words on either side stay apart.
PAD = 5
MIN_CUT = 16
JOIN_S = 0.15
HIS, OTHER = 1, -1

# A judge's thread with nothing to do for this long ends; the next window starts another.
IDLE_S = 10.0


def enabled(cfg):
    """ears.own_voice_end, read like the other end of turn settings, and off with the voice
    check (ears_speaker_check)."""
    from . import endturn

    if str(cfg.get("ears_speaker_check", "1")).strip().lower() in endturn.OFF:
        return False
    return endturn.Policy.from_cfg(cfg).own_voice


class Judge:
    """Which speech frames of one recording are his. Feed every 512 sample frame in order.

    verdict(clip, since, until) is True for his voice, False for someone else's and None
    when it cannot tell, where since and until are the mic times the window spans. It runs
    on the judge's own thread; background=False runs it in feed() instead, for replays."""

    def __init__(self, verdict, background=True):
        self.verdict, self.background = verdict, background
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self.pos, self.chunks, self.at, self.run = [], [], [], []
        self.votes, self.label = [], []
        self.last_his, self.his_count = -1, 0
        self.spans, self.version = [], 0
        self.next_window = WINDOW
        self.pending = None
        self.closed = False
        self.thread = None
        self.windows = self.other_windows = 0

    def feed(self, index, chunk, speech, at):
        """One frame. True when it is speech that counts as his right now."""
        if not speech:
            return False
        window = None
        with self.lock:
            new_run = not self.pos or index - self.pos[-1] > RUN_GAP
            run = (self.run[-1] if self.run else 0) + (1 if new_run else 0)
            label = OTHER if not new_run and self.label[-1] == OTHER else HIS
            self.pos.append(index)
            self.chunks.append(np.asarray(chunk, dtype=np.float32))
            self.at.append(at)
            self.run.append(run)
            self.votes.append([0, 0])
            self.label.append(label)
            if label == HIS:
                self.last_his = len(self.pos) - 1
                self.his_count += 1
            if len(self.pos) > 1 and self._cut(len(self.pos) - 2):
                # A cut that ran to the end now has speech after it, so it keeps PAD there.
                self._refresh()
            if len(self.pos) >= self.next_window:
                start = len(self.pos) - WINDOW
                window = (start, len(self.pos), np.concatenate(self.chunks[start:]), self.at[start], at)
                self.next_window += STRIDE
                if self.background:
                    self.pending, window = window, None
                    self._start()
        if window is not None:
            self._judge(window)
        with self.lock:
            return self.label[-1] == HIS

    def _start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name="own-voice", daemon=True)
            self.thread.start()
        else:
            self.wake.notify()

    def _work(self):
        while True:
            with self.lock:
                if self.pending is None and not self.closed:
                    self.wake.wait(IDLE_S)
                if self.closed or self.pending is None:
                    self.thread = None
                    return
                window, self.pending = self.pending, None
            self._judge(window)

    def _judge(self, window):
        start, end, clip, since, until = window
        try:
            his = self.verdict(clip, since, until)
        except Exception as error:
            log.debug("own voice window failed, counted as his: %s", error)
            his = None
        self._vote(start, end, his is None or bool(his))

    def _vote(self, start, end, his):
        with self.lock:
            if self.closed:
                return
            self.windows += 1
            self.other_windows += 0 if his else 1
            for p in range(start, end):
                self.votes[p][0 if his else 1] += 1
            changed = False
            for p in range(start, len(self.pos)):
                for_his, for_other = self.votes[p]
                if for_his or for_other:
                    label = OTHER if for_other > for_his else HIS
                else:
                    label = OTHER if p and self.run[p] == self.run[p - 1] and self.label[p - 1] == OTHER else HIS
                if label != self.label[p]:
                    self.his_count += 1 if label == HIS else -1
                    self.label[p] = label
                    changed = True
            if changed:
                self.last_his = next((p for p in range(len(self.pos) - 1, -1, -1) if self.label[p] == HIS), -1)
            self._refresh()

    def _cut(self, p):
        """Whether speech frame p may be cut: two windows called it someone else's and none his.
        Talk that only runs on from theirs, or that one window has judged so far, stays: his
        reply straight after a stranger sits in the newest window, which is mostly theirs."""
        return self.votes[p][0] == 0 and self.votes[p][1] >= 2

    def _refresh(self):
        """The cut spans again; version counts each change, so record() knows the words it
        read were of other audio. Called with the lock held."""
        spans, i, n = [], 0, len(self.pos)
        while i < n:
            if not self._cut(i):
                i += 1
                continue
            j = i
            while j + 1 < n and self._cut(j + 1):
                j += 1
            start = self.pos[i] + (PAD if i > 0 else 0)
            end = self.pos[j] + 1 - (PAD if j + 1 < n else 0)
            if end - start >= MIN_CUT:
                spans.append((start * FRAME, end * FRAME))
            i = j + 1
        if spans != self.spans:
            self.spans = spans
            self.version += 1

    def close(self):
        with self.lock:
            self.closed = True
            self.wake.notify()

    def his_end(self):
        """The frame index just after his last speech, 0 when he has not spoken."""
        with self.lock:
            return self.pos[self.last_his] + 1 if self.last_his >= 0 else 0

    def his_last_at(self):
        """The mic time of his last speech, or None."""
        with self.lock:
            return self.at[self.last_his] if self.last_his >= 0 else None

    def his_seconds(self):
        with self.lock:
            return self.his_count * STEP_S

    def quiet(self, frames, raw):
        """Quiet that counts for the end of the turn after `frames` frames, given `raw`, the
        quiet since anyone spoke."""
        own = (frames - self.his_end()) * STEP_S
        if own <= raw + 1e-9:
            return raw
        return max(raw, own - LAG_S)

    def cuts(self):
        """(start, end) sample spans of other people's talk: speech two windows called someone
        else's and no window called his, less PAD next to any other speech."""
        with self.lock:
            return list(self.spans)

    def trim(self, audio):
        """audio without the spans of other people's talk."""
        spans = self.cuts()
        if not spans:
            return audio
        gap = np.zeros(int(JOIN_S * RATE), dtype=np.float32)
        parts, at = [], 0
        for start, end in spans:
            if start > at:
                parts.append(audio[at:start])
            at = end
        if at < len(audio):
            parts.append(audio[at:])
        out = []
        for part in parts:
            if out:
                out.append(gap)
            out.append(part)
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def cut_seconds(self):
        return sum(end - start for start, end in self.cuts()) / RATE


# Under talk_min, less any slack, less this, a window is clearly someone else.
MARGIN = 0.05
# A window this close to his print is his for certain, and his windows in the turn make a print
# of their own: the same mic, room and music as the rest of it. Measured on 1.6 s windows of
# held out dictations mixed with LibriSpeech strangers in a room: strangers scored 0.50 to 0.70
# against talk.npy and his own 0.66 to 0.90, but against his windows in the same turn his sat
# at 0.78 and over, and strangers at 0.66 and under. Once there is such a print, a window is
# someone else's only when it is under TURN_MIN against it and under talk_min plus TALK_OVER
# against talk.npy: with talk_min at 0.62, 97% of stranger windows were, and 2% of windows
# that were half him or more.
SURE = 0.75
TURN_MIN = 0.68
TALK_OVER = 0.06


class Voice:
    """The verdict on one window. Once the turn holds a window SURE to be him, later windows
    are held against those windows, else against talk.npy and talk_min. slack is taken off the
    print's bar for the whole recording, and sound(since, until), when given, is the slack for
    sound the speakers played during the window (ears.sound_slack): music drags him down. With
    either and no window of his yet, there is no verdict."""

    def __init__(self, minimum, talk, slack=0.0, sound=None):
        from . import voiceid, wake

        self.wake, self.voiceid = wake, voiceid
        self.minimum, self.slack, self.sound = minimum, slack, sound
        self.talk = np.asarray(talk, dtype=np.float32)
        self.mine = None

    def __call__(self, clip, since, until):
        if self.voiceid.busy():
            # Never queue behind another check: the request's own voice check comes next.
            return None
        emb = self.wake.talk_embedding(clip)
        if emb is None:
            return None
        score = float(self.talk @ emb)
        if score >= SURE:
            self.mine = emb if self.mine is None else self.mine + emb
            return True
        sound = self.sound(since, until) if self.sound is not None else 0.0
        slack = max(self.slack, sound)
        if self.mine is None:
            # Under music his talk scored 0.43 against talk.npy, and a song alone 0.44 to 0.56:
            # with sound playing the print cannot tell them apart.
            return None if slack else bool(score >= self.minimum - MARGIN)
        # Both have to say someone else: a window that is half him scores low against his
        # windows but stays high against talk.npy.
        near = float(self.mine @ emb) / (np.linalg.norm(self.mine) + 1e-9)
        return bool(near >= TURN_MIN - sound or score >= min(self.minimum + TALK_OVER, SURE) - slack)


def for_turn(cfg, slack=0.0, sound=None):
    """A Judge for one recording, or None when it is off or there is no talk print yet."""
    if not enabled(cfg):
        return None
    from . import wake

    minimum = wake.meta().get("talk_min")
    if minimum is None or not wake.TALK_PRINT.exists():
        return None
    try:
        talk = np.load(wake.TALK_PRINT)
    except (OSError, ValueError):
        return None
    return Judge(Voice(float(minimum), talk, slack=slack, sound=sound))
