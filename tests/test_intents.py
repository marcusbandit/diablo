import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import intents


class TestIntents(unittest.TestCase):
    def check(self, text, intent, named=True):
        self.assertEqual(intents.match(text), ([intent], named), text)

    def test_two_orders_in_one_breath_voice_first(self):
        self.assertEqual(intents.match("Can you please shut up and go deaf for a moment?"), (["mute", "deafen"], False))
        self.assertEqual(intents.match("Diablo, listen and speak."), (["unmute", "listen"], True))

    def test_opposites_let_the_later_word_win(self):
        self.assertEqual(intents.match("Diablo stop listening. No wait, listen.")[0], ["listen"])

    def test_voice_off(self):
        for text in ("Diablo, shut up.", "Diablo silence.", "Diablo, stop speaking.", "Diablo turn off your voice",
                     "diablo, be quiet", "Diablo, voice off."):
            self.check(text, "mute")

    def test_voice_on(self):
        for text in ("Diablo, you can speak now.", "Diablo, speak.", "Diablo turn on your voice", "Diablo, talk again."):
            self.check(text, "unmute")

    def test_ears_off(self):
        for text in ("Diablo, stop listening.", "Can you deafen for a second, Diablo?", "Diablo, no more mic.",
                     "Diablo turn off the microphone.", "Diablo, off.", "Diablo don't listen"):
            self.check(text, "deafen")

    def test_ears_on(self):
        for text in ("Diablo, listen.", "Diablo on.", "Diablo, turn on the mic.", "Diablo, start listening.",
                     "Diablo, wake up."):
            self.check(text, "listen")

    def test_unnamed_still_matches_but_says_so(self):
        self.check("stop listening for a moment", "deafen", named=False)

    def test_ordinary_speech_is_not_a_command(self):
        for text in ("Diablo, what is the weather?", "Turn off the corner lamp.", "I was listening to music."):
            self.assertEqual(intents.match(text)[0], [], text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
