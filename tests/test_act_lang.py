import unittest

from voice import act, eyes, mouth


class LanguageTest(unittest.TestCase):
    def test_flags_and_aliases(self):
        parse = act.build_parser().parse_args
        self.assertEqual(parse(["--last"]).lang, "auto")
        self.assertEqual(parse(["--last", "--dk"]).lang, "da")
        self.assertEqual(parse(["--last", "--jp"]).lang, "ja")
        self.assertEqual(parse(["--last", "--lang", "DK"]).lang, "da")
        self.assertEqual(parse(["take.m4a", "--es"]).lang, "es")
        self.assertEqual(act.ALIASES["dk"], "da")
        self.assertEqual(act.ALIASES["jp"], "ja")
        self.assertEqual(act.ALIASES["us"], "en")

    def test_japanese_gets_clause_spaces(self):
        text = mouth.spaced("こんにちは、マーカスさん。今日は。", "ja")
        self.assertEqual(text, "こんにちは、 マーカスさん。 今日は。")
        self.assertEqual([c for c, _ in eyes.clauses(text)], ["こんにちは、", "マーカスさん。", "今日は。"])
        self.assertEqual(mouth.spaced("hej, med dig.", "da"), "hej, med dig.")

    @unittest.skipUnless(__import__("importlib").util.find_spec("pykakasi"), "pykakasi not installed")
    def test_kanji_read_as_kana(self):
        self.assertEqual(mouth.kana("作りました"), "つくりました")

    def test_danish_and_spanish_sounds_have_mouths(self):
        self.assertEqual(mouth.shape("ø"), "oo")
        self.assertEqual(mouth.shape("y"), "oo")
        self.assertEqual(mouth.shape("ʋ"), "fv")
        self.assertEqual(mouth.shape("β"), "rest")
        self.assertEqual(mouth.shape("ɯᵝ"), "ee")


class MeasureTest(unittest.TestCase):
    def test_touching_stretches_keep_cues_in_order(self):
        import io
        import tempfile
        import wave
        from pathlib import Path
        from unittest import mock

        buf = io.BytesIO()
        with wave.open(buf, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\0\0" * 24000 * 4)
        wav = Path(tempfile.mkdtemp()) / "take.wav"
        wav.write_bytes(buf.getvalue())
        # each stretch ends with a closing rest well after its last sound
        part = {"mouth": [(0.1, "open"), (0.5, "mid"), (2.3, "rest")], "eyes": [(0.0, "happy")]}
        segments = [{"start": 0.0, "end": 2.0, "text": "one", "lang": "en"},
                    {"start": 2.0, "end": 4.0, "text": "two", "lang": "da"}]
        with mock.patch.object(mouth, "face_for", return_value=part) as face_for, \
                mock.patch.object(act, "tag_eyes", return_value=["happy"]):
            face, looks, text = act.measure(wav, segments)
        times = [at for at, _ in face["mouth"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(face_for.call_args_list[1].args[3], "da")
        self.assertEqual(text, "one two")

    def test_text_and_language_skip_whisper(self):
        from unittest import mock

        with mock.patch.object(act.subprocess, "run") as run, mock.patch("voice.mouth.seconds", return_value=3.0), \
                mock.patch.object(act.Path, "read_bytes", return_value=b""):
            out = act.transcribe("x.wav", "da", "hej med dig")
        run.assert_not_called()
        self.assertEqual(out, [{"start": 0.0, "end": 3.0, "text": "hej med dig", "lang": "da"}])


if __name__ == "__main__":
    unittest.main()
