import unittest

from voice import commands


class AnimateWordsTest(unittest.TestCase):
    def test_asks(self):
        for said in ("animate me", "Diablo, animate me.", "Diablo act", "act this out", "can you animate me please",
                     "make me an animation", "Animate my voice"):
            self.assertTrue(commands.wants_animation(said), said)
        for said in ("animate the button on hover", "act like a pirate and tell me a joke", "what is an act"):
            self.assertFalse(commands.wants_animation(said), said)

    def test_end_phrase_is_cut(self):
        cases = {
            "and that is all from me. I'm done.": (True, "and that is all from me."),
            "this is cool, dictation done": (True, "this is cool,"),
            "Recording finished.": (True, ""),
            "okay I am done": (True, "okay"),
            "I tried the new build and that's it.": (False, "I tried the new build and that's it."),
            "I'm not done yet with this": (False, "I'm not done yet with this"),
        }
        for said, want in cases.items():
            self.assertEqual(commands.animation_end(said), want, said)


class AnimateFlowTest(unittest.TestCase):
    def test_take_until_im_done_then_last_word_and_render(self):
        import numpy as np
        from unittest import mock
        from voice import ears

        pieces = [(np.ones(16000, dtype=np.float32) * 0.1, "hello there mister man"),
                  (np.ones(32000, dtype=np.float32) * 0.1, "this is so cool. I'm done.")]
        heard = iter([(audio, True) for audio, _ in pieces])
        texts = iter([text for _, text in pieces])
        said, rendered = [], []

        class Speaker:
            def say(self, words, cfg, interruptible=True):
                said.append(words)

        class Settings:
            def get(self):
                return {}

        class Timeline:
            def mark(self, *a):
                pass

            def note(self, **k):
                pass

        def cut(audio, text, keep):
            self.assertEqual((text, keep), ("this is so cool. I'm done.", 4))
            return audio[:16000]

        class Now:
            def __init__(self, target, args, **kw):
                self.run = lambda: target(*args)

            def start(self):
                rendered.append(1)
                self.run()

        captured = {}
        with mock.patch.object(ears, "record", side_effect=lambda *a, **k: next(heard)), \
                mock.patch.object(ears, "transcribe", side_effect=lambda *a: next(texts)), \
                mock.patch.object(ears, "listening", return_value=True), \
                mock.patch.object(ears, "set_state"), mock.patch.object(ears, "feedback"), \
                mock.patch.object(ears, "_cut_before", side_effect=cut), \
                mock.patch.object(ears, "talk_ok", return_value=(True, 1.0)), \
                mock.patch.object(ears, "_render_take", side_effect=lambda pieces: captured.update(
                    n=sum(len(a) for a, _ in pieces), text=" ".join(t for _, t in pieces))), \
                mock.patch.object(ears.threading, "Thread", Now):
            reply = ears.animate(None, Settings(), None, Timeline(), Speaker())
        self.assertEqual(said, ["Sir, wait for the beep."])
        self.assertEqual(reply, "Got it. Your last word was cool.")
        self.assertEqual(captured["text"], "hello there mister man this is so cool.")
        self.assertEqual(captured["n"], 16000 + 16000)  # both pieces, the second without its end phrase


if __name__ == "__main__":
    unittest.main()
