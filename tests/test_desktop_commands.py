"""Plain voice commands Jev picks without a model, the ears running them, and the voice loop."""

import asyncio
import unittest
import numpy as np
from unittest import mock

from hub import brains, jev, route
from voice import commands, ears

DESKTOP = {
    "windows": [
        {"address": "0xaa", "class": "vesktop", "title": "Discord", "workspace": 2, "focused": True},
        {"address": "0xbb", "class": "Spotify", "title": "Clairo - Juna", "workspace": 3, "focused": False},
    ],
    "players": ["spotify", "firefox.instance_1_253"],
}


def answer(desktop, conf=0.95, **extra):
    row = {"tier": "fast", "tier_confidence": 0.9, "action": "none", "action_confidence": 0.9,
           "desktop": desktop, "desktop_confidence": conf, "ms": 400}
    for key, value in extra.items():
        row[key] = value
        row.setdefault(f"{key}_confidence", 0.95)
    return row


_quiet = []


def setUpModule():
    """No sound and no toast on his desktop from a test run."""
    from voice import feedback

    for name in ("done", "failed", "back", "mode", "dictation", "piece", "thinking"):
        patch = mock.patch.object(feedback, name)
        patch.start()
        _quiet.append(patch)
    # Nor any entry in his real desktop history, which "go back" would act on.
    for patch in (mock.patch.object(commands, "_save_history"),
                  mock.patch.object(commands, "HISTORY", commands.collections.deque(maxlen=30))):
        patch.start()
        _quiet.append(patch)


def tearDownModule():
    for patch in _quiet:
        patch.stop()


