import unittest

try:
    import numpy as np

    from voice import ears, endturn, wake
except ImportError as error:
    raise unittest.SkipTest(f"the voice stack is not installed: {error}")

POLICY = endturn.Policy(base_s=2.0, open_s=3.5, max_s=90.0, adaptive=True)


class TestTrailsOff(unittest.TestCase):
    def test_finished_sentences(self):
        for text in ("Start Spotify.", "Dim the lights.", "Is it raining?", "Thank you.", "Do it!",
                     "I think so.", "The price is 40."):
            self.assertFalse(endturn.trails_off(text), text)

    def test_trailing_conjunctions_and_fillers(self):
        for text in ("Turn the lights off and", "I wanted to ask, so", "I could do that but",
                     "Do it because", "Well, um", "Dim the lights, and.", "Uh.", "It is the"):
            self.assertTrue(endturn.trails_off(text), text)

    def test_mid_clause_without_a_full_stop(self):
        for text in ("I would like you to add some kind of usage indicator into the", "like what",
                     "Dim the lights"):
            self.assertTrue(endturn.trails_off(text), text)

    def test_trailing_marks(self):
        for text in ("Well,", "So I was thinking...", "Let me think …", "And then:"):
            self.assertTrue(endturn.trails_off(text), text)

    def test_number_without_a_stop_trails(self):
        self.assertTrue(endturn.trails_off("My number is 4"))
        self.assertTrue(endturn.trails_off("set a timer for twenty"))

    def test_closing_word_ends_without_a_stop(self):
        self.assertFalse(endturn.trails_off("Open the door please"))

    def test_nothing_heard_is_not_a_trail(self):
        self.assertFalse(endturn.trails_off(""))
        self.assertFalse(endturn.trails_off("  "))


SENTENCE = "Start Spotify on the big speakers."


class TestDecide(unittest.TestCase):
    def test_breath_shorter_than_base_waits(self):
        self.assertEqual(endturn.decide(1.5, SENTENCE, POLICY), "wait")
        self.assertEqual(endturn.decide(1.99, SENTENCE, POLICY), "wait")

    def test_complete_sentence_ends_at_the_base(self):
        self.assertEqual(endturn.decide(2.0, SENTENCE, POLICY), "end")

    def test_short_command_ends_at_the_short_quiet(self):
        for line in ("Next song", "Next song.", "Louder", "Workspace 3", "Lights off", "Start Spotify."):
            self.assertEqual(endturn.decide(0.9, line, POLICY), "wait", line)
            self.assertEqual(endturn.decide(1.0, line, POLICY), "end", line)

    def test_short_line_on_an_open_word_still_waits(self):
        for line in ("Turn off the", "Lights and", "Dim it,", "Play um"):
            self.assertFalse(endturn.is_command(line), line)
            self.assertEqual(endturn.decide(1.0, line, POLICY), "wait", line)
        self.assertFalse(endturn.is_command("Turn the lights off in the kitchen"))

    def test_trailing_and_waits_past_the_base(self):
        self.assertEqual(endturn.decide(2.5, "Turn the lights off and", POLICY), "wait")
        self.assertEqual(endturn.decide(3.4, "Turn the lights off and", POLICY), "wait")

    def test_mid_clause_ends_at_the_long_quiet(self):
        self.assertEqual(endturn.decide(3.5, "I wanted to ask about the", POLICY), "end")

    def test_long_pause_always_ends(self):
        self.assertEqual(endturn.decide(30.0, "Turn the lights off and", POLICY), "end")

    def test_unknown_words_use_the_base(self):
        self.assertEqual(endturn.decide(1.9, None, POLICY), "wait")
        self.assertEqual(endturn.decide(2.0, None, POLICY), "end")

    def test_adaptive_off_is_the_plain_base_quiet(self):
        plain = endturn.Policy(base_s=2.0, open_s=3.5, max_s=90.0, adaptive=False)
        self.assertEqual(endturn.decide(2.0, "Turn the lights off and", plain), "end")
        self.assertEqual(endturn.decide(1.9, "Start Spotify.", plain), "wait")
        self.assertEqual(endturn.first_check_s(plain), 2.0)
        self.assertEqual(endturn.first_check_s(POLICY), 1.0)
        self.assertEqual(endturn.first_check_s(POLICY, heard_s=3.0), 2.0)


