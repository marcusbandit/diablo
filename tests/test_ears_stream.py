"""The ears speaking a streamed answer: no sentence twice."""

import unittest
from unittest import mock

from voice import ears, timeline


def asked(say_lines, done):
    def ask(cfg, text, context, shot, desktop, on_say=None):
        for line in say_lines:
            on_say(line)
        return done

    return ask


class TestNothingTwice(unittest.TestCase):
    def words(self, say_lines, done):
        heard = []
        with mock.patch.object(ears, "ask", side_effect=asked(say_lines, done)):
            words = ears.answer_words({}, "lights", [], None, {}, timeline.Timeline(), heard.append)
        return heard, words

    def test_every_streamed_sentence_is_handed_over_and_the_rest_is_what_is_left(self):
        heard, words = self.words(
            ["On it.", "The lamp is off."],
            {"reply": "On it.\n\nThe lamp is off. The panel is dimmed.", "spoken_first": "On it.",
             "rest": "The lamp is off. The panel is dimmed.", "backend": "claude"})
        self.assertEqual(heard, ["On it.", "The lamp is off."])
        self.assertEqual(words, "The panel is dimmed.")

    def test_an_answer_wholly_streamed_says_nothing_more(self):
        heard, words = self.words(
            ["Checking.", "It is sunny."],
            {"reply": "Checking.\n\nIt is sunny.", "spoken_first": "Checking.", "rest": "It is sunny.",
             "backend": "claude"})
        self.assertEqual(words, "")

    def test_a_sentence_with_a_link_streamed_is_not_said_again(self):
        heard, words = self.words(
            ["See https://example.com/a for it."],
            {"reply": "See https://example.com/a for it.", "spoken_first": "See https://example.com/a for it.",
             "rest": "See https://example.com/a for it.", "backend": "claude"})
        self.assertEqual(words, "")

    def test_without_streamed_lines_the_reply_is_said_as_before(self):
        heard, words = self.words([], {"reply": "Here:\n- one\n- two", "backend": "claude"})
        self.assertEqual(heard, [])
        self.assertEqual(words, ears.spoken("Here:\n- one\n- two"))


if __name__ == "__main__":
    unittest.main()