class TestDesktopAction(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(route.settings, "get", return_value="")
        patch.start()
        self.addCleanup(patch.stop)

    def test_close_this_closes_the_focused_window(self):
        act = route.desktop_action(answer("close_window", window="focused"), "close this app", DESKTOP)
        self.assertEqual(act["window"], "0xaa")
        self.assertEqual(act["did"], "closed vesktop")

    def test_a_named_window_is_found_by_its_choice(self):
        act = route.desktop_action(answer("focus_window", window="w2"), "switch to spotify", DESKTOP)
        self.assertEqual((act["action"], act["window"]), ("focus_window", "0xbb"))

    def test_focus_needs_a_named_window(self):
        self.assertIsNone(route.desktop_action(answer("focus_window", window="focused"), "switch to it", DESKTOP))

    def test_unsure_window_goes_to_claude(self):
        sure = answer("close_window", window="w2")
        sure["window_confidence"] = 0.5
        self.assertIsNone(route.desktop_action(sure, "close the music thing", DESKTOP))

    def test_workspace_needs_a_sure_number(self):
        act = route.desktop_action(answer("workspace", workspace="3"), "workspace three", DESKTOP)
        self.assertEqual((act["workspace"], act["did"]), (3, "switched to workspace 3"))
        self.assertIsNone(route.desktop_action(answer("workspace", workspace="none"), "go to a workspace", DESKTOP))

    def test_media_names_the_player_only_when_listed(self):
        act = route.desktop_action(answer("pause", player="spotify"), "pause spotify", DESKTOP)
        self.assertEqual(act["player"], "spotify")
        act = route.desktop_action(answer("pause", player="vlc"), "pause vlc", DESKTOP)
        self.assertNotIn("player", act)

    def test_unsure_long_or_none_goes_to_claude(self):
        self.assertIsNone(route.desktop_action(answer("mute", conf=0.5), "mute", DESKTOP))
        self.assertIsNone(route.desktop_action(answer("close_window", conf=0.7, window="focused"), "close this", DESKTOP))
        self.assertIsNotNone(route.desktop_action(answer("pause", conf=0.6), "music off", DESKTOP))
        self.assertIsNone(route.desktop_action(answer("none"), "what time is it", DESKTOP))
        long = "could you please maybe turn the sound down a little bit because it is way too loud"
        self.assertIsNone(route.desktop_action(answer("volume_down"), long, DESKTOP))
        self.assertIsNone(route.desktop_action(None, "mute", DESKTOP))
        self.assertIsNone(route.desktop_action(answer("mute"), "mute", None))

    def test_threshold_is_a_setting(self):
        with mock.patch.object(route.settings, "get", side_effect=lambda key: "0.5" if key == "route.desktop_threshold" else ""):
            self.assertIsNotNone(route.desktop_action(answer("close_window", conf=0.6, window="focused"), "close this", DESKTOP))


class TestQuestions(unittest.TestCase):
    def test_windows_and_players_become_choices(self):
        asked = jev.desktop_questions(DESKTOP)
        self.assertEqual(set(asked), {"desktop", "window", "workspace", "player"})
        self.assertIn("(focused now)", asked["window"]["criteria"]["w1"])
        self.assertIn("Spotify", asked["window"]["criteria"]["w2"])
        self.assertEqual(asked["player"]["criteria"]["firefox.instance_1_253"], "firefox")
        self.assertIn("none", asked["desktop"]["criteria"])

    def test_decide_sends_the_extra_questions(self):
        sent = {}

        class Reply:
            is_success = True

            def raise_for_status(self):
                pass

            def json(self):
                return {"answers": {"tier": {"choice": "fast", "confidence": 0.9},
                                    "desktop": {"choice": "mute", "confidence": 0.97}}}

        def post(url, json, headers, timeout):
            sent.update(json)
            return Reply()

        with mock.patch.object(jev, "_key", return_value="k"), mock.patch.object(jev, "_http", return_value=mock.Mock(post=mock.Mock(side_effect=post))), \
                mock.patch.object(jev.settings, "get", return_value=""), \
                mock.patch.object(jev.usage, "record"):
            out = jev.decide("mute", extra=jev.desktop_questions(DESKTOP))
        self.assertIn("desktop", sent["questions"])
        self.assertEqual((out["desktop"], out["desktop_confidence"]), ("mute", 0.97))


class TestHubTurn(unittest.TestCase):
    def test_a_desktop_command_returns_an_act_without_a_model(self):
        async def never(*args, **kwargs):
            raise AssertionError("a model ran")

        with mock.patch.object(brains, "backend", return_value="claude"), \
                mock.patch.object(brains.lockguard, "for_turn", return_value=mock.Mock(line="", restricted=False)), \
                mock.patch.object(brains.context, "recent", return_value=[]), \
                mock.patch.object(brains.context, "card", return_value=("", [])), \
                mock.patch.object(brains.context, "record"), mock.patch.object(brains.context, "answered"), \
                mock.patch.object(brains, "jev_answer", return_value=answer("close_window", window="focused")) as ask_jev, \
                mock.patch.object(brains, "pick_tier", return_value=None), \
                mock.patch.object(brains, "usage_log"), \
                mock.patch.object(route.settings, "get", return_value=""), \
                mock.patch.object(brains, "run_claude", new=never):
            turn = asyncio.run(brains._ask("x", "close this", origin="voice", desktop=DESKTOP))
        self.assertEqual(turn.act["window"], "0xaa")
        self.assertEqual((turn.backend, turn.text), ("direct", "Closed vesktop, sir."))
        self.assertIn("desktop", ask_jev.call_args.args[-1])


class TestLoopWords(unittest.TestCase):
    def test_start_and_stop(self):
        for line in ("Diablo, start voice loop.", "Start the voice loop", "Voice loop.", "enter command mode"):
            self.assertEqual(commands.loop_intent(line), "start", line)
        for line in ("Stop voice loop.", "Stop the loop", "That's all.", "Exit loop.", "voice loop off"):
            self.assertEqual(commands.loop_intent(line), "stop", line)
        for line in ("Stop the music.", "Turn on dark mode", "close this app", "", None):
            self.assertIsNone(commands.loop_intent(line), line)


class TestRun(unittest.TestCase):
    def test_close_focuses_checks_then_closes(self):
        calls = []
        with mock.patch.object(commands.apps, "_dispatch", side_effect=lambda lua: calls.append(lua) or (True, "ok")), \
                mock.patch.object(commands.apps, "_query", return_value=(True, {"address": "0xbb"})):
            ok, _ = commands.run({"action": "close_window", "window": "0xbb", "name": "Spotify"})
        self.assertTrue(ok)
        self.assertIn("address:0xbb", calls[0])
        self.assertEqual(calls[1], "hl.dsp.window.close()")

    def test_close_leaves_a_window_that_would_not_come_forward(self):
        calls = []
        with mock.patch.object(commands.apps, "_dispatch", side_effect=lambda lua: calls.append(lua) or (True, "ok")), \
                mock.patch.object(commands.apps, "_query", return_value=(True, {"address": "0xaa"})):
            ok, why = commands.run({"action": "close_window", "window": "0xbb", "name": "Spotify"})
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)

    def test_bad_input_is_refused(self):
        self.assertFalse(commands.run({"action": "close_window", "window": "0xbb; rm"})[0])
        self.assertFalse(commands.run({"action": "launch_rockets"})[0])

    def test_pause_without_a_player_pauses_them_all(self):
        with mock.patch.object(commands, "_run", return_value=(True, "")) as run:
            commands._run_act({"action": "pause"})
            commands._run_act({"action": "next", "player": "spotify"})
            commands._run_act({"action": "volume_up"})
        self.assertEqual(run.call_args_list[0].args[0], ["playerctl", "-a", "pause"])
        self.assertEqual(run.call_args_list[1].args[0], ["playerctl", "-p", "spotify", "next"])
        self.assertEqual(run.call_args_list[2].args[0][:2], ["wpctl", "set-volume"])


class TestEarsAnswer(unittest.TestCase):
    def test_a_done_act_says_nothing_and_a_failed_one_says_why(self):
        found = {"reply": "Closed vesktop, sir.", "backend": "direct", "act": {"action": "close_window"}}
        with mock.patch.object(ears, "ask", return_value=found), \
                mock.patch.object(commands, "run", return_value=(True, "")):
            self.assertEqual(ears.answer_words({}, "close this", [], None, DESKTOP), "")
        with mock.patch.object(ears, "ask", return_value=found), \
                mock.patch.object(commands, "run", return_value=(False, "no such window")):
            self.assertEqual(ears.answer_words({}, "close this", [], None, DESKTOP), "That didn't work: no such window.")

    def test_direct_lights_are_quiet_unless_asked(self):
        found = {"reply": "Turned all lights off, sir.", "backend": "direct"}
        with mock.patch.object(ears, "ask", return_value=found):
            self.assertEqual(ears.answer_words({}, "lights off", [], None, None), "")
            self.assertEqual(ears.answer_words({"ears.speak_direct": "1"}, "lights off", [], None, None),
                             "Turned all lights off, sir.")

    def test_a_model_reply_is_spoken(self):
        with mock.patch.object(ears, "ask", return_value={"reply": "It will rain.", "backend": "claude"}):
            self.assertEqual(ears.answer_words({}, "weather", [], None, None), "It will rain.")


