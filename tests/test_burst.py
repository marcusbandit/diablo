import asyncio
import threading
import time
import unittest
from unittest import mock

from voice import burst, ears, timeline


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def wait_merged(group, timeout=2.0):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if group.merging is None or group.merging["done"].is_set():
            return
        time.sleep(0.01)


class BurstCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.merged = []

        def merger(turns):
            self.merged.append(turns)
            return "Merged " + " / ".join(t["heard"] for t in reversed(turns)) + ".", "test"

        self.group = burst.Burst(clock=self.clock, merger=merger)

    def start(self, said):
        return self.group.start(lambda say: "", None, said, run=False)

    def finish(self, job, words):
        job["words"] = words
        self.group.finish(job)

    def ready(self):
        speech = self.group.has_speech()
        wait_merged(self.group)
        return speech or self.group.has_speech()


class TestLoneTurn(BurstCase):
    def test_a_lone_answer_is_spoken_the_moment_it_is_in(self):
        job = self.start("what time is it")
        self.assertFalse(self.ready())
        self.finish(job, "Nine.")
        self.assertTrue(self.ready())
        jobs, merged = self.group.take()
        self.assertEqual(jobs, [job])
        self.assertIsNone(merged)
        self.assertEqual(self.merged, [])

    def test_a_lone_turn_says_its_first_line_while_it_works(self):
        job = self.start("launch it")
        job["early"].append("On it.")
        self.assertTrue(self.group.has_speech())
        self.assertEqual(self.group.take_early(), [(job, ["On it."])])
        self.assertFalse(self.group.has_speech())


class TestRapidTurns(BurstCase):
    def test_five_rapid_turns_give_one_reply_after_the_last(self):
        jobs = [self.start(name) for name in "ABCDE"]
        for name, job in zip("ABCD", jobs):
            self.finish(job, f"Reply {name}.")
            self.clock.now += 5
            self.assertFalse(self.ready(), name)
        self.assertEqual(self.group.take(), ([], None))
        self.finish(jobs[-1], "Reply E.")
        self.assertFalse(self.ready())
        self.clock.now += burst.QUIET_S - 0.1
        self.assertFalse(self.ready())
        self.clock.now += 0.2
        self.assertTrue(self.ready())
        taken, merged = self.group.take()
        self.assertEqual(taken, jobs)
        self.assertEqual(merged, "Merged E / D / C / B / A.")
        self.assertEqual(len(self.merged), 1)
        self.assertEqual([t["heard"] for t in self.merged[0]], list("ABCDE"))
        self.assertEqual([t["reply"] for t in self.merged[0]], [f"Reply {n}." for n in "ABCDE"])
        self.assertEqual(self.group.take(), ([], None))
        self.assertEqual(self.group.size(), 0)

    def test_answers_landing_out_of_order_wait_for_every_one(self):
        a, b = self.start("A"), self.start("B")
        self.finish(b, "Reply B.")
        self.clock.now += 10
        self.assertFalse(self.ready())
        self.finish(a, "Reply A.")
        self.clock.now += burst.QUIET_S
        self.assertTrue(self.ready())
        self.assertEqual(self.group.take()[0], [a, b])

    def test_a_turn_that_joins_never_answers_inline(self):
        a = self.start("A")
        self.assertTrue(self.group.alone(a))
        b = self.start("B")
        self.assertFalse(self.group.alone(b))
        self.finish(a, "Reply A.")
        self.finish(b, "Reply B.")
        self.assertFalse(self.group.alone(b))

    def test_an_unsaid_answer_holds_the_next_turn_too(self):
        a = self.start("A")
        self.finish(a, "Reply A.")
        b = self.start("B")
        self.assertFalse(self.group.alone(b))
        self.finish(b, "Reply B.")
        self.clock.now += burst.QUIET_S
        self.assertTrue(self.ready())
        self.assertEqual(self.group.take()[0], [a, b])

    def test_first_lines_stop_once_a_second_turn_joins(self):
        a = self.start("A")
        a["early"].append("On it.")
        self.start("B")
        self.assertEqual(self.group.take_early(), [])
        self.assertFalse(self.group.has_speech())

    def test_unsaid_first_lines_go_into_the_merge(self):
        a, b = self.start("A"), self.start("B")
        a["early"].append("Launching the build.")
        self.finish(a, "It passed.")
        self.finish(b, "Lights off.")
        self.clock.now += burst.QUIET_S
        self.assertTrue(self.ready())
        self.assertEqual(self.merged[0][0]["reply"], "Launching the build. It passed.")

    def test_a_turn_during_the_merge_puts_the_burst_back_and_joins_it(self):
        a, b = self.start("A"), self.start("B")
        self.finish(a, "Reply A.")
        self.finish(b, "Reply B.")
        self.assertFalse(self.ready())
        self.assertIsNotNone(self.group.merging)
        c = self.start("C")
        self.assertIsNone(self.group.merging)
        self.assertEqual(self.group.size(), 3)
        self.finish(c, "Reply C.")
        self.clock.now += burst.QUIET_S
        self.assertTrue(self.ready())
        taken, merged = self.group.take()
        self.assertEqual(taken, [a, b, c])
        self.assertEqual(merged, "Merged C / B / A.")

    def test_a_straggler_is_left_behind_and_speaks_alone_later(self):
        a, b = self.start("A"), self.start("B")
        self.finish(b, "Reply B.")
        self.clock.now += burst.QUIET_S
        self.assertFalse(self.ready())
        self.clock.now += burst.STRAGGLE_S
        self.assertTrue(self.ready())
        taken, merged = self.group.take()
        self.assertEqual(taken, [b])
        self.assertEqual(self.group.thinking(), 1)
        self.finish(a, "Reply A.")
        self.assertTrue(self.ready())
        self.assertEqual(self.group.take(), ([a], None))

    def test_never_mind_drops_a_burst_being_merged(self):
        a, b = self.start("A"), self.start("B")
        self.finish(a, "Reply A.")
        self.finish(b, "Reply B.")
        self.group.has_speech()
        self.assertEqual(self.group.drop_all(), 2)
        self.clock.now += 10
        self.assertFalse(self.ready())
        self.assertEqual(self.group.size(), 0)

    def test_the_quiet_window_comes_from_the_settings(self):
        self.assertEqual(burst.quiet_s({}), burst.QUIET_S)
        self.assertEqual(burst.quiet_s({"ears.burst_quiet_s": "3.5"}), 3.5)
        self.assertEqual(burst.quiet_s({"ears.burst_quiet_s": "nonsense"}), burst.QUIET_S)
        self.assertEqual(burst.quiet_s({"ears.burst_quiet_s": 99}), 15.0)


