"""Only his voice keeps a turn open (voice/ownvoice.py), replayed through ears.record().

The voiceprint is stubbed: his audio is a level of 0.5, other people's 0.3, and a window is
someone else's when most of it is at 0.3. Windows are judged in feed(), so a replay gives the
same verdicts every run."""

import threading
import time
import unittest
from unittest import mock

import numpy as np

from voice import ears, ownvoice, wake

HIM, THEM = 0.5, 0.3


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
    """parts are (seconds, level) pairs; 80 ms blocks stamped as they would arrive."""
    out, t = [], start
    for seconds, level in parts:
        for _ in range(round(seconds / 0.08)):
            out.append((t, np.full(wake.CHUNK, level, dtype=np.float32)))
            t += 0.08
    return out


def stub(clip, since=None, until=None):
    return bool(np.mean(np.isclose(clip, THEM)) <= 0.5)


def seconds_of(audio, level):
    return float(np.sum(np.isclose(audio, level))) / wake.RATE


def judge(verdict=stub):
    return ownvoice.Judge(verdict, background=False)


def run(parts, cfg=None, own=True, verdict=stub, probe=None):
    cfg = {"ears.adaptive": "0", **(cfg or {})}
    j = judge(verdict) if own else None
    return ears.record(FakeMic(stream(*parts)), cfg, 0.0, FakeVAD(), probe=probe, judge=j), j


class TestRecord(unittest.TestCase):
    def test_him_alone_ends_exactly_as_before(self):
        parts = [(3.0, HIM), (0.6, 0), (2.0, HIM), (5.0, 0)]
        before, _ = run(parts, own=False)
        after, _ = run(parts)
        self.assertEqual(len(after[0]), len(before[0]))
        self.assertTrue(after[1])
        self.assertEqual(after.cut_s, 0.0)

    def test_a_stranger_after_him_no_longer_holds_the_turn_open(self):
        parts = [(3.0, HIM), (0.3, 0), (20.0, THEM), (4.0, 0)]
        before, _ = run(parts, own=False)
        self.assertGreater(len(before[0]) / wake.RATE, 3.3 + 20.0)
        heard, _ = run(parts)
        self.assertTrue(heard[1])
        self.assertLess(len(heard.full) / wake.RATE, 3.3 + 2.0 + ownvoice.LAG_S + 2.5)
        self.assertAlmostEqual(seconds_of(heard[0], HIM), 3.0, delta=0.05)
        # What stays of theirs: the start a window still called his, and the last two strides,
        # which fewer than two windows have judged.
        self.assertLess(seconds_of(heard[0], THEM), 0.32 + 2 * 0.64 + 0.1)
        self.assertGreater(heard.cut_s, 1.0)

    def test_he_is_not_cut_off_when_he_picks_up_again_over_them(self):
        parts = [(3.0, HIM), (1.7, THEM), (2.5, HIM), (5.0, 0)]
        heard, j = run(parts)
        self.assertAlmostEqual(seconds_of(heard.full, HIM), 5.5, delta=0.05)
        self.assertAlmostEqual(seconds_of(heard[0], HIM), 5.5, delta=0.05)
        self.assertGreater(len(heard.full) / wake.RATE, 3.0 + 1.7 + 2.5 + 1.9)
        self.assertLess(seconds_of(heard[0], THEM), seconds_of(heard.full, THEM))

    def test_after_a_pause_his_talk_counts_at_once(self):
        parts = [(3.0, HIM), (0.3, 0), (1.5, THEM), (0.6, 0), (2.0, HIM), (5.0, 0)]
        heard, _ = run(parts)
        self.assertAlmostEqual(seconds_of(heard[0], HIM), 5.0, delta=0.05)
        self.assertGreater(len(heard.full) / wake.RATE, 3.0 + 0.3 + 1.5 + 0.6 + 2.0 + 1.9)

    def test_someone_else_talking_longer_than_the_quiet_ends_his_turn(self):
        # Quiet of his own for silence_s plus LAG_S, while someone else talks, is a finished turn.
        parts = [(3.0, HIM), (0.3, 0), (3.5, THEM), (0.6, 0), (2.0, HIM), (5.0, 0)]
        heard, _ = run(parts)
        self.assertAlmostEqual(seconds_of(heard[0], HIM), 3.0, delta=0.05)
        self.assertLess(len(heard.full) / wake.RATE, 3.3 + 3.5 + 0.6)

    def test_his_short_reply_straight_after_a_stranger_is_kept(self):
        heard, j = run([(3.0, HIM), (0.3, 0), (2.0, THEM), (0.6, HIM), (6.0, 0)])
        self.assertGreater(j.other_windows, 0)
        self.assertAlmostEqual(seconds_of(heard[0], HIM), 3.04 + 0.64, delta=0.05)
        self.assertGreater(len(heard.full) / wake.RATE, 3.0 + 0.3 + 2.0 + 0.6)

    def test_only_other_voices_is_nothing_heard(self):
        heard, _ = run([(0.5, 0), (8.0, THEM), (6.0, 0)])
        self.assertFalse(heard[1])
        self.assertIsNone(heard.text)
        self.assertLess(len(heard.full) / wake.RATE, 0.5 + 8.0)

    def test_a_window_it_cannot_judge_is_his(self):
        parts = [(3.0, HIM), (0.3, 0), (10.0, THEM), (4.0, 0)]
        before, _ = run(parts, own=False)
        for verdict in (lambda *a: None, mock.Mock(side_effect=RuntimeError("busy"))):
            heard, j = run(parts, verdict=verdict)
            self.assertEqual(len(heard[0]), len(before[0]))
            self.assertEqual(heard.cut_s, 0.0)
            self.assertGreater(j.windows, 0)

    def test_the_end_of_turn_check_reads_only_his_words(self):
        clips = []

        def probe(clip):
            clips.append(clip)
            return "Turn the lights off."

        parts = [(3.0, HIM), (0.3, 0), (20.0, THEM), (4.0, 0)]
        heard, _ = run(parts, cfg={"ears.adaptive": "1"}, probe=probe)
        self.assertTrue(clips)
        self.assertLess(seconds_of(clips[-1], THEM), 0.32 + 2 * 0.64 + 0.1)
        self.assertLess(len(heard.full) / wake.RATE, 3.3 + 2.0 + ownvoice.LAG_S + 2.5)

    def test_without_a_judge_the_audio_is_all_of_it(self):
        heard, _ = run([(1.5, HIM), (4.0, 0)], own=False)
        self.assertIs(heard.full, heard[0])
        self.assertEqual(heard.cut_s, 0.0)