if __name__ == "__main__":
    unittest.main()


class TestSpeechControl(unittest.TestCase):
    def test_every_key_act_has_words_a_key_and_a_jev_choice(self):
        self.assertEqual(set(route.KEY_SAID), set(commands.KEYS))
        self.assertTrue(set(route.KEY_SAID) <= set(jev.DESKTOP_CRITERIA))

    def test_key_acts_and_the_sure_ones(self):
        with mock.patch.object(route.settings, "get", return_value=""):
            self.assertEqual(route.desktop_action(answer("scroll_down", conf=0.7), "scroll down", DESKTOP)["did"],
                             "scrolled down")
            self.assertIsNone(route.desktop_action(answer("enter", conf=0.7), "press enter", DESKTOP))
            self.assertIsNotNone(route.desktop_action(answer("enter", conf=0.82), "press enter", DESKTOP))

    def test_keys_press_and_pointer_acts_click_or_scroll(self):
        with mock.patch.object(commands.desktop, "press_key", return_value="ok") as press, \
                mock.patch.object(commands.desktop, "mouse_click", return_value="ok") as click, \
                mock.patch.object(commands.desktop, "mouse_scroll", return_value="ok") as scroll:
            self.assertTrue(commands.run({"action": "copy"})[0])
            self.assertTrue(commands.run({"action": "double_click"})[0])
            self.assertTrue(commands.run({"action": "scroll_up"})[0])
        press.assert_called_once_with("ctrl+c")
        click.assert_called_once_with("left", 2)
        scroll.assert_called_once_with(-5)

    def test_mode_words(self):
        for line in ("Diablo, speech control.", "Speech control mode", "accessibility mode", "Start voice loop"):
            self.assertEqual(commands.loop_intent(line), "start", line)
        for line in ("Stop speech control.", "Exit control mode", "Speech control off"):
            self.assertEqual(commands.loop_intent(line), "stop", line)

    def test_type_and_dictation_words(self):
        self.assertEqual(commands.typed_words("Type hello there."), "hello there")
        self.assertEqual(commands.typed_words("Type: see you soon!"), "see you soon!")
        self.assertIsNone(commands.typed_words("Write: see you soon!"))
        self.assertIsNone(commands.typed_words("Typewriters are cool"))
        self.assertTrue(commands.wants_dictation("Take dictation."))
        self.assertFalse(commands.wants_dictation("dictation is hard"))
        self.assertEqual(commands.dictation_end("and we went home. Dictation done."), (True, "and we went home."))
        self.assertEqual(commands.dictation_end("Done dictating"), (True, ""))
        self.assertEqual(commands.dictation_end("I love dictation"), (False, "I love dictation"))


class TestDictate(unittest.TestCase):
    def test_pieces_until_the_end_phrase_are_typed_once(self):
        from voice import timeline

        pieces = iter(["Dear Albert,", "the build is green. Dictation done."])
        heard = ears.Heard(np.ones(16000, dtype=np.float32), True)
        typed = []
        with mock.patch.object(ears, "record", return_value=heard), \
                mock.patch.object(ears, "talk_ok", return_value=(True, 0.9)), \
                mock.patch.object(ears, "transcribe", side_effect=lambda audio, cfg: next(pieces)), \
                mock.patch.object(ears, "listening", return_value=True), \
                mock.patch.object(ears, "set_state"), mock.patch.object(ears.chime, "play"), \
                mock.patch.object(commands, "desktop_state", return_value=DESKTOP), \
                mock.patch.object(commands, "type_text", side_effect=lambda t: typed.append(t) or (True, "")):
            settings = mock.Mock(get=mock.Mock(return_value={}))
            words = ears.dictate(None, settings, None, timeline.Timeline())
        self.assertEqual(words, "")
        self.assertEqual(typed, ["Dear Albert, the build is green."])


class TestControlEndpoint(unittest.TestCase):
    def test_returns_the_act_and_writes_no_message(self):
        from fastapi.testclient import TestClient

        from hub import server

        server.app.dependency_overrides[server.require_token] = lambda: {"master": True}
        self.addCleanup(server.app.dependency_overrides.clear)
        with mock.patch.object(server.jev, "control", return_value=answer("copy", conf=0.9)), \
                mock.patch.object(route.settings, "get", return_value=""), \
                mock.patch.object(server.lockguard, "for_turn", return_value=mock.Mock(restricted=False)), \
                mock.patch.object(server.db, "add_message") as stored:
            reply = TestClient(server.app).post("/v1/voice/control", json={"text": "copy that", "desktop": DESKTOP})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["act"]["action"], "copy")
        stored.assert_not_called()


