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
# A turn of the burst still thinking this long after the newest one is in is left behind:
# the rest is spoken without it, and it speaks on its own once it lands.
STRAGGLE_S = 30.0
MERGE_TIMEOUT_S = 25.0
FIRST_SENTENCE_CHARS = 160


def quiet_s(cfg):
    try:
        return min(max(float(cfg.get("ears.burst_quiet_s") or QUIET_S), 0.0), 15.0)
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
        ahead of the answer only while the job is alone in its burst."""
        now = self.clock()
        job = {"done": threading.Event(), "words": "", "tl": tl, "said": said, "dropped": False, "early": [],
               "started": now, "finished": None, "n": next(self._serial)}

        def say(words):
            with self.lock:
                if not job["dropped"]:
                    job["early"].append(words)

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
                self.jobs = self.merging["jobs"] + self.jobs
                self.merging = None
            self.jobs.append(job)
            self.last = max(self.last, now)
        if run:
            threading.Thread(target=go, daemon=True, name="answer").start()
        return job

    def finish(self, job):
        with self.lock:
            job["finished"] = self.clock()
            self.last = max(self.last, job["finished"])
        job["done"].set()

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
            if self.merging is not None or len(live) != 1 or live[0]["done"].is_set() or not live[0]["early"]:
                return []
            job = live[0]
            early, job["early"] = job["early"], []
            return [(job, early)]

    def keep_early(self, job, lines):
        """Puts back the first lines a cut took but never said."""
        with self.lock:
            if not job["dropped"] and job in self.jobs:
                job["early"] = list(lines) + job["early"]

    def keep(self, jobs):
        """Puts back a lone job whose first line he cut off with the name, so its answer is not
        lost: what he says next joins it, and one reply covers both."""
        with self.lock:
            back = [job for job in jobs if not job["dropped"] and job not in self.jobs]
            self.jobs = back + self.jobs

    def _in(self, now):
        """The jobs whose answers are in and make up the burst, or []: the newest job must be
        in, and an earlier one still thinking waits until it has been left behind."""
        live = self._live()
        if not live or not live[-1]["done"].is_set():
            return []
        out = [job for job in live if job["done"].is_set()]
        if len(out) < len(live) and now - (live[-1]["finished"] or now) < self.straggle_s:
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
        return len(live) == 1 and bool(live[0]["early"])

    def take(self):
        """(jobs, words) for the burst to speak now, taken off the list; ([], None) when none is
        ready. A lone job comes with words None and says its own lines; a burst of more comes
        with its merged reply, once he has been quiet for quiet_s after its newest turn."""
        with self.lock:
            self._advance()
            now = self.clock()
            if self.merging is not None:
                if not self._speakable(now):
                    return [], None
                holder, self.merging = self.merging, None
                return holder["jobs"], holder["words"]
            live = self._live()
            if len(live) == 1 and live[0]["done"].is_set():
                self.jobs.remove(live[0])
                return live, None
            return [], None

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
    parts, seen = [newest], {newest}
    for turn in reversed(turns[:-1]):
        line = first_sentence(turn["reply"])
        if line and line not in seen and line not in newest:
            parts.append(line)
            seen.add(line)
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