class TestJudge(unittest.TestCase):
    def feed(self, j, levels, start=0):
        for i, level in enumerate(levels):
            j.feed(start + i, np.full(512, level, dtype=np.float32), level > 0.1, float(start + i))
        return start + len(levels)

    def test_his_talk_breaks_the_quiet_and_others_do_not(self):
        j = judge()
        at = self.feed(j, [HIM] * 60 + [THEM] * 100)
        self.assertLess(j.his_end(), 70)
        self.assertGreater(j.quiet(at, 0.0), 0.0)
        self.assertEqual(j.quiet(at, 0.0), (at - j.his_end()) * ownvoice.STEP_S - ownvoice.LAG_S)

    def test_a_tie_between_windows_is_his(self):
        outcomes = iter([True, False])
        j = judge(lambda *a: next(outcomes))
        self.feed(j, [HIM] * 70)
        self.assertEqual(j.label[20:50], [ownvoice.HIS] * 30)
        self.assertEqual(j.label[50:70], [ownvoice.OTHER] * 20)

    def test_a_numpy_verdict_counts(self):
        j = judge(lambda *a: np.False_)
        self.feed(j, [HIM] * ownvoice.WINDOW)
        self.assertEqual(j.other_windows, 1)
        self.assertEqual(j.label[0], ownvoice.OTHER)

    def test_talk_straight_on_from_someone_else_is_theirs_until_judged(self):
        j = judge()
        self.feed(j, [HIM] * 40 + [THEM] * 60)
        self.assertEqual(j.label[-1], ownvoice.OTHER)
        self.assertFalse(j.feed(100, np.full(512, HIM, dtype=np.float32), True, 100.0))
        self.assertTrue(j.feed(120, np.full(512, HIM, dtype=np.float32), True, 120.0))

    def test_a_cut_keeps_padding_next_to_him_and_short_ones_are_kept(self):
        j = judge()
        self.feed(j, [HIM] * 60 + [THEM] * 80)
        spans = j.cuts()
        self.assertEqual(len(spans), 1)
        start, end = spans[0]
        first_other = next(j.pos[p] for p in range(len(j.pos)) if j.label[p] == ownvoice.OTHER and not j.votes[p][0])
        self.assertGreater(first_other, 60)
        self.assertEqual(start, (first_other + ownvoice.PAD) * ownvoice.FRAME)
        last_judged = max(p for p in range(len(j.pos)) if j.votes[p][1] >= 2)
        self.assertEqual(end, (j.pos[last_judged] + 1 - ownvoice.PAD) * ownvoice.FRAME)
        audio = np.concatenate([np.full(512, HIM if i < 60 else THEM, dtype=np.float32) for i in range(140)])
        trimmed = j.trim(audio)
        self.assertEqual(len(trimmed), len(audio) - (end - start) + int(ownvoice.JOIN_S * ownvoice.RATE))
        self.assertEqual(self.labelled([ownvoice.HIS] * 10 + [ownvoice.OTHER] * 10 + [ownvoice.HIS] * 10).cuts(), [])

    def labelled(self, labels):
        j = judge()
        j.pos, j.label = list(range(len(labels))), list(labels)
        j.votes = [[1, 0] if label == ownvoice.HIS else [0, 2] for label in labels]
        with j.lock:
            j._refresh()
        return j

    def test_speech_any_window_called_his_is_never_cut(self):
        j = self.labelled([ownvoice.HIS] * 20 + [ownvoice.OTHER] * 60 + [ownvoice.HIS] * 20)
        j.votes[20:40] = [[1, 3]] * 20
        with j.lock:
            j._refresh()
        self.assertEqual(j.cuts(), [((40 + ownvoice.PAD) * 512, (80 - ownvoice.PAD) * 512)])

    def test_talk_fewer_than_two_windows_judged_is_never_cut(self):
        j = self.labelled([ownvoice.HIS] * 20 + [ownvoice.OTHER] * 60 + [ownvoice.OTHER] * 20)
        j.votes[80:90] = [[0, 0]] * 10
        j.votes[90:] = [[0, 1]] * 10
        with j.lock:
            j._refresh()
        self.assertEqual(j.cuts(), [((20 + ownvoice.PAD) * 512, (80 - ownvoice.PAD) * 512)])

    def test_a_cut_between_his_words_leaves_a_short_silence(self):
        j = self.labelled([ownvoice.HIS] * 20 + [ownvoice.OTHER] * 60 + [ownvoice.HIS] * 20)
        audio = np.ones(100 * 512, dtype=np.float32)
        trimmed = j.trim(audio)
        cut = (60 - 2 * ownvoice.PAD) * 512
        self.assertEqual(len(trimmed), len(audio) - cut + int(ownvoice.JOIN_S * ownvoice.RATE))
        self.assertEqual(j.cut_seconds(), cut / ownvoice.RATE)

    def test_an_idle_thread_ends_and_the_next_window_starts_one(self):
        j = ownvoice.Judge(lambda *a: True)
        with mock.patch.object(ownvoice, "IDLE_S", 0.01):
            for i in range(ownvoice.WINDOW):
                j.feed(i, np.full(512, HIM, dtype=np.float32), True, float(i))
            first = j.thread
            first.join(5)
            self.assertFalse(first.is_alive())
            self.assertIsNone(j.thread)
            for i in range(ownvoice.WINDOW, ownvoice.WINDOW + ownvoice.STRIDE):
                j.feed(i, np.full(512, HIM, dtype=np.float32), True, float(i))
            self.assertIsNot(j.thread, first)
            deadline = time.monotonic() + 5
            while j.windows < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            j.close()
        self.assertEqual(j.windows, 2)

    def test_windows_are_judged_on_a_thread_of_their_own(self):
        started, release = threading.Event(), threading.Event()

        def slow(clip, since, until):
            started.set()
            release.wait(5)
            return False

        j = ownvoice.Judge(slow)
        for i in range(ownvoice.WINDOW):
            j.feed(i, np.full(512, HIM, dtype=np.float32), True, float(i))
        self.assertTrue(started.wait(5))
        self.assertEqual(j.windows, 0)
        release.set()
        deadline = time.monotonic() + 5
        while j.windows == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(j.windows, 1)
        self.assertEqual(j.label[0], ownvoice.OTHER)
        j.close()