class TestQueueOrBeside(unittest.TestCase):
    def test_lines_that_point_back_queue_and_separate_ones_run_beside(self):
        sure = {"after": "separate", "after_confidence": 0.96}
        with mock.patch.object(brains.settings, "get", return_value=""):
            self.assertTrue(brains.runs_beside(sure, "Can you please open Spotify?"))
            for line in ("Can you move that program to workspace 3?", "And book it.", "Reply to the one from Albert.",
                         "Put the music one on workspace 2.", "Also restart it if it isn't."):
                self.assertFalse(brains.runs_beside(sure, line), line)
            self.assertFalse(brains.runs_beside({"after": "separate", "after_confidence": 0.5}, "Open Spotify"))
            self.assertFalse(brains.runs_beside({"after": "r1", "after_confidence": 0.9}, "Open Spotify"))
            self.assertFalse(brains.runs_beside(None, "Open Spotify"))

    def test_the_question_lists_what_is_running(self):
        asked = jev.after_question(["Open Blender", "Open Spotify"])
        self.assertEqual(set(asked["after"]["criteria"]), {"separate", "r1", "r2"})
        self.assertIsNone(jev.after_question([]))

    def test_a_side_session_forks_and_is_never_stored(self):
        seen = {}

        async def spawn_proc(*argv, **kwargs):
            seen["argv"] = argv
            raise RuntimeError("stop here")

        session = brains.ClaudeSession("main", fork_from="abc")
        with mock.patch.object(brains, "executable", return_value="/bin/claude"), \
                mock.patch.object(brains, "write_private"), mock.patch.object(brains, "mcp_config", return_value={}), \
                mock.patch.object(brains.lockguard, "hook_settings", return_value="{}"), \
                mock.patch.object(brains.charter, "charter", return_value=""), \
                mock.patch.object(brains.asyncio, "create_subprocess_exec", side_effect=spawn_proc):
            with self.assertRaises(RuntimeError):
                asyncio.run(session.spawn("m", "low", "abc", fork=True))
        self.assertIn("--fork-session", seen["argv"])
        self.assertEqual(seen["argv"][seen["argv"].index("--resume") + 1], "abc")
        self.assertTrue(session.side)


class TestBackground(unittest.TestCase):
    def test_a_quick_answer_comes_back_and_a_slow_one_waits_in_the_background(self):
        from voice import timeline

        with mock.patch.object(ears, "INLINE_WAIT_S", 0.2):
            self.assertEqual(ears.answer_or_background(lambda say: "fast", timeline.Timeline(), "x"), "fast")
            gate = __import__("threading").Event()
            tl = timeline.Timeline()
            self.assertIsNone(ears.answer_or_background(lambda say: gate.wait(2) and "slow", tl, "think about it"))
        self.assertTrue(tl.info["background"])
        self.assertEqual(ears.PENDING.thinking(), 1)
        gate.set()
        ears.PENDING.jobs[0]["done"].wait(2)
        ready = ears.PENDING.speakable()
        self.assertEqual([(job["words"], early, finished) for job, early, finished in ready], [("slow", [], True)])
        self.assertEqual(ears.PENDING.thinking(), 0)

    def test_never_mind_drops_what_is_thinking(self):
        from voice import timeline

        gate = __import__("threading").Event()
        with mock.patch.object(ears, "INLINE_WAIT_S", 0.05):
            ears.answer_or_background(lambda say: gate.wait(2) and "late", timeline.Timeline(), "slow one")
        with mock.patch.object(ears, "cancel_thinking", wraps=lambda cfg: ears.PENDING.drop_all()) as cancel:
            words = ears.local_line(None, mock.Mock(get=mock.Mock(return_value={})), None, "Never mind.", timeline.Timeline())
        self.assertEqual(words, "")
        cancel.assert_called_once()
        gate.set()
        ears.PENDING.jobs[0]["done"].wait(2)
        self.assertEqual(ears.PENDING.speakable(), [])


