"""Spoken turns in quick succession, answered as one.

Marcus often says several things in a row. Each turn still goes to the hub on its own and
its reply lands in the chat, but the ears hold the spoken side: every turn started while an
earlier one is still thinking, or its answer is still unsaid, joins that turn's burst. Once
the newest turn is in and he has been quiet for QUIET_S, the burst is spoken as one reply,
the answers merged by the hub (POST /v1/voice/merge) newest first. A burst of one is spoken
the moment it is in, as before, and only a lone turn says its first line while it works.
"""

import itertools
import logging
import threading
import time

import httpx

log = logging.getLogger("diablo.ears.burst")

# After the newest turn of a burst is in, the burst waits this long with no new turn or
# answer before it speaks, so a next "Diablo, and also..." still joins it.
QUIET_S = 2.0
# With answers in but a turn still thinking, the burst waits this long after the last turn
# started or landed. Then the answers that are in are spoken, and the turns still thinking are
# left behind: each speaks once it lands, or joins the next burst.
STRAGGLE_S = 6.0
MERGE_TIMEOUT_S = 25.0
FIRST_SENTENCE_CHARS = 160
FALLBACK_CHARS = 900


def quiet_s(cfg):
    value = cfg.get("ears.burst_quiet_s") if hasattr(cfg, "get") else None
    if value is None or value == "":
        return QUIET_S
    try:
        return min(max(float(value), 0.0), 15.0)
    except (TypeError, ValueError, AttributeError):
        return QUIET_S


class Burst:
    """The jobs still thinking on the hub, or finished and unsaid, in the order they started.

    Each job runs its hub call on a thread. The main loop asks take_early() for a lone job's
    first lines and take() for a burst that is ready to be spoken. A burst of more than one is
    merged on a thread of its own as soon as its answers are in, so the ears keep listening
    meanwhile; a turn that starts during the merge puts the burst back and joins it."""

    def __init__(self, clock=time.monotonic, merger=None):
        self.jobs = []
        self.lock = threading.Lock()
        self.clock = clock
        self.last = 0.0
        self.quiet_s = QUIET_S
        self.straggle_s = STRAGGLE_S
        self.merger = merger or (lambda turns: (fallback(turns), "fallback"))
        self.merging = None
        self._serial = itertools.count(1)

    def _live(self):
        return [job for job in self.jobs if not job["dropped"]]

    def start(self, work, tl, said, run=True):
        """work(say) runs the hub call; each line it hands to say is a first line, spoken
        ahead of the answer only while the job is alone in its burst. say.working() says a tool
        has started: the reply being spoken ends once the lines in hand are said, and the lines
        written after the work make a reply of their own."""
        now = self.clock()
        job = {"done": threading.Event(), "words": "", "tl": tl, "said": said, "dropped": False, "early": [],
               "working": False, "started": now, "finished": None, "n": next(self._serial), "stirred": threading.Event()}

        def say(words):
            with self.lock:
                if not job["dropped"]:
                    job["early"].append(words)
                job["working"] = False
            job["stirred"].set()

        def working():
            with self.lock:
                job["working"] = True
            job["stirred"].set()

        say.working = working

        def go():
            try:
                job["words"] = work(say)
            except Exception:
                log.exception("background answer failed")
                job["words"] = "That one failed on my side."
            finally:
                self.finish(job)

        with self.lock:
            if self.merging is not None:
                # He went on talking while the last burst was being merged: it is not over yet.
                self.jobs = sorted(self.merging["jobs"] + self.jobs, key=lambda j: j["n"])
                self.merging = None
            self.jobs.append(job)
            self.last = max(self.last, now)
            live = self._live()
            if len(live) > 1:
                for each in live:
                    each["grouped"] = True
        if run:
            threading.Thread(target=go, daemon=True, name="answer").start()
        return job

    def finish(self, job):
        with self.lock:
            job["finished"] = self.clock()
            self.last = max(self.last, job["finished"])
        job["done"].set()
        job["stirred"].set()

    def _wait_stir(self, job, seconds):
        job["stirred"].wait(max(seconds, 0.0))
        job["stirred"].clear()

    def settle(self, job, wait, grace):
        """True once job is done within wait. False when wait runs out, or as soon as a first
        line of it has waited grace without the job finishing: that line is then said at once
        instead of after the whole wait."""
        deadline = time.monotonic() + wait
        while not job["done"].is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            with self.lock:
                stirred = bool(job["early"])
            if stirred:
                return job["done"].wait(min(grace, left))
            self._wait_stir(job, left)
        return True

    def pull_early(self, job, wait):
        """The next first line of a lone job, waiting up to wait seconds for one to come. None
        when none came, a tool started after the last one, the job finished with none left, or it
        was dropped or joined a burst."""
        deadline = time.monotonic() + wait
        while True:
            with self.lock:
                if job["dropped"] or job.get("grouped"):
                    return None
                if job["early"]:
                    return job["early"].pop(0)
                if job["working"]:
                    return None
            left = deadline - time.monotonic()
            if job["done"].is_set() or left <= 0:
                return None
            self._wait_stir(job, left)

    def alone(self, job):
        with self.lock:
            return self.merging is None and self._live() == [job]

    def forget(self, job):
        with self.lock:
            if job in self.jobs:
                self.jobs.remove(job)

    def take_early(self):
        """[(job, lines)]: the first lines of a lone job still thinking. A job in a burst of
        more than one says none; its lines go into the merged reply instead."""
        with self.lock:
            live = self._live()
            if (self.merging is not None or len(live) != 1 or live[0]["done"].is_set() or not live[0]["early"]
                    or live[0].get("grouped")):
                return []
            job = live[0]
            early, job["early"] = job["early"], []
            return [(job, early)]

    def drop(self, job):
        """He cut the reply off with the name: nothing more of this job is said, not the lines
        queued behind the cut and not what it writes after it."""
        with self.lock:
            job["dropped"] = True
            job["early"] = []

    def drop_early(self, job):
        """Drops the first lines waiting their turn: he cut the reply off with the name, and
        what was queued behind the cut is never said. Lines written after the cut still count."""
        with self.lock:
            job["early"] = []

    def keep(self, jobs):
        """Puts back a lone job whose first line he cut off with the name, so its answer is not
        lost: what he says next joins it, and one reply covers both."""
        with self.lock:
            back = [job for job in jobs if not job["dropped"] and job not in self.jobs]
            self.jobs = back + self.jobs

    def _in(self, now):
        """The jobs whose answers are in and make up the burst, or []: all of them once every
        job is in, else those that are in once nothing started or landed for straggle_s."""
        live = self._live()
        out = [job for job in live if job["done"].is_set()]
        if not out or (len(out) < len(live) and now - self.last < self.straggle_s):
            return []
        return out

    def _merge(self, jobs):
        holder = {"jobs": jobs, "done": threading.Event(), "words": "", "via": ""}
        self.merging = holder
        for job in jobs:
            self.jobs.remove(job)
        turns = [{"heard": job["said"], "reply": answer_of(job)} for job in jobs]

        def go():
            try:
                holder["words"], holder["via"] = self.merger(turns)
            except Exception:
                log.exception("merging a burst failed")
                holder["words"], holder["via"] = fallback(turns), "fallback"
            finally:
                holder["done"].set()

        threading.Thread(target=go, daemon=True, name="merge").start()

    def _advance(self):
        """Call with the lock held: starts the merge of a burst whose answers are all in."""
        self.jobs = [job for job in self.jobs if not (job["dropped"] and job["done"].is_set())]
        if self.merging is not None:
            return
        found = self._in(self.clock())
        if len(found) > 1 or (found and len(found) < len(self._live())):
            self._merge(found)

    def _speakable(self, now):
        if self.merging is not None:
            return self.merging["done"].is_set() and now - self.last >= self.quiet_s
        live = self._live()
        if len(live) == 1 and live[0]["done"].is_set():
            return True
        return len(live) == 1 and bool(live[0]["early"]) and not live[0].get("grouped")

    def take(self):
        """(jobs, words) for the burst to speak now, taken off the list; ([], None) when none is
        ready. A lone job comes with words None and says its own lines; a burst of more comes
        with its merged reply, once he has been quiet for quiet_s after its newest turn."""
        with self.lock:
            self._advance()
            if not self._speakable(self.clock()):
                return [], None
            if self.merging is not None:
                holder, self.merging = self.merging, None
                return holder["jobs"], holder["words"]
            live = self._live()
            if not live[0]["done"].is_set():
                return [], None
            self.jobs.remove(live[0])
            return live, None

    def has_speech(self):
        """Whether the main loop has something to say now; a burst whose answers are all in
        starts merging here."""
        with self.lock:
            self._advance()
            return self._speakable(self.clock())

    def thinking(self):
        with self.lock:
            return sum(1 for job in self._live() if not job["done"].is_set())

    def size(self):
        with self.lock:
            return len(self._live()) + (len(self.merging["jobs"]) if self.merging is not None else 0)

    def drop_all(self):
        with self.lock:
            dropped = list(self.jobs) + (self.merging["jobs"] if self.merging is not None else [])
            self.merging = None
            for job in dropped:
                job["dropped"] = True
                job["early"] = []
            return len(dropped)