def unit(*parts):
    v = np.zeros(4, dtype=np.float32)
    v[: len(parts)] = parts
    return v / np.linalg.norm(v)


TALK = unit(1.0)


def at_print(score, side=1):
    """A voiceprint that scores `score` against TALK, the rest of it along axis `side`."""
    v = np.zeros(4, dtype=np.float32)
    v[0], v[side] = score, np.sqrt(1 - score ** 2)
    return v


class TestVoice(unittest.TestCase):
    def verdicts(self, *embeddings, slack=0.0, sound=None):
        voice = ownvoice.Voice(0.62, TALK, slack=slack, sound=sound)
        out = []
        for emb in embeddings:
            with mock.patch.object(wake, "talk_embedding", return_value=emb):
                out.append(voice(np.zeros(16000, dtype=np.float32), 1.0, 2.6))
        return out

    def test_before_a_sure_window_the_print_decides(self):
        self.assertEqual(self.verdicts(at_print(0.60)), [True])
        self.assertEqual(self.verdicts(at_print(0.62 - ownvoice.MARGIN + 1e-4)), [True])
        self.assertEqual(self.verdicts(at_print(0.55)), [False])

    def test_after_a_sure_window_his_windows_in_the_turn_decide(self):
        his = at_print(0.8)
        close_to_him = at_print(0.55)
        a_stranger = at_print(0.60, side=2)
        self.assertEqual(self.verdicts(his, close_to_him, a_stranger), [True, True, False])

    def test_half_him_stays_his_while_it_is_high_against_his_print(self):
        self.assertEqual(self.verdicts(at_print(0.8), at_print(0.70, side=2)), [True, True])
        self.assertEqual(self.verdicts(at_print(0.8), at_print(0.65, side=2)), [True, False])

    def test_with_music_the_print_alone_never_calls_someone_else(self):
        self.assertEqual(self.verdicts(at_print(0.43), slack=ears.BARGE_SLACK), [None])
        seen = []
        self.assertEqual(self.verdicts(at_print(0.43), sound=lambda since, until: seen.append((since, until)) or ears.BARGE_SLACK), [None])
        self.assertEqual(seen, [(1.0, 2.6)])
        self.assertEqual(self.verdicts(at_print(0.43), sound=lambda since, until: 0.0), [False])

    def test_with_music_his_windows_in_the_turn_still_decide_with_the_slack(self):
        loud = lambda since, until: ears.BARGE_SLACK
        self.assertEqual(self.verdicts(at_print(0.8), at_print(0.55), at_print(0.45, side=2), sound=loud),
                         [True, True, False])
        self.assertEqual(self.verdicts(at_print(0.8), at_print(0.60, side=2), sound=loud), [True, True])

    def test_no_voiceprint_is_no_verdict(self):
        self.assertEqual(self.verdicts(None), [None])

    def test_it_never_queues_behind_another_voice_check(self):
        voice = ownvoice.Voice(0.62, TALK)
        with mock.patch.object(ownvoice_voiceid(), "busy", return_value=True), \
                mock.patch.object(wake, "talk_embedding") as embedding:
            self.assertIsNone(voice(np.zeros(16000, dtype=np.float32), 1.0, 2.6))
        embedding.assert_not_called()