class TestFirstLine(unittest.TestCase):
    def tearDown(self):
        ears.PENDING.jobs.clear()

    def test_the_first_line_is_spoken_before_the_answer_and_never_twice(self):
        from voice import timeline

        gate, said = __import__("threading").Event(), []

        def work(say):
            say("On it, launching that on banditbox.")
            gate.wait(2)
            return "Done, the thread is running."

        tl = timeline.Timeline()
        with mock.patch.object(ears, "INLINE_WAIT_S", 0.05):
            self.assertIsNone(ears.answer_or_background(work, tl, "launch it"))
        speak = lambda mic, speaker, words, cfg, detector, tap, tl=None, first=False: said.append((words, first)) and False
        with mock.patch.object(ears, "say_answer", side_effect=speak), mock.patch.object(ears, "thinking_changed"):
            self.assertTrue(ears.PENDING.has_speech())
            self.assertFalse(ears.say_ready(None, None, {}, None, None))
            self.assertEqual(said, [("On it, launching that on banditbox.", True)])
            self.assertFalse(ears.PENDING.has_speech())
            gate.set()
            ears.PENDING.jobs[0]["done"].wait(2)
            with mock.patch.object(tl, "save"):
                ears.say_ready(None, None, {}, None, None)
        self.assertEqual(said[1:], [("Done, the thread is running.", False)])
        self.assertEqual(ears.PENDING.thinking(), 0)

    def test_a_cut_in_the_first_line_keeps_the_answer_for_later(self):
        from voice import timeline

        tl, gate = timeline.Timeline(), __import__("threading").Event()
        with mock.patch.object(ears, "INLINE_WAIT_S", 0.05):
            self.assertIsNone(ears.answer_or_background(lambda say: say("On it.") or (gate.wait(2) and "Done."), tl, "do it"))
        gate.set()
        ears.PENDING.jobs[0]["done"].wait(2)
        said = []

        def speak(mic, speaker, words, cfg, detector, tap, tl=None, first=False):
            said.append(words)
            return first

        with mock.patch.object(ears, "say_answer", side_effect=speak), mock.patch.object(ears, "thinking_changed"), \
                mock.patch.object(tl, "save"):
            self.assertTrue(ears.say_ready(None, None, {}, None, None))
            self.assertTrue(ears.PENDING.has_speech())
            self.assertFalse(ears.say_ready(None, None, {}, None, None))
        self.assertEqual(said, ["On it.", "Done."])
        self.assertEqual(ears.PENDING.thinking(), 0)

    def test_a_quick_turn_says_both_lines_as_one(self):
        from voice import timeline

        def work(say):
            say("Checking.")
            return "It is sunny."

        with mock.patch.object(ears, "INLINE_WAIT_S", 1.0):
            self.assertEqual(ears.answer_or_background(work, timeline.Timeline(), "weather"), "Checking. It is sunny.")

    def test_answer_words_streams_the_first_line_and_returns_only_the_rest(self):
        from voice import timeline

        heard = []

        def ask(cfg, text, context, shot, desktop, on_say=None):
            on_say("Checking the weather.")
            return {"reply": "Checking the weather.\n\nSunny, 12 degrees.", "spoken_first": "Checking the weather.",
                    "rest": "Sunny, 12 degrees.", "backend": "claude"}

        tl = timeline.Timeline()
        with mock.patch.object(ears, "ask", side_effect=ask):
            words = ears.answer_words({}, "weather", [], None, {}, tl, heard.append)
        self.assertEqual(heard, ["Checking the weather."])
        self.assertEqual(words, "Sunny, 12 degrees.")
        self.assertIn("ack_back", tl.points)

    def test_a_plain_answer_first_is_not_said_again(self):
        def ask(cfg, text, context, shot, desktop, on_say=None):
            on_say("It is half past nine.")
            return {"reply": "It is half past nine.", "spoken_first": "It is half past nine.", "rest": "", "backend": "claude"}

        with mock.patch.object(ears, "ask", side_effect=ask):
            self.assertEqual(ears.answer_words({}, "time", [], None, {}, None, lambda w: None), "")

    def test_read_lines_hands_over_say_and_returns_done(self):
        import json

        seen = []
        lines = [json.dumps({"say": "On it."}), "", json.dumps({"done": {"reply": "On it.\n\nDone."}})]
        self.assertEqual(ears.read_lines(iter(lines), seen.append), {"reply": "On it.\n\nDone."})
        self.assertEqual(seen, ["On it."])
        with self.assertRaises(ValueError):
            ears.read_lines(iter([json.dumps({"error": {"status": 502}})]), seen.append)
        with self.assertRaises(ValueError):
            ears.read_lines(iter([json.dumps({"say": "x"})]), seen.append)


class TestFeedbackAndReopen(unittest.TestCase):
    def test_a_done_act_plays_ok_and_shows_what_happened(self):
        from voice import feedback, timeline

        with mock.patch.object(commands, "run", return_value=(True, "")):
            self.assertEqual(ears.run_act({"action": "copy", "did": "copied"}, timeline.Timeline(), {}), "")
        feedback.done.assert_called_with()

    def test_reopen_brings_back_the_last_closed_app(self):
        with mock.patch.object(commands, "HISTORY", commands.collections.deque(maxlen=30)), \
                mock.patch.object(commands, "_save_history"):
            self.assertFalse(commands.reopen()[0])
            commands.remember("close_window", "closed AniBeam", closed={
                "class": "com.marcusrosado.AniBeam", "initial": "com.marcusrosado.AniBeam", "title": "Home",
                "cmd": ["/usr/bin/anibeam"]})
            with mock.patch.object(commands.apps, "find_app", return_value={"id": "anibeam.desktop", "name": "AniBeam"}), \
                    mock.patch.object(commands.apps, "_launch", return_value=(True, "")) as launch:
                self.assertEqual(commands.reopen(), (True, "reopened AniBeam"))
            launch.assert_called_once_with(["gtk-launch", "anibeam.desktop"])
            self.assertFalse(commands.reopen()[0])

    def test_reopen_is_a_jev_choice_and_an_act(self):
        self.assertIn("reopen_window", jev.DESKTOP_CRITERIA)
        with mock.patch.object(route.settings, "get", return_value=""):
            act = route.desktop_action(answer("reopen_window", conf=0.9), "reopen it", DESKTOP)
        self.assertEqual(act["action"], "reopen_window")

    def test_terminals_paste_with_shift(self):
        self.assertTrue(commands._terminal("kitty"))
        self.assertFalse(commands._terminal("com.t3tools.T3Code"))


