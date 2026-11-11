"""The setup session and the control panel API, with the mic, voice and learning faked."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import numpy as np

from voice import ears_api, ears_train, wake


class WordMic:
    """Each take hears one short word in the middle of silence."""

    def __init__(self, word_s=0.5, loud=0.3):
        self.word_s, self.loud = word_s, loud
        self.n = 0

    def read(self, timeout=1.0):
        self.n += 1
        block = np.zeros(wake.CHUNK, dtype=np.float32)
        phase = self.n % 33
        if 10 <= phase < 10 + int(self.word_s / 0.08):
            block += self.loud * np.sin(np.arange(wake.CHUNK) / 3.0).astype(np.float32)
        # Stamped a second ahead: a fake mic answers at once, and a real block always
        # arrives after the tick that started the take.
        return time.monotonic() + 1.0, block

    def drain(self):
        # Every take starts at a drain, so the word sits in the middle of each take.
        self.n = 0


class QuietSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text, cfg, interruptible=False):
        self.said.append(text)
        return ("done", 0.0, 0.0)

    def stop_now(self):
        return False


class Settings:
    def get(self):
        return {}


METRICS = {"validation_fires": 0, "held_out_recall_clean": 1.0, "threshold": 0.8}


def fake_learn(cancel):
    for i in range(16):
        yield {"log": f"  step {i}/4"}
    yield {"log": "calibrating on 11 hours it never heard"}
    yield {"metrics": METRICS}


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_samples = wake.SAMPLES
        wake.SAMPLES = Path(self.tmp.name)
        patches = [
            mock.patch.object(ears_train.chime, "play", return_value=mock.Mock(wait=lambda: None)),
            mock.patch.object(ears_train, "PASSAGE_S", 0.4),
            mock.patch.object(ears_train, "ROOM_S", 0.4),
            mock.patch("voice.speakers._playerctl", return_value=""),
            mock.patch.object(ears_train, "MOVE_S", 0.05),
            mock.patch("voice.ears.transcribe", return_value="the words he heard"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # A real `cancel.wait(0.35)` between takes would make this slow.
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        wake.SAMPLES = self.saved_samples

    def session(self, **kw):
        kw.setdefault("learn", fake_learn)
        return ears_train.Session({}, WordMic(), Settings(), speaker=QuietSpeaker(), **kw)

    def test_the_whole_setup_records_learns_and_ends(self):
        s = self.session()
        with mock.patch.object(ears_train.Session, "try_it", lambda self: self._set(stage="trying")):
            s.start().thread.join(30)
        state = s.snapshot()
        self.assertEqual(state["stage"], "done", state["error"])
        self.assertFalse(state["active"])
        self.assertEqual(sum(1 for t in state["takes"] if t["ok"]), 20)
        self.assertEqual(len(list((wake.SAMPLES / "pos").glob("*.wav"))), 20)
        # Two sentences and the free talk; near words in his voice; the noise; the room.
        self.assertEqual(len(list((wake.SAMPLES / "speech").glob("*.wav"))), 3)
        self.assertEqual(len(list((wake.SAMPLES / "notname").glob("*.wav"))), ears_train.NOT_NAME_EACH)
        self.assertEqual(state["notname"][0]["word"], "Diplo")
        self.assertEqual(len(list((wake.SAMPLES / "noise").glob("*.wav"))), 1)
        self.assertEqual(len(list((wake.SAMPLES / "room").glob("*.wav"))), 1)
        self.assertEqual(state["metrics"], METRICS)
        self.assertEqual(state["progress"], 1.0)
        self.assertEqual({t["place"] for t in state["takes"]}, {"desk", "bed", "far", "anywhere"})
        self.assertEqual(len(state["takes"][0]["envelope"]), 64)

    def test_sentences_do_not_repeat_until_the_pool_runs_out(self):
        seen = []
        for _ in range(len(ears_train.SENTENCES) // 2):
            seen += ears_train.pick_sentences()
        self.assertEqual(sorted(seen), sorted(ears_train.SENTENCES))

    def test_skip_leaves_out_the_rest_of_a_place(self):
        s = self.session(places=["bed", "far"])
        s.start()
        while not s.snapshot()["takes"]:
            time.sleep(0.01)
        s.skip_place()
        while s.snapshot()["group"] != "far" and s.snapshot()["active"]:
            time.sleep(0.01)
        s.stop()
        s.thread.join(10)
        bed = [t for t in s.snapshot()["takes"] if t["place"] == "bed"]
        self.assertLess(len(bed), 5)

    def test_stop_keeps_the_takes_so_far(self):
        s = self.session()
        s.start()
        while len(s.snapshot()["takes"]) < 3:
            time.sleep(0.01)
        s.stop()
        s.thread.join(10)
        state = s.snapshot()
        self.assertEqual(state["stage"], "stopped")
        kept = len(list((wake.SAMPLES / "pos").glob("*.wav")))
        self.assertGreaterEqual(kept, 3)

    def test_new_takes_never_overwrite_old_ones(self):
        pos = wake.SAMPLES / "pos"
        pos.mkdir(parents=True)
        for n in (0, 1, 7):
            wake.write_wav(pos / f"{n:03d}.wav", np.zeros(100, dtype=np.float32))
        s = self.session()
        s.start()
        while not s.snapshot()["takes"]:
            time.sleep(0.01)
        s.stop()
        s.thread.join(10)
        self.assertEqual(s.snapshot()["takes"][0]["file"], "008.wav")

    def test_sound_from_the_computer_ends_after_three_takes(self):
        tap = mock.Mock(level=mock.Mock(return_value=0.2))
        s = self.session(tap=tap)
        s.start().thread.join(10)
        state = s.snapshot()
        self.assertEqual(state["stage"], "failed")
        self.assertIn("kept playing sound", state["error"])
        self.assertEqual(s.speaker.said.count(ears_train.BLEED_LINE), ears_train.BLEED_TRIES)

    def test_start_over_sets_the_old_takes_aside(self):
        pos = wake.SAMPLES / "pos"
        pos.mkdir(parents=True)
        wake.write_wav(pos / "000.wav", np.zeros(100, dtype=np.float32))
        s = self.session(fresh=True)
        s.start()
        while not s.snapshot()["takes"]:
            time.sleep(0.01)
        s.stop()
        s.thread.join(10)
        aside = list(wake.SAMPLES.glob("pos.before-*/000.wav"))
        self.assertEqual(len(aside), 1)
        self.assertEqual(s.snapshot()["takes"][0]["file"], "000.wav")

    def test_retrain_skips_the_recording(self):
        s = self.session(retrain=True)
        s.start().thread.join(10)
        state = s.snapshot()
        self.assertEqual(state["stage"], "done")
        self.assertEqual(state["takes"], [])

    def test_a_failed_learning_says_why(self):
        def broken(cancel):
            yield {"error": "only 3 usable recordings of the name, 10 or more needed"}

        s = self.session(retrain=True, learn=broken)
        s.start().thread.join(10)
        self.assertEqual(s.snapshot()["stage"], "failed")
        self.assertIn("10 or more", s.snapshot()["error"])


class TestJudge(unittest.TestCase):
    """Every redo names its real problem."""

    def take(self, *bursts, seconds=2.6):
        audio = np.random.default_rng(0).normal(0, 0.002, int(seconds * wake.RATE)).astype(np.float32)
        for start, length in bursts:
            a = int(start * wake.RATE)
            n = min(int(length * wake.RATE), len(audio) - a)
            audio[a:a + n] += 0.3 * np.sin(np.arange(n) / 3)
        return audio

    def code(self, audio, typical=None):
        return wake.judge(audio, typical)["code"]

    def test_verdicts(self):
        self.assertEqual(self.code(self.take()), "nothing")
        self.assertEqual(self.code(self.take((0.0, 0.5))), "early")
        self.assertEqual(self.code(self.take((2.2, 0.5))), "late")
        self.assertEqual(self.code(self.take((1.0, 0.1))), "short")
        self.assertEqual(self.code(self.take((0.4, 0.5), (1.6, 0.5))), "two")
        self.assertEqual(self.code(self.take((0.3, 1.8)), typical=0.6), "long")
        self.assertEqual(self.code(self.take((0.6, 0.6))), "ok")

    def test_a_click_after_the_name_does_not_make_it_long(self):
        verdict = wake.judge(self.take((0.5, 0.6), (2.0, 0.06)))
        self.assertEqual(verdict["code"], "ok")
        self.assertAlmostEqual(verdict["seconds"], 0.6, delta=0.1)

    def test_loud_enough_to_clip_is_fine(self):
        audio = self.take()
        audio[8000:16000] = np.clip(3.0 * np.sin(np.arange(8000) / 3), -1, 1)
        self.assertTrue(wake.judge(audio)["ok"])

    def test_the_words_light_up_in_order(self):
        marks = ears_train.align("Give me a minute while I learn it.", "give me a minute I learn it")
        self.assertEqual(marks, ["hit", "hit", "hit", "hit", "miss", "hit", "hit", "hit"])
        self.assertEqual(ears_train.align("Give me a minute.", "give"), ["hit", "", "", ""])

class TestApi(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, value in (("WAKE_CLIPS", Path(tmp.name)), ("WAKE_LOG", Path(tmp.name) / "log.json")):
            patch = mock.patch.object(ears_api, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.live = ears_api.Live()
        self.started = []

        def start(fresh, retrain, places=None):
            session = mock.Mock(active=True)
            session.snapshot.return_value = {"stage": "intro"}
            self.started.append((fresh, retrain))
            return session

        self.on = [True]
        self.controller = ears_api.Controller(
            self.live, start, lambda: self.on[0], lambda v: self.on.__setitem__(0, v),
            lambda: 0.05, lambda: 0.0)
        self.server = ears_api.serve(self.controller, port=0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()

    def call(self, path, body=None, header=True):
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Diablo-Ears"] = "1"
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_status_carries_the_live_meter(self):
        self.live.score(0.4, 0.8)
        self.live.wake(0.9, 0.81, "answered")
        code, body = self.call("/v1/ears")
        self.assertEqual(code, 200)
        self.assertEqual(body["live"]["threshold"], 0.8)
        code, levels = self.call("/v1/ears/live")
        self.assertEqual((code, levels["mic"], levels["score"]), (200, 0.05, 0.4))
        self.assertEqual(body["live"]["threshold"], 0.8)
        self.assertEqual(body["wakes"][0]["outcome"], "answered")
        self.assertEqual([p["id"] for p in body["places"]], ["desk", "bed", "far", "anywhere"])

    def test_posts_need_the_header(self):
        code, _ = self.call("/v1/ears/train", {}, header=False)
        self.assertEqual(code, 403)
        self.assertEqual(self.started, [])

    def test_one_session_at_a_time(self):
        self.assertEqual(self.call("/v1/ears/train", {"fresh": True})[0], 200)
        self.assertEqual(self.started, [(True, False)])
        self.assertEqual(self.call("/v1/ears/train", {})[0], 409)

    def test_no_setup_during_a_turn(self):
        # "waking" covers the gap between the wake word firing and the turn starting.
        for state in ("waking", "listening", "speaking"):
            self.live.set_state(state)
            self.assertEqual(self.call("/v1/ears/train", {})[0], 409, state)
        self.assertEqual(self.started, [])

    def test_a_foreign_host_is_refused(self):
        req = urllib.request.Request(self.base + "/v1/ears/train", data=b"{}", method="POST",
                                     headers={"X-Diablo-Ears": "1", "Host": "evil.example:8758"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.started, [])


    def test_listening_switch(self):
        code, body = self.call("/v1/ears/listen", {"on": False})
        self.assertEqual((code, body["listening"]), (200, False))


if __name__ == "__main__":
    unittest.main()