def ownvoice_voiceid():
    from voice import voiceid

    return voiceid


class TestSetting(unittest.TestCase):
    def factory(self, cfg, meta=None, printed=True):
        with mock.patch.object(wake, "meta", return_value={"talk_min": 0.62} if meta is None else meta), \
                mock.patch.object(wake, "TALK_PRINT", mock.Mock(exists=lambda: printed)), \
                mock.patch.object(ownvoice.np, "load", return_value=TALK):
            return ownvoice.for_turn(cfg)

    def test_on_by_default(self):
        self.assertIsInstance(self.factory({}), ownvoice.Judge)

    def test_the_hub_setting_and_the_toml_table_switch_it_off(self):
        self.assertIsNone(self.factory({"ears.own_voice_end": "0"}))
        self.assertIsNone(self.factory({"ears": {"own_voice_end": 0}}))
        self.assertIsNone(self.factory({"ears_speaker_check": "off"}))

    def test_nothing_to_judge_with_is_off(self):
        self.assertIsNone(self.factory({}, meta={}))
        self.assertIsNone(self.factory({}, printed=False))

    def test_it_is_an_end_of_turn_setting_and_so_a_hub_setting(self):
        from hub import settings
        from voice import endturn

        self.assertEqual(endturn.DEFAULTS["ears.own_voice_end"], "1")
        self.assertEqual(settings.DEFAULTS["ears.own_voice_end"], "1")