class TestHistoryAndUndo(unittest.TestCase):
    def setUp(self):
        for name, value in (("HISTORY", commands.collections.deque(maxlen=30)),):
            patch = mock.patch.object(commands, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(commands, "_save_history")
        patch.start()
        self.addCleanup(patch.stop)

    def test_it_keeps_the_last_thirty(self):
        for n in range(40):
            commands.remember("next_tab", f"tab {n}")
        self.assertEqual(len(commands.HISTORY), 30)
        self.assertEqual(commands.recent(2), ["tab 38", "tab 39"])

    def test_go_back_after_a_move_puts_the_focus_back(self):
        commands.remember("workspace", "switched to workspace 3", {"address": "0xaa", "workspace": 1})
        with mock.patch.object(commands, "_focus", return_value=(True, "")) as focus:
            self.assertEqual(commands.undo_last(), (True, "went back"))
        focus.assert_called_once_with("0xaa")

    def test_flips_and_edits_and_the_order(self):
        commands.remember("new_tab", "opened a new tab")
        commands.remember("type", "typed hi")
        with mock.patch.object(commands.desktop, "press_key", return_value="ok") as press:
            self.assertTrue(commands.undo_last()[0])
            press.assert_called_with("ctrl+z")
            self.assertTrue(commands.undo_last()[0])
            press.assert_called_with("ctrl+w")

    def test_a_moved_window_goes_back_to_its_workspace(self):
        commands.remember("move_to_workspace", "moved it", {"address": "0xbb", "workspace": 2})
        with mock.patch.object(commands.apps, "_dispatch", return_value=(True, "ok")) as dispatch:
            self.assertTrue(commands.undo_last()[0])
        self.assertIn("workspace = 2", dispatch.call_args.args[0])
        self.assertIn("address:0xbb", dispatch.call_args.args[0])

    def test_nothing_recent_falls_back_to_the_apps_undo(self):
        with mock.patch.object(commands.desktop, "press_key", return_value="ok") as press:
            self.assertEqual(commands.undo_last(), (True, "undid it in the app"))
        press.assert_called_once_with("ctrl+z")

    def test_run_records_and_undo_is_not_recorded(self):
        with mock.patch.object(commands, "_active", return_value={"address": "0xaa", "workspace": 1}), \
                mock.patch.object(commands, "_run_act", return_value=(True, "")), \
                mock.patch.object(commands, "_focus", return_value=(True, "")):
            commands.run({"action": "focus_left", "did": "moved left"})
            self.assertEqual(commands.HISTORY[-1]["action"], "focus_left")
            commands.run({"action": "undo"})
        self.assertEqual(len(commands.HISTORY), 1)
        self.assertTrue(commands.HISTORY[-1]["undone"])

    def test_spoken_keys_and_tabs(self):
        self.assertEqual(commands.pressed_combo("Press control 1."), "ctrl+1")
        self.assertEqual(commands.pressed_combo("hit ctrl shift t"), "ctrl+shift+t")
        self.assertIsNone(commands.pressed_combo("Press the button please"))
        self.assertEqual(commands.tab_name("Go to the YouTube tab."), "YouTube")
        self.assertIsNone(commands.tab_name("Go to the next tab."))


class TestThreads(unittest.TestCase):
    THREADS = [{"title": "Diablo Purpose: Rust or Web Frontend", "status": "running", "open": "t3code://threads/e/a"},
               {"title": "Ideas to Replace Evening YouTube", "status": "completed", "open": "t3code://threads/e/b"}]

    def test_a_named_thread_becomes_an_open_act(self):
        desktop = dict(DESKTOP, threads=self.THREADS)
        asked = jev.desktop_questions(desktop)
        self.assertIn("Diablo Purpose", asked["thread"]["criteria"]["t1"])
        with mock.patch.object(route.settings, "get", return_value=""):
            act = route.desktop_action(answer("open_thread", thread="t1"), "go to the working diablo thread", desktop)
            unsure = answer("open_thread", thread="t2")
            unsure["thread_confidence"] = 0.3
            self.assertIsNone(route.desktop_action(unsure, "go to that thread", desktop))
        self.assertEqual(act["link"], "t3code://threads/e/a")

    def test_the_thread_list_is_kept_only_for_top_level_unsettled_ones(self):
        from hub import t3threads

        data = {"threads": [
            {"title": "Mine", "status": "running", "link": "[Mine](t3-thread://v2/env1/abc)", "parentThreadId": None},
            {"title": "/code-review", "status": "idle", "link": "[x](t3-thread://v2/env1/def)", "parentThreadId": "abc"},
            {"title": "Done", "status": "completed", "link": "[Done](t3-thread://v2/env1/ghi)", "settled": True},
            {"title": "Plain", "status": "completed", "link": "https://app.t3.codes/threads/env2/jkl"}]}
        rows = t3threads._voice_rows("banditbox", data)
        self.assertEqual([r["title"] for r in rows], ["Mine", "Plain"])
        self.assertEqual(rows[1]["open"], "t3code://threads/env2/jkl")
        self.assertEqual(rows[0]["open"], "t3code://threads/env1/abc")

    def test_threads_are_asked_about_only_when_the_line_or_focus_points_there(self):
        from hub import t3threads

        self.assertTrue(t3threads.wants_threads("go to the working diablo thread", {}))
        self.assertFalse(t3threads.wants_threads("louder", DESKTOP))
        t3 = {"windows": [{"class": "com.t3tools.T3Code", "focused": True}]}
        self.assertTrue(t3threads.wants_threads("the purpose one", t3))

    def test_open_thread_runs_only_t3_links(self):
        with mock.patch.object(commands.apps, "_launch", return_value=(True, "")) as launch:
            self.assertTrue(commands._run_act({"action": "open_thread", "link": "t3code://threads/e/a", "name": "x"})[0])
            self.assertFalse(commands._run_act({"action": "open_thread", "link": "https://evil.example"})[0])
        launch.assert_called_once_with(["xdg-open", "t3code://threads/e/a"])


class TestEarlyEnd(unittest.TestCase):
    """Jev ends the turn: a sure plain command stops the listening at the first short pause."""

    def run_record(self, blocks, early, probe_text="Next song"):
        from tests.test_endturn import FakeMic, FakeVAD

        calls = []

        class SlowMic(FakeMic):
            # The read runs on a thread; a mic that hands out blocks faster than real time
            # would outrun it, so each block takes a moment, as a real one does.
            def read(self, timeout=1.0):
                import time

                time.sleep(0.004)
                return super().read(timeout)

        def probe(clip):
            calls.append(len(clip))
            return probe_text

        with mock.patch.object(ears, "EARLY_S", 0.3):
            heard = ears.record(SlowMic(blocks), {}, 0.0, FakeVAD(), probe=probe, early=early)
        return heard, calls

    def stream(self, *parts):
        from tests.test_endturn import stream

        return stream(*parts)

    def test_a_sure_command_ends_long_before_the_quiet(self):
        sure = {"act": {"action": "next"}, "confidence": 0.98}
        heard, calls = self.run_record(self.stream((0.8, 1), (3.0, 0)), lambda text: sure)
        self.assertEqual(heard.early, sure)
        self.assertEqual(heard.text, "Next song")
        self.assertLess(len(heard[0]) / ears.wake.RATE, 0.8 + 0.9)
        self.assertEqual(len(calls), 1)

    def test_an_unsure_read_leaves_the_normal_end(self):
        heard, calls = self.run_record(self.stream((0.8, 1), (3.0, 0)), lambda text: {"act": None, "confidence": 0.4})
        self.assertIsNone(heard.early)
        self.assertEqual(heard.text, "Next song")
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(len(heard[0]) / ears.wake.RATE, 0.8 + 1.0)

    def test_talk_that_resumes_throws_the_read_away(self):
        sure = {"act": {"action": "next"}, "confidence": 0.98}
        heard, _ = self.run_record(self.stream((0.8, 1), (0.32, 0), (1.5, 1), (5.0, 0)), lambda text: None,
                                   probe_text="Next song and then turn it up")
        self.assertIsNone(heard.early)
        self.assertGreater(len(heard[0]) / ears.wake.RATE, 0.8 + 0.32 + 1.5)
        self.assertTrue(ears.early_sure(sure))
        self.assertFalse(ears.early_sure({"act": None, "confidence": 0.99}))


class TestDirectJev(unittest.TestCase):
    def test_thread_lines_go_to_the_hub_and_unbound_is_none(self):
        from voice import jevfast

        with mock.patch.dict(jevfast._bound, {"settings": None}):
            self.assertIsNone(jevfast.control("louder", DESKTOP))
        with mock.patch.dict(jevfast._bound, {"settings": mock.Mock()}), \
                mock.patch.object(jevfast, "refresh_threads"), \
                mock.patch.dict(jevfast._threads, {"rows": []}):
            self.assertIsNone(jevfast.control("go to the diablo thread", DESKTOP))
            with mock.patch.object(jev, "control", return_value=answer("volume_up", conf=0.97)), \
                    mock.patch.object(route.settings, "get", return_value=""):
                found = jevfast.control("louder", DESKTOP)
        self.assertEqual((found["act"]["action"], found["path"]), ("volume_up", "direct"))

    def test_the_ears_fall_back_to_the_hub(self):
        with mock.patch.object(ears.jevfast, "control", side_effect=RuntimeError("no key")), \
                mock.patch.object(ears.httpx, "post") as post:
            post.return_value = mock.Mock(json=mock.Mock(return_value={"act": None}), raise_for_status=mock.Mock())
            self.assertEqual(ears.control({"hub_url": "http://hub"}, "louder", DESKTOP), {"act": None})


class TestSounds(unittest.TestCase):
    def test_every_feedback_sound_renders_short_and_soft(self):
        from voice import chime

        for name in ("ok", "back", "mode_on", "mode_off", "dictate_on", "dictate_off", "piece", "thinking"):
            sound = chime.render(name)
            self.assertLess(len(sound) / chime.RATE, 0.8, name)
            self.assertLessEqual(float(np.max(np.abs(sound))), chime.LEVEL + 1e-6, name)
        self.assertLess(np.max(np.abs(chime.render("piece"))), np.max(np.abs(chime.render("ok"))))

    def test_feedback_never_sends_a_notification(self):
        from voice import feedback

        self.assertFalse(hasattr(feedback, "toast"))


class TestDirectThreads(unittest.TestCase):
    def test_a_thread_line_goes_direct_once_the_ears_hold_the_list(self):
        from voice import jevfast

        rows = [{"title": "Diablo Purpose", "status": "running", "open": "t3code://threads/e/a"}]
        seen = {}

        def control(text, desktop):
            seen.update(desktop)
            return answer("open_thread", conf=0.95, thread="t1")

        with mock.patch.dict(jevfast._bound, {"settings": mock.Mock()}), \
                mock.patch.object(jevfast, "refresh_threads"), mock.patch.dict(jevfast._threads, {"rows": rows}), \
                mock.patch.object(jev, "control", side_effect=control), \
                mock.patch.object(route.settings, "get", return_value=""):
            found = jevfast.control("go to the diablo thread", DESKTOP)
        self.assertEqual(seen["threads"], rows)
        self.assertEqual(found["act"]["link"], "t3code://threads/e/a")


class TestOwnSounds(unittest.TestCase):
    def test_diablo_plays_under_his_own_name(self):
        from voice import chime

        self.assertIn('application.name = "Diablo"', " ".join(chime.PLAYER))

    def test_the_hold_never_touches_his_own_sounds(self):
        from voice import speakers

        self.assertTrue(speakers.own({"names": ["Diablo"]}))
        self.assertTrue(speakers.own({"names": ["pw-play"]}))
        self.assertFalse(speakers.own({"names": ["spotify", "Spotify"]}))


class TestContext(unittest.TestCase):
    """2026-11-11: "Down to 40" right after three quieters set the lights to 40, and "the voice
    control could stop" did not stop it."""

    def setUp(self):
        patch = mock.patch.object(commands, "HISTORY", commands.collections.deque(maxlen=30))
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_number_after_a_sound_change_is_the_volume(self):
        self.assertIsNone(commands.volume_target("Down to 40."))
        for _ in range(3):
            commands.remember("volume_down", "turned the sound down")
        self.assertEqual(commands.volume_target("Down to 40."), 40)
        self.assertEqual(commands.volume_target("set it to fifty"), 50)
        self.assertIsNone(commands.volume_target("Dim the lights to 40."))
        self.assertEqual(commands.recent_lines(now=commands.time.time())[-1][:36], "turned the sound down 3 times, last ")

    def test_a_number_that_names_the_sound_needs_no_history(self):
        self.assertEqual(commands.volume_target("Turn the music down to 40."), 40)
        self.assertEqual(commands.volume_target("Volume 30 percent"), 30)

    def test_the_mode_stops_on_loose_words_only_while_it_is_on(self):
        for line in ("The voice control could stop.", "I'm paid.", "Stop.", "That's it", "Voice control, off please"):
            self.assertEqual(commands.loop_intent(line, active=True), "stop", line)
        self.assertIsNone(commands.loop_intent("Stop the music.", active=True))
        self.assertIsNone(commands.loop_intent("Stop."))

    def test_again_replays_the_last_act(self):
        commands.remember("volume_down", "turned the sound down", act={"action": "volume_down"})
        with mock.patch.object(commands, "_run_act", return_value=(True, "")) as run, \
                mock.patch.object(commands, "_active", return_value={}):
            self.assertTrue(commands.run({"action": "again"})[0])
        run.assert_called_once_with({"action": "volume_down"})

    def test_jev_reads_what_was_just_done(self):
        sent = {}

        class Reply:
            is_success = True

            def raise_for_status(self):
                pass

            def json(self):
                return {"answers": {"desktop": {"choice": "again", "confidence": 0.9}}}

        def post(url, json, headers, timeout):
            sent.update(json)
            return Reply()

        with mock.patch.object(jev, "_key", return_value="k"), \
                mock.patch.object(jev, "_http", return_value=mock.Mock(post=mock.Mock(side_effect=post))), \
                mock.patch.object(jev.settings, "get", return_value=""), mock.patch.object(jev.usage, "record"):
            jev.control("a bit more", dict(DESKTOP, recent=["turned the sound down, 3 s ago"]))
        self.assertIn("turned the sound down, 3 s ago", sent["state"])
        with mock.patch.object(route.settings, "get", return_value=""):
            self.assertEqual(route.desktop_action(answer("again"), "a bit more", DESKTOP)["action"], "again")

    def test_a_cancelled_turn_says_nothing(self):
        with mock.patch.object(ears, "ask", return_value={"reply": "That failed: stopped", "backend": "claude"}):
            self.assertEqual(ears.answer_words({}, "something", [], None, None), "")


class TestReviewFindings(unittest.TestCase):
    """The review of PR #209, 2026-11-11."""

    def test_write_is_a_request_not_text_to_paste(self):
        self.assertIsNone(commands.typed_words("Diablo, write a script that backs up my photos"))
        self.assertEqual(commands.typed_words("Type hello there."), "hello there")

    def test_quick_answers_leave_requests_that_mention_the_word(self):
        from voice import quick

        for line in ("Add a weather card to the dashboard", "make a thread for the bus app, next step",
                     "Can you fix the weather widget so it shows tomorrow too please"):
            self.assertIsNone(quick.answer(line), line)
        self.assertIsNotNone(quick.answer("What time is it?"))

    def test_an_early_end_never_sends_or_closes(self):
        for action in ("close_window", "close_tab", "enter"):
            self.assertFalse(ears.early_sure({"act": {"action": action}, "confidence": 0.99}), action)
        self.assertTrue(ears.early_sure({"act": {"action": "next"}, "confidence": 0.95}))

    def test_again_is_only_for_the_newest_fresh_act(self):
        with mock.patch.object(commands, "HISTORY", commands.collections.deque(maxlen=30)):
            commands.remember("close_tab", "closed the tab", act={"action": "close_tab"})
            commands.HISTORY[-1]["at"] -= 3600
            self.assertFalse(commands.again()[0])
            commands.remember("type", "typed hi")
            self.assertFalse(commands.again()[0])

    def test_a_locked_box_runs_no_act_from_the_ears(self):
        from voice import timeline

        with mock.patch.object(ears, "locked", return_value=True), mock.patch.object(commands, "run") as run:
            words = ears.run_act({"action": "enter"}, timeline.Timeline(), {})
        run.assert_not_called()
        self.assertIn("locked", words)
