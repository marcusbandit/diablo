import io
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from hub import db, pronunciation, tts

LEXICON = """
[da]
words = ["Example Town", "Sample", "Sample Phrase Long"]

[ja]
words = ["Samplebox"]

[de-DE]
words = ["Probewort"]
"""


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")
    stamp = time.time_ns()
    os.utime(path, ns=(stamp, stamp))


def wav(frames, rate=24000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return out.getvalue()


class LexiconTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "pronunciation.toml"
        write(self.path, LEXICON)

    def runs(self, text, language="en"):
        return pronunciation.segments(text, language, self.path)

    def test_a_listed_phrase_gets_its_language(self):
        self.assertEqual(self.runs("We drive to Example Town tonight."),
                         [("en", "We drive to "), ("da", "Example Town"), ("en", " tonight.")])

    def test_case_is_ignored_and_the_text_kept(self):
        self.assertEqual(self.runs("off to EXAMPLE town now"),
                         [("en", "off to "), ("da", "EXAMPLE town"), ("en", " now")])

    def test_only_whole_words_match(self):
        for said in ("Samplers are loud", "a Samplebox2 host", "unSample it", "Example Towns grow"):
            self.assertEqual(self.runs(said), [("en", said)], said)

    def test_the_longest_phrase_wins(self):
        self.assertEqual(self.runs("Say Sample Phrase Long now"),
                         [("en", "Say "), ("da", "Sample Phrase Long"), ("en", " now")])
        self.assertEqual(self.runs("Say Sample Phrase now"),
                         [("en", "Say "), ("da", "Sample"), ("en", " Phrase now")])

    def test_unknown_words_are_untouched(self):
        said = "Nothing here is listed, sir."
        self.assertEqual(self.runs(said), [("en", said)])

    def test_any_language_code_passes_through(self):
        self.assertEqual(self.runs("Samplebox and Probewort"),
                         [("ja", "Samplebox"), ("en", " and "), ("de-DE", "Probewort")])

    def test_phrases_match_across_line_breaks(self):
        self.assertEqual(self.runs("to Example\nTown"), [("en", "to "), ("da", "Example\nTown")])

    def test_punctuation_rides_on_a_neighbour(self):
        self.assertEqual(self.runs("Example Town, Sample!"), [("da", "Example Town, Sample!")])
        self.assertEqual(self.runs('"Example Town" it is'), [("da", '"Example Town'), ("en", '" it is')])

    def test_a_phrase_in_the_base_language_stays_one_run(self):
        self.assertEqual(self.runs("We drive to Example Town.", "da"), [("da", "We drive to Example Town.")])

    def test_underscores_are_not_word_letters(self):
        self.assertEqual(self.runs("off to _Example Town_"), [("en", "off to _"), ("da", "Example Town_")])

    def test_letters_whose_lower_case_is_longer_still_match(self):
        write(self.path, '[tr]\nwords = ["İrnekköy"]\n')
        self.assertEqual(self.runs("to İrnekköy"), [("en", "to "), ("tr", "İrnekköy")])
        self.assertEqual(self.runs("to İRNEKKÖY"), [("en", "to "), ("tr", "İRNEKKÖY")])

    def test_a_missing_file_changes_nothing(self):
        said = "We drive to Example Town."
        self.assertEqual(pronunciation.segments(said, "en", Path(self.tmp.name) / "nope.toml"), [("en", said)])

    def test_a_broken_file_changes_nothing(self):
        write(self.path, "[da\nwords = ")
        self.assertEqual(self.runs("Example Town"), [("en", "Example Town")])

    def test_an_edit_loads_without_a_restart(self):
        self.assertEqual(self.runs("Probeort"), [("en", "Probeort")])
        write(self.path, '[sv]\nwords = ["Probeort"]\n')
        self.assertEqual(self.runs("Probeort"), [("sv", "Probeort")])
        self.assertEqual(self.runs("Example Town"), [("en", "Example Town")])

    def test_a_bare_list_per_language_is_read_too(self):
        write(self.path, 'fr = ["Mot Exemple"]\n')
        self.assertEqual(self.runs("le Mot Exemple"), [("en", "le "), ("fr", "Mot Exemple")])

    def test_the_example_file_parses(self):
        example = Path(__file__).resolve().parent.parent / "hub" / "pronunciation.example.toml"
        self.assertEqual(pronunciation.segments("to Example Town", "en", example),
                         [("en", "to "), ("da", "Example Town")])


class SpeechRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        db.set_setting("tts_provider", "cartesia")
        db.set_setting("tts_api_key", "k")
        db.set_setting("tts_voice_id", "voice-one")
        self.path = Path(self.tmp.name) / "pronunciation.toml"
        write(self.path, LEXICON)
        patch = mock.patch.object(pronunciation, "PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)
        self.sent = []

    def post(self, refuse=()):
        def post(url, headers=None, json=None, timeout=None):
            self.sent.append(dict(json))
            request = httpx.Request("POST", url)
            if json["language"] in refuse:
                return httpx.Response(400, json={"error": "bad language"}, request=request)
            frame = {"en": b"\x01\x00", "da": b"\x02\x00", "ja": b"\x03\x00"}.get(json["language"], b"\x09\x00")
            return httpx.Response(200, content=wav(frame * 10), request=request)
        return post

    def test_the_listed_word_goes_out_with_its_language_tag(self):
        with mock.patch.object(tts.httpx, "post", self.post()):
            audio = tts.speak("We drive to Example Town tonight.")
        tagged = sorted((s["language"], s["transcript"]) for s in self.sent)
        self.assertEqual(tagged, [("da", "Example Town"), ("en", "We drive to"), ("en", "tonight.")])
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual(w.getnframes(), 30)
            self.assertEqual(w.readframes(30), b"\x01\x00" * 10 + b"\x02\x00" * 10 + b"\x01\x00" * 10)

    def test_a_reply_with_no_listed_word_is_one_request_as_before(self):
        with mock.patch.object(tts.httpx, "post", self.post()):
            tts.speak("Lights on, sir.")
        self.assertEqual([(s["language"], s["transcript"]) for s in self.sent], [("en", "Lights on, sir.")])

    def test_a_reply_that_is_only_a_listed_word_is_one_request_in_its_language(self):
        with mock.patch.object(tts.httpx, "post", self.post()):
            tts.speak("Samplebox")
        self.assertEqual([(s["language"], s["transcript"]) for s in self.sent], [("ja", "Samplebox")])

    def test_a_language_the_voice_refuses_is_said_in_the_reply_language(self):
        with mock.patch.object(tts.httpx, "post", self.post(refuse={"de-DE"})):
            audio = tts.speak("Say Probewort now")
        self.assertIn(("en", "Probewort"), [(s["language"], s["transcript"]) for s in self.sent])
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual(w.getnframes(), 30)

    def test_a_reply_that_is_only_a_refused_word_is_still_said(self):
        with mock.patch.object(tts.httpx, "post", self.post(refuse={"de-DE"})):
            audio = tts.speak("Probewort")
        self.assertEqual([(s["language"], s["transcript"]) for s in self.sent],
                         [("de-DE", "Probewort"), ("en", "Probewort")])
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual(w.getnframes(), 10)

    def test_the_english_speed_stays_on_english_runs_only(self):
        db.set_setting("speech_rate", "1.2")
        with mock.patch.object(tts.httpx, "post", self.post()):
            tts.speak("To Example Town")
        speeds = {s["language"]: s["speed"] for s in self.sent}
        self.assertEqual(speeds, {"en": 1.2, "da": 1.0})


if __name__ == "__main__":
    unittest.main()