class TestWiring(unittest.TestCase):
    def test_speech_control_judges_its_lines(self):
        seen = {}

        def fake_record(*args, **kwargs):
            seen.update(kwargs)
            return ears.Heard(np.zeros(0, dtype=np.float32), False)

        sentinel = object()
        settings = mock.Mock(get=lambda: {})
        with mock.patch.object(ears, "record", side_effect=fake_record), mock.patch.object(ears, "set_state"), \
                mock.patch.object(ears.ownvoice, "for_turn", return_value=sentinel):
            ears.loop_turn(None, settings, None, FakeVAD())
        self.assertIs(seen["judge"], sentinel)

    def test_dictation_judges_its_pieces(self):
        from voice import commands, timeline

        seen = []
        heard = ears.Heard(np.ones(16000, dtype=np.float32), True)
        sentinel = object()
        with mock.patch.object(ears, "record", side_effect=lambda *a, **k: seen.append(k) or heard), \
                mock.patch.object(ears.ownvoice, "for_turn", return_value=sentinel), \
                mock.patch.object(ears, "talk_ok", return_value=(True, 0.9)), \
                mock.patch.object(ears, "transcribe", return_value="Hello. Dictation done."), \
                mock.patch.object(ears, "listening", return_value=True), \
                mock.patch.object(ears, "set_state"), mock.patch.object(ears.chime, "play"), \
                mock.patch.object(commands, "desktop_state", return_value={"windows": []}), \
                mock.patch.object(commands, "type_text", return_value=(True, "")):
            ears.dictate(None, mock.Mock(get=mock.Mock(return_value={})), None, timeline.Timeline())
        self.assertIs(seen[0]["judge"], sentinel)

    def test_the_slack_follows_the_speakers_during_each_window(self):
        tap = mock.Mock()
        tap.level.return_value = 0.2
        with mock.patch.object(ears.ownvoice, "for_turn", side_effect=lambda cfg, slack, sound: sound):
            sound = ears.own_voice({}, tap)
        self.assertEqual(sound(5.0, 6.6), ears.BARGE_SLACK)
        self.assertEqual(tap.level.call_args.kwargs["since"], 5.0)
        self.assertEqual(tap.level.call_args.kwargs["until"], 6.6)
        with mock.patch.object(ears.ownvoice, "for_turn", side_effect=lambda cfg, slack, sound: sound):
            self.assertEqual(ears.own_voice({}, None)(5.0, 6.6), 0.0)


if __name__ == "__main__":
    unittest.main()