class TestFallback(unittest.TestCase):
    def test_the_newest_comes_first_and_earlier_ones_briefly(self):
        turns = [{"heard": "A", "reply": "The build passed. All 40 tests are green."},
                 {"heard": "B", "reply": "The lights are off."}]
        self.assertEqual(burst.fallback(turns), "The lights are off. The build passed.")

    def test_a_repeated_answer_is_said_once(self):
        turns = [{"heard": "A", "reply": "Centered."}, {"heard": "B", "reply": "Centered."}]
        self.assertEqual(burst.fallback(turns), "Centered.")

    def test_merge_without_the_hub_falls_back(self):
        turns = [{"heard": "A", "reply": "One done."}, {"heard": "B", "reply": "Two done."}]
        with mock.patch.object(burst.httpx, "post", side_effect=burst.httpx.ConnectError("down")):
            self.assertEqual(burst.merge({"hub_url": "http://hub.example.com"}, turns),
                             ("Two done. One done.", "fallback"))

    def test_one_answer_needs_no_merge(self):
        turns = [{"heard": "A", "reply": ""}, {"heard": "B", "reply": "Two done."}]
        with mock.patch.object(burst.httpx, "post") as post:
            self.assertEqual(burst.merge({"hub_url": "http://hub.example.com"}, turns), ("Two done.", "single"))
        post.assert_not_called()

    def test_merge_takes_the_hubs_words(self):
        turns = [{"heard": "A", "reply": "One done."}, {"heard": "B", "reply": "Two done."}]
        answer = mock.Mock(json=lambda: {"say": "Both done.", "via": "haiku"}, raise_for_status=lambda: None)
        with mock.patch.object(burst.httpx, "post", return_value=answer) as post:
            self.assertEqual(burst.merge({"hub_url": "http://hub.example.com/"}, turns), ("Both done.", "haiku"))
        self.assertEqual(post.call_args.args[0], "http://hub.example.com/v1/voice/merge")
        self.assertEqual(post.call_args.kwargs["json"], {"turns": turns})