class TestPolicy(unittest.TestCase):
    def test_defaults(self):
        policy = endturn.Policy.from_cfg({})
        self.assertEqual((policy.base_s, policy.open_s, policy.max_s, policy.adaptive), (2.0, 3.5, 90.0, True))

    def test_hub_strings_are_read(self):
        policy = endturn.Policy.from_cfg({"ears.silence_s": "2.4", "ears.silence_open_s": "4",
                                          "ears.turn_max_s": "45", "ears.adaptive": "0"})
        self.assertEqual((policy.base_s, policy.open_s, policy.max_s, policy.adaptive), (2.4, 4.0, 45.0, False))

    def test_old_key_still_sets_the_base(self):
        self.assertEqual(endturn.Policy.from_cfg({"silence_tail_s": 0.5}).base_s, 0.5)

    def test_toml_table_is_read(self):
        policy = endturn.Policy.from_cfg({"ears": {"silence_s": 2.4, "adaptive": 0}})
        self.assertEqual((policy.base_s, policy.adaptive), (2.4, False))

    def test_hub_key_beats_the_old_key(self):
        policy = endturn.Policy.from_cfg({"ears.silence_s": "2.0", "silence_tail_s": 0.5})
        self.assertEqual(policy.base_s, 2.0)

    def test_short_quiet_is_read_and_never_longer_than_the_base(self):
        self.assertEqual(endturn.Policy.from_cfg({"ears.silence_short_s": "0.8"}).short_s, 0.8)
        self.assertEqual(endturn.Policy.from_cfg({"ears.silence_short_s": "5"}).short_s, 2.0)

    def test_bad_values_fall_back_and_the_long_quiet_is_never_shorter(self):
        policy = endturn.Policy.from_cfg({"ears.silence_s": "soon", "ears.silence_open_s": "0.5"})
        self.assertEqual(policy.base_s, 2.0)
        self.assertEqual(policy.open_s, 2.0)

    def test_every_default_is_a_hub_setting(self):
        from hub import settings

        for key, value in endturn.DEFAULTS.items():
            self.assertEqual(settings.DEFAULTS[key], value)


class FakeMic:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def read(self, timeout=1.0):
        if not self.blocks:
            raise AssertionError("record read past the end")
        return self.blocks.pop(0)


class FakeVAD:
    def reset(self):
        pass

    def prob(self, chunk):
        return 1.0 if np.max(np.abs(chunk)) > 0.1 else 0.0


def stream(*parts, start=10.0):
    """parts are (seconds, loud) pairs; 80 ms blocks stamped as they would arrive."""
    out, t = [], start
    for seconds, loud in parts:
        for _ in range(round(seconds / 0.08)):
            out.append((t, np.full(wake.CHUNK, 0.5 if loud else 0.0, dtype=np.float32)))
            t += 0.08
    return out