def answer_of(job):
    """Everything a job has to say that was not said yet: first lines left, then the answer."""
    return " ".join(w for w in list(job["early"]) + [job["words"]] if w and w.strip()).strip()


def first_sentence(text, limit=FIRST_SENTENCE_CHARS):
    text = " ".join(str(text or "").split())
    for at, char in enumerate(text):
        if char in ".?!" and (at + 1 == len(text) or text[at + 1] == " ") and at >= 10:
            return text[: at + 1]
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "."


def fallback(turns):
    """The burst as one line without the hub: the newest answer whole, then the first sentence
    of each earlier one, newest first, each said once."""
    turns = [t for t in turns if str(t.get("reply") or "").strip()]
    if not turns:
        return ""
    newest = turns[-1]["reply"].strip()
    parts, seen, size = [newest], {newest}, len(newest)
    for turn in reversed(turns[:-1]):
        line = first_sentence(turn["reply"])
        if not line or line in seen or line in newest:
            continue
        if size + len(line) > FALLBACK_CHARS:
            parts.append("The rest is in the chat.")
            break
        parts.append(line)
        seen.add(line)
        size += len(line) + 1
    return " ".join(parts)


def merge(cfg, turns):
    """(words, via) for [{"heard", "reply"}], oldest first: the hub's merged reply, or the
    fallback when the hub cannot make one."""
    turns = [t for t in turns if str(t.get("reply") or "").strip()]
    if not turns:
        return "", "empty"
    if len(turns) == 1:
        return turns[0]["reply"].strip(), "single"
    try:
        response = httpx.post(
            f"{cfg['hub_url'].rstrip('/')}/v1/voice/merge",
            headers={"X-Diablo-Token": cfg.get("hub_token", "")},
            json={"turns": turns},
            timeout=MERGE_TIMEOUT_S,
        )
        response.raise_for_status()
        found = response.json()
        say = str((found or {}).get("say") or "").strip()
        if say:
            return say, str(found.get("via") or "hub")
    except (httpx.HTTPError, ValueError, KeyError, AttributeError) as error:
        log.warning("hub merge failed: %s", error)
    return fallback(turns), "fallback"