class TestEarsSpeakOneReply(unittest.TestCase):
    """Five turns, each slower than INLINE_WAIT_S, through the real answer_or_background and
    say_ready: one say_answer, with the merged reply, after the last one is in."""

    def test_rapid_turns_through_the_ears(self):
        clock = Clock()
        merges = []

        def merger(turns):
            merges.append(turns)
            return "E first, then the rest.", "test"

        group = burst.Burst(clock=clock, merger=merger)
        gates = {name: threading.Event() for name in "ABCDE"}
        said, saved = [], []

        def speak(mic, speaker, words, cfg, detector, tap, tl=None, first=False):
            said.append((words, first))
            return False

        with mock.patch.object(ears, "PENDING", group), mock.patch.object(ears, "INLINE_WAIT_S", 0.05), \
                mock.patch.object(ears, "say_answer", side_effect=speak), \
                mock.patch.object(ears, "thinking_changed"), mock.patch.object(ears, "feedback"), \
                mock.patch.object(timeline.Timeline, "save", lambda tl: saved.append(tl)):
            jobs = []
            for name in "ABCDE":
                def work(say, name=name):
                    if name == "A":
                        say("On it.")
                    gates[name].wait(2)
                    return f"Reply {name}."
                self.assertIsNone(ears.answer_or_background(work, timeline.Timeline(), f"order {name}"))
                jobs.append(group.jobs[-1])
                clock.now += 1
            for name in "ABCD":
                gates[name].set()
                jobs["ABCD".index(name)]["done"].wait(2)
                self.assertFalse(ears.say_ready(None, None, {}, None, None))
            self.assertEqual(said, [])
            gates["E"].set()
            jobs[-1]["done"].wait(2)
            group.has_speech()
            wait_merged(group)
            self.assertFalse(ears.say_ready(None, None, {}, None, None))
            clock.now += burst.QUIET_S
            self.assertFalse(ears.say_ready(None, None, {}, None, None))
        self.assertEqual(said, [("E first, then the rest.", False)])
        self.assertEqual(len(merges), 1)
        self.assertEqual([t["heard"] for t in merges[0]], [f"order {n}" for n in "ABCDE"])
        self.assertEqual(merges[0][0]["reply"], "On it. Reply A.")
        self.assertEqual(len(saved), 5)

    def test_a_quick_lone_turn_still_answers_inline(self):
        group = burst.Burst()
        with mock.patch.object(ears, "PENDING", group), mock.patch.object(ears, "INLINE_WAIT_S", 0.5):
            self.assertEqual(ears.answer_or_background(lambda say: "Nine.", timeline.Timeline(), "time"), "Nine.")
        self.assertEqual(group.size(), 0)

    def test_a_quick_turn_behind_an_unsaid_one_joins_it(self):
        group = burst.Burst()
        gate = threading.Event()
        with mock.patch.object(ears, "PENDING", group), mock.patch.object(ears, "INLINE_WAIT_S", 0.05), \
                mock.patch.object(ears, "thinking_changed"), mock.patch.object(ears, "feedback"):
            self.assertIsNone(ears.answer_or_background(lambda say: gate.wait(2) and "Slow.", timeline.Timeline(), "A"))
            self.assertIsNone(ears.answer_or_background(lambda say: "Quick.", timeline.Timeline(), "B"))
        gate.set()
        self.assertEqual(group.size(), 2)

    def test_never_mind_cancels_a_burst_whose_answers_are_all_in(self):
        clock = Clock()
        group = burst.Burst(clock=clock)
        for name in "AB":
            job = group.start(lambda say: "", None, name, run=False)
            group.finish(job)
        group.has_speech()
        with mock.patch.object(ears, "PENDING", group), \
                mock.patch.object(ears, "cancel_thinking", wraps=lambda cfg: group.drop_all()) as cancel, \
                mock.patch.object(ears, "thinking_changed"), mock.patch.object(ears, "feedback"):
            words = ears.local_line(None, mock.Mock(get=mock.Mock(return_value={})), None, "Never mind.",
                                    timeline.Timeline())
        self.assertEqual(words, "")
        cancel.assert_called_once()
        self.assertEqual(group.size(), 0)


class TestHubMerge(unittest.TestCase):
    def test_haiku_gets_every_turn_oldest_first(self):
        from hub import voicemerge

        seen = []

        async def ask(text):
            seen.append(text)
            return "**The lights are off**, and the build passed."

        turns = [{"heard": "run the build", "reply": "The build passed."},
                 {"heard": "lights off", "reply": "Lights off."}]
        with mock.patch.object(voicemerge.quick_merge, "ask", side_effect=ask):
            found = asyncio.run(voicemerge.merge(turns))
        self.assertEqual(found["say"], "The lights are off, and the build passed.")
        self.assertEqual(found["via"], "haiku")
        self.assertLess(seen[0].index("run the build"), seen[0].index("lights off"))

    def test_a_failed_merge_says_nothing_so_the_ears_fall_back(self):
        from hub import voicemerge

        turns = [{"heard": "a", "reply": "One."}, {"heard": "b", "reply": "Two."}]
        with mock.patch.object(voicemerge.quick_merge, "ask", side_effect=RuntimeError("claude is not installed")):
            self.assertEqual(asyncio.run(voicemerge.merge(turns))["say"], "")

    def test_one_reply_is_passed_through(self):
        from hub import voicemerge

        with mock.patch.object(voicemerge.quick_merge, "ask") as ask:
            found = asyncio.run(voicemerge.merge([{"heard": "a", "reply": ""}, {"heard": "b", "reply": "Two."}]))
        self.assertEqual(found["say"], "Two.")
        ask.assert_not_called()

    def test_the_endpoint(self):
        import tempfile
        from pathlib import Path

        from fastapi.testclient import TestClient

        from hub import db, server, voicemerge

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db.init(str(Path(tmp.name) / "diablo.db"))
        server.app.dependency_overrides[server.require_token] = lambda: {"master": True}
        self.addCleanup(server.app.dependency_overrides.clear)

        async def ask(text):
            return "Both done."

        with mock.patch.object(voicemerge.quick_merge, "ask", side_effect=ask):
            answer = TestClient(server.app).post("/v1/voice/merge", json={"turns": [
                {"heard": "a", "reply": "One."}, {"heard": "b", "reply": "Two."}]})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["say"], "Both done.")


if __name__ == "__main__":
    unittest.main()
