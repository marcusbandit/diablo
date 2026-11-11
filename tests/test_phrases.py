import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import phrases

TOTAL = sum(len(lines) for lines in phrases.BANKS.values())


def make_wav(seconds=0.1, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeSpeaker:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def _cloud_wav(self, text, cfg):
        self.calls.append(text)
        if text == self.fail_on:
            raise httpx.HTTPError("synth failed")
        return make_wav()


ARCHIE = "ef191366-f52f-447a-a398-ed8c0f2943a1"


def cfg_for(voice_id=ARCHIE, provider="cartesia"):
    return {"tts_voice_id": voice_id, "tts_provider": provider, "tts_model": "sonic-latest"}


class TestLines(unittest.TestCase):
    def test_ten_distinct_lines_without_dashes(self):
        self.assertEqual(len(phrases.ACKS), 10)
        self.assertEqual(len(set(phrases.ACKS)), 10)
        for line in phrases.ACKS:
            self.assertNotIn("-", line)
            self.assertNotIn(chr(0x2013), line)
            self.assertNotIn(chr(0x2014), line)
            self.assertTrue(line.isascii(), line)


class TestBank(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(phrases, "ROOT", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_countdown_bank_is_cached_beside_the_acknowledgements(self):
        speaker = FakeSpeaker()
        phrases.ensure(cfg_for(), speaker)
        folder = phrases.bank_dir(ARCHIE)
        self.assertTrue((folder / "countdown-00.wav").exists())
        self.assertIn("Three, two, one.", speaker.calls)
        text, wav = phrases.get(cfg_for(), "countdown")
        self.assertEqual(text, "Three, two, one.")
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIsNone(phrases.get(cfg_for(), "countdown", index=5))
        self.assertIsNone(phrases.get({}, "countdown"))

    def test_sample_is_long_enough_for_a_fast_talker(self):
        # Around 15 characters a second at his pace, so ten seconds needs 150 and more.
        self.assertGreater(len(phrases.SAMPLE[0]), 140)
        text, wav = phrases.get(cfg_for(), "sample") or (None, None)
        self.assertIsNone(text)
        phrases.ensure(cfg_for(), FakeSpeaker())
        text, wav = phrases.get(cfg_for(), "sample")
        self.assertEqual(text, phrases.SAMPLE[0])

    def test_ensure_writes_all_lines_once(self):
        speaker = FakeSpeaker()
        log = mock.Mock()
        self.assertEqual(phrases.ensure(cfg_for(), speaker, log), TOTAL)
        folder = phrases.bank_dir(ARCHIE)
        wavs = sorted(p.name for p in folder.glob("ack-*.wav"))
        self.assertEqual(wavs, [f"ack-{i:02d}.wav" for i in range(10)])
        manifest = json.loads((folder / "voice.json").read_text())
        self.assertEqual(manifest["voice_id"], ARCHIE)
        self.assertEqual(manifest["lines"], phrases.ACKS)
        self.assertEqual(manifest["tts_model"], "sonic-latest")
        log.info.assert_called_once()
        self.assertEqual(phrases.ensure(cfg_for(), speaker, log), 0)
        self.assertEqual(len(speaker.calls), TOTAL)
        self.assertFalse(list(folder.glob("*.tmp")))

    def test_each_voice_gets_its_own_folder(self):
        speaker = FakeSpeaker()
        phrases.ensure(cfg_for(), speaker)
        self.assertEqual(phrases.ensure(cfg_for("other-voice"), speaker), TOTAL)
        self.assertTrue((phrases.bank_dir(ARCHIE) / "ack-00.wav").exists())
        self.assertTrue((phrases.bank_dir("other-voice") / "ack-00.wav").exists())
        self.assertEqual(len(speaker.calls), TOTAL * 2)

    def test_pick_returns_cached_line_and_none_when_empty(self):
        self.assertIsNone(phrases.pick(cfg_for()))
        phrases.ensure(cfg_for(), FakeSpeaker())
        text, wav = phrases.pick(cfg_for())
        self.assertIn(text, phrases.ACKS)
        self.assertEqual(wav, make_wav())
        self.assertIsNone(phrases.pick(cfg_for("never-cached")))

    def test_pick_only_offers_lines_that_landed(self):
        phrases.ensure(cfg_for(), FakeSpeaker(fail_on=phrases.ACKS[3]))
        seen = {phrases.pick(cfg_for())[0] for _ in range(100)}
        self.assertNotIn(phrases.ACKS[3], seen)

    def test_no_voice_id_or_no_provider_does_nothing(self):
        speaker = FakeSpeaker()
        self.assertEqual(phrases.ensure(cfg_for(voice_id=""), speaker), 0)
        self.assertEqual(phrases.ensure(cfg_for(provider="none"), speaker), 0)
        self.assertEqual(phrases.ensure({}, speaker), 0)
        self.assertEqual(speaker.calls, [])
        self.assertFalse(list(Path(self.tmp.name).iterdir()))
        self.assertIsNone(phrases.pick(cfg_for(voice_id="")))

    def test_failed_fetch_skips_that_line_only(self):
        speaker = FakeSpeaker(fail_on=phrases.ACKS[3])
        self.assertEqual(phrases.ensure(cfg_for(), speaker), TOTAL - 1)
        folder = phrases.bank_dir(ARCHIE)
        self.assertFalse((folder / "ack-03.wav").exists())
        self.assertEqual(len(list(folder.glob("ack-*.wav"))), 9)
        # A later pass with a working synth fills the gap and nothing else.
        good = FakeSpeaker()
        self.assertEqual(phrases.ensure(cfg_for(), good), 1)
        self.assertEqual(good.calls, [phrases.ACKS[3]])

    def test_watch_survives_errors_and_refreshes(self):
        import threading

        calls = []
        stop = threading.Event()

        def live():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("hub down")
            stop.set()
            return cfg_for()

        log = mock.Mock()
        phrases.watch(live, FakeSpeaker(), log, sleep=lambda s: None, stop=stop)
        log.exception.assert_called_once()
        self.assertEqual(len(calls), 2)
        self.assertTrue((phrases.bank_dir(ARCHIE) / "ack-00.wav").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