class Probe:
    """Stands in for the speech to text: says what the audio so far reads as."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, clip):
        self.calls.append(len(clip) / wake.RATE)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestReplay(unittest.TestCase):
    def test_a_short_command_ends_after_a_second(self):
        mic = stream((0.8, 1), (6.0, 0))
        probe = Probe("Next song")
        heard, events = self.run_record(mic, probe)
        self.assertLess(len(heard[0]) / wake.RATE, 0.8 + 1.2)
        self.assertEqual(heard.text, "Next song")
        self.assertEqual(events, [])

    def run_record(self, mic_blocks, probe=None, cfg=None):
        events = []
        heard = ears.record(FakeMic(mic_blocks), cfg or {}, 0.0, FakeVAD(), probe=probe, hold=events.append)
        return heard, events

    def test_a_breath_inside_a_sentence_stays_one_turn(self):
        # 2 s of talk, a 1.5 s breath, 2 s more, then a real end. Today's 1.2 s would cut at the breath.
        mic = stream((2.0, 1), (1.5, 0), (2.0, 1), (4.0, 0))
        probe = Probe("Add a usage indicator to the desktop app, and the rest of it.")
        (audio, spoke), events = self.run_record(mic, probe)
        self.assertTrue(spoke)
        self.assertGreaterEqual(len(audio) / wake.RATE, 2.0 + 1.5 + 2.0)
        self.assertEqual(len(probe.calls), 1)
        self.assertAlmostEqual(probe.calls[0], 5.5 + 2.0, delta=0.2)
        self.assertEqual(events, [])

    def test_the_old_timeout_would_have_cut_it(self):
        mic = stream((2.0, 1), (1.5, 0), (2.0, 1), (4.0, 0))
        audio, _ = ears.record(FakeMic(mic), {"ears.silence_s": 1.2, "ears.adaptive": "0"}, 0.0, FakeVAD())
        self.assertLess(len(audio) / wake.RATE, 2.0 + 1.5 + 0.5)

    def test_finished_sentence_ends_at_the_base_and_reuses_the_words(self):
        mic = stream((1.5, 1), (6.0, 0))
        probe = Probe("Start Spotify.")
        heard, events = self.run_record(mic, probe)
        self.assertLess(len(heard[0]) / wake.RATE, 1.5 + 2.3)
        self.assertEqual(heard.text, "Start Spotify.")
        self.assertEqual(events, [])

    def test_trailing_and_is_held_then_ends_at_the_long_quiet(self):
        mic = stream((1.5, 1), (8.0, 0))
        heard, events = self.run_record(mic, Probe("Turn the lights off and"))
        seconds = len(heard[0]) / wake.RATE
        self.assertGreaterEqual(seconds, 1.5 + 3.5)
        self.assertLess(seconds, 1.5 + 3.8)
        self.assertEqual(events, [True, False])
        self.assertEqual(heard.text, "Turn the lights off and")

    def test_held_turn_goes_on_when_he_talks_again(self):
        mic = stream((1.5, 1), (2.6, 0), (1.5, 1), (4.0, 0))
        probe = Probe("Turn the lights off and", "Turn the lights off and the music too.")
        heard, events = self.run_record(mic, probe)
        self.assertEqual(events, [True, False])
        self.assertEqual(len(probe.calls), 2)
        self.assertEqual(heard.text, "Turn the lights off and the music too.")
        self.assertGreater(len(heard[0]) / wake.RATE, 1.5 + 2.6 + 1.5 + 1.9)

    def test_hard_max_ends_a_turn_that_never_stops(self):
        mic = stream((20.0, 1))
        heard, _ = self.run_record(mic, Probe(), {"ears.turn_max_s": 5})
        self.assertTrue(heard[1])
        self.assertAlmostEqual(len(heard[0]) / wake.RATE, 5.0, delta=0.2)
        self.assertIsNone(heard.text)

    def test_hard_max_ends_a_hold(self):
        mic = stream((1.0, 1), (30.0, 0))
        heard, events = self.run_record(mic, Probe("and"), {"ears.turn_max_s": 5, "ears.silence_open_s": 10})
        self.assertLessEqual(len(heard[0]) / wake.RATE, 5.2)
        self.assertEqual(events, [True, False])

    def test_adaptive_off_never_asks_for_words(self):
        mic = stream((1.5, 1), (6.0, 0))
        probe = Probe()
        heard, _ = self.run_record(mic, probe, {"ears.adaptive": "0"})
        self.assertEqual(probe.calls, [])
        self.assertIsNone(heard.text)
        self.assertGreaterEqual(len(heard[0]) / wake.RATE, 1.5 + 2.0)
        self.assertLess(len(heard[0]) / wake.RATE, 1.5 + 2.3)

    def test_failed_check_falls_back_to_the_base_quiet(self):
        mic = stream((1.5, 1), (6.0, 0))
        heard, events = self.run_record(mic, Probe(RuntimeError("down")))
        self.assertLess(len(heard[0]) / wake.RATE, 1.5 + 2.3)
        self.assertIsNone(heard.text)
        self.assertEqual(events, [])

    def test_nothing_said_gives_up_without_asking(self):
        from unittest import mock

        clock = iter(np.arange(0, 100, 0.5))
        probe = Probe()
        with mock.patch.object(ears.time, "monotonic", lambda: next(clock)):
            heard, _ = self.run_record(stream((20.0, 0)), probe)
        self.assertFalse(heard[1])
        self.assertEqual(probe.calls, [])


if __name__ == "__main__":
    unittest.main()
