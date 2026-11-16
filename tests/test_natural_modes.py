"""Speech control takes commands, "dictate" works however it is said, and a bare stop leaves any mode."""

import unittest
from unittest import mock

import numpy as np

from voice import commands, ears, timeline

DESKTOP = {"windows": [{"address": "0x1", "focused": True, "class": "zen"}], "players": []}


class TestDictationStart(unittest.TestCase):
    def test_plain_and_repeated_start_it(self):
        for line in ("Dictate.", "Diablo, dictate", "Take dictation.", "start dictating now",
                     "Dictate. Dictate. Dictate. Dictate.", "Dictate, dictate"):
            self.assertEqual(commands.dictation_start(line), ("", False), line)

    def test_the_words_in_the_same_breath(self):
        self.assertEqual(commands.dictation_start("Dictate hello there, dictation done."), ("hello there,", True))
        self.assertEqual(commands.dictation_start("Dictate. Something random. Dictation ended."),
                         ("Something random.", True))
        self.assertEqual(commands.dictation_start("Diablo, dictate: see you at eight"), ("see you at eight", False))
        self.assertEqual(commands.dictation_start("Start dictating, dear Alex"), ("dear Alex", False))

    def test_talk_about_dictation_is_not_a_dictation(self):
        for line in ("dictation is hard", "Dictation is broken on the laptop, can you check?",
                     "I don't know something more dictation and", "Can you dictate this for me?",
                     "dictated words go missing", "Diablo, dictate a mail to Alex saying I'm late"):
            self.assertIsNone(commands.dictation_start(line), line)
            self.assertFalse(commands.wants_dictation(line), line)

    def test_one_way_out(self):
        for line in ("Stop.", "That's all", "I'm done.", "Diablo, stop"):
            self.assertTrue(commands.leaves_mode(line), line)
        for line in ("Stop the music", "the bus stop", "I'm done with the report, send it"):
            self.assertFalse(commands.leaves_mode(line), line)


class TestEarlyLocal(unittest.TestCase):
    def test_mode_words_end_the_turn_at_the_first_pause(self):
        self.assertEqual(ears.early_local("Dictate. Dictate."), {"local": "dictate"})
        self.assertEqual(ears.early_local("Speech control"), {"local": "start"})
        self.assertEqual(ears.early_local("Stop.", loop=True), {"local": "stop"})
        self.assertIsNone(ears.early_local("Stop."))
        self.assertIsNone(ears.early_local("Speech control", loop=True))
        self.assertIsNone(ears.early_local("what is the time"))
        self.assertTrue(ears.early_sure({"local": "dictate"}))

    def test_early_read_skips_jev_for_them(self):
        with mock.patch.object(ears, "control") as control:
            self.assertEqual(ears.early_read({}, "dictate", DESKTOP), {"local": "dictate"})
        control.assert_not_called()


class TestDictateTakesItsStart(unittest.TestCase):
    def run_dictate(self, pieces, first="", ended=False):
        pieces = iter(pieces)
        typed = []
        heard = ears.Heard(np.ones(16000, dtype=np.float32), True)
        with mock.patch.object(ears, "record", return_value=heard), \
                mock.patch.object(ears, "talk_ok", return_value=(True, 0.9)), \
                mock.patch.object(ears, "transcribe", side_effect=lambda audio, cfg, *_, **__: next(pieces)), \
                mock.patch.object(ears, "listening", return_value=True), \
                mock.patch.object(ears, "set_state"), mock.patch.object(ears.chime, "play"), \
                mock.patch.object(commands, "desktop_state", return_value=DESKTOP), \
                mock.patch.object(commands, "type_text", side_effect=lambda t: typed.append(t) or (True, "")):
            settings = mock.Mock(get=mock.Mock(return_value={}))
            ears.dictate(None, settings, None, timeline.Timeline(), first=first, ended=ended)
        return typed

    def test_said_and_closed_in_one_breath_types_at_once(self):
        self.assertEqual(self.run_dictate([], first="hello there,", ended=True), ["hello there,"])

    def test_the_first_words_lead_the_rest(self):
        self.assertEqual(self.run_dictate(["and the rest. Dictation done."], first="Dear Alex,"),
                         ["Dear Alex, and the rest."])

    def test_a_bare_stop_ends_it_and_types_what_it_has(self):
        self.assertEqual(self.run_dictate(["The build is green.", "Stop."]), ["The build is green."])


class TestCommandsOnly(unittest.TestCase):
    def loop_line(self, text, cfg=None):
        heard = ears.Heard(np.zeros(1600, dtype=np.float32), True, text)
        played = []
        patches = [
            mock.patch.object(ears, "talk_ok", return_value=(True, None)),
            mock.patch.object(ears, "set_state"),
            mock.patch.object(ears.chime, "play", side_effect=played.append),
            mock.patch.object(ears.recordings, "keep", return_value=None),
            mock.patch.object(ears, "local_line", return_value=None),
            mock.patch.object(ears.commands, "desktop_state", return_value={"windows": []}),
            mock.patch.object(ears, "control", return_value={}),
            mock.patch.object(ears, "note_control"),
            mock.patch.object(ears.surroundings, "lines", return_value=[]),
            mock.patch.object(ears.surroundings, "points_at_screen", return_value=False),
            mock.patch.object(ears, "say_answer", return_value=False),
        ]
        for patch in patches:
            self.enterContext(patch)
        claude = self.enterContext(mock.patch.object(ears, "answer_or_background", return_value="Nine."))
        got = ears._loop_line(None, None, None, None, None, None, None, cfg or {}, heard, timeline.Timeline("loop"))
        return got, claude.called, played

    def test_a_stray_sentence_gets_the_low_note_and_no_claude(self):
        got, asked, played = self.loop_line("Yeah, we wouldn't care, do.")
        self.assertIsNone(got)
        self.assertFalse(asked)
        self.assertEqual(played[-1], "never")

    def test_his_name_still_reaches_claude(self):
        got, asked, _ = self.loop_line("Diablo, what is the time")
        self.assertIs(got, True)
        self.assertTrue(asked)

    def test_the_setting_brings_back_every_line_to_claude(self):
        got, asked, _ = self.loop_line("what is the time", {"ears.loop_commands_only": "0"})
        self.assertIs(got, True)
        self.assertTrue(asked)


if __name__ == "__main__":
    unittest.main()
