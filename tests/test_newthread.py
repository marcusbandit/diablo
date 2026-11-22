import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, desktop, devices, events, lockguard, newthread, questions, route, server, standby, t3link
from voice import window

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


def project(env, pid, title, updated="2026-11-01T00:00:00Z"):
    return {"env": env, "id": pid, "title": title, "updated": updated}


ROWS = [
    project("boxa", "p-ani-a", "AniBeam", "2026-11-20T00:00:00Z"),
    project("boxa", "p-dia-a", "diablo", "2026-11-21T00:00:00Z"),
    project("boxb", "p-dia-b", "Diablo", "2026-11-19T00:00:00Z"),
    project("boxb", "p-shop", "shop-scan", "2026-11-18T00:00:00Z"),
]
T3_FOCUSED = {"windows": [{"class": "com.t3tools.T3Code", "title": "Anibeam / Comparing two approaches",
                           "address": "0x1", "focused": True},
                          {"class": "zen", "title": "Docs", "address": "0x2", "focused": False}]}


class Launches:
    """A fake T3 MCP: records each launch and hands back a thread like T3 does."""

    def __init__(self):
        self.calls = []

    def __call__(self, env, name, arguments, timeout=None):
        self.calls.append((env, name, arguments))
        return {"threadId": "mcp:t-1", "projectId": arguments["projectId"], "status": "idle",
                "link": f"https://app.t3.codes/threads/env-{env}/mcp%3At-1"}


def opened():
    return mock.Mock(return_value=subprocess.CompletedProcess([], 0, "opened the thread in T3 Code\n", ""))


class TestCommand(unittest.TestCase):
    def test_new_thread_lines_with_and_without_a_name(self):
        for line, name in (("new thread", ""), ("new thread for me", ""), ("make us a new thread please", ""), ("Diablo, make a new thread.", ""), ("create new thread in AniBeam", "anibeam"),
                           ("new thread in Diablo", "diablo"), ("could you start a new thread for the diablo project please", "diablo"),
                           ("[voice] new T3 thread in shop-scan", "shop-scan"), ("open a fresh thread on ani beam", "ani beam")):
            self.assertEqual(newthread.command(line), name, line)

    def test_work_and_hub_threads_are_not_the_command(self):
        for line in ("new thread in diablo to fix the orb", "new chat about the bus app", "start a new thread on boxa to fix the login bug",
                     "what is the new thread about", "open a new tab", "go to the diablo thread"):
            self.assertIsNone(newthread.command(line), line)

    def test_jev_never_turns_it_into_a_new_tab(self):
        answer = {"desktop": "new_tab", "desktop_confidence": 0.99}
        no_settings = mock.patch.object(route.settings, "get", return_value=None)
        no_settings.start()
        self.addCleanup(no_settings.stop)
        self.assertIsNone(route.desktop_action(answer, "new thread in AniBeam", {"windows": []}))
        self.assertEqual(route.desktop_action(answer, "open a new tab", {"windows": []})["action"], "new_tab")


class TestWindow(unittest.TestCase):
    def test_project_from_the_ears_desktop(self):
        self.assertEqual(newthread.window_project(T3_FOCUSED), "Anibeam")

    def test_project_from_the_turn_note(self):
        note = ["Focused window: com.t3tools.T3Code: Anibeam / Comparing Repo Handling (workspace 2)", "Also on screen: zen"]
        self.assertEqual(newthread.window_project(None, note), "Anibeam")

    def test_no_project_when_another_app_has_the_focus(self):
        self.assertEqual(newthread.window_project({"windows": [{"class": "zen", "title": "Diablo / docs", "focused": True}]}), "")
        self.assertEqual(newthread.window_project(None, ["Focused window: kitty: ~/code / thing (workspace 1)"]), "")
        self.assertEqual(newthread.window_project({"windows": [{"class": "com.t3tools.T3Code", "title": "T3 Code", "focused": True}]}), "")


class TestMatch(unittest.TestCase):
    def test_case_spacing_and_hyphens_do_not_matter(self):
        for said in ("AniBeam", "anibeam", "Ani Beam", "ani-beam", "ANIBEAM"):
            self.assertEqual(newthread.one_project(newthread.match(said, ROWS))["id"], "p-ani-a", said)
        self.assertEqual(newthread.one_project(newthread.match("shop scan", ROWS))["id"], "p-shop")

    def test_a_start_or_near_spelling_still_matches(self):
        self.assertEqual(newthread.one_project(newthread.match("ani", ROWS))["id"], "p-ani-a")
        self.assertEqual(newthread.one_project(newthread.match("anibeem", ROWS))["id"], "p-ani-a")
        self.assertEqual(newthread.match("weather", ROWS), [])

    def test_a_name_on_two_boxes_prefers_the_focused_box(self):
        self.assertEqual(newthread.one_project(newthread.match("Diablo", ROWS, prefer="boxb"))["env"], "boxb")
        self.assertEqual(newthread.one_project(newthread.match("Diablo", ROWS, prefer="boxa"))["env"], "boxa")

    def test_with_no_focused_box_the_first_box_that_is_up_wins(self):
        self.assertEqual(newthread.one_project(newthread.match("Diablo", ROWS))["env"], "boxa")
        up = [r for r in ROWS if r["env"] == "boxb"]
        self.assertEqual(newthread.one_project(newthread.match("Diablo", up, prefer="boxa"))["env"], "boxb")

    def test_two_different_titles_are_not_one_project(self):
        rows = ROWS + [project("boxa", "p-ani2", "AniBeam site")]
        self.assertIsNone(newthread.one_project(newthread.match("ani", rows)))

    def test_projects_come_from_every_box_that_is_up(self):
        pages = {("boxa", None): {"projects": [{"id": "a1", "title": "One"}], "nextCursor": 1},
                 ("boxa", 1): {"projects": [{"id": "a2", "title": "Two"}, {"id": "gone", "title": "Old", "deletedAt": "x"}]},
                 ("boxb", None): {"projects": [{"id": "b1", "title": "Three"}]}}
        call = lambda env, name, args, timeout=None: pages[(env, args.get("cursor"))]
        rows = newthread.projects(fresh=True, envs=["boxa", "boxb", "boxc"], call=call, is_up=lambda e: e != "boxc")
        self.assertEqual([(r["env"], r["id"]) for r in rows], [("boxa", "a1"), ("boxa", "a2"), ("boxb", "b1")])


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        self.events = []
        publish = mock.patch.object(events, "publish", side_effect=self.events.append)
        publish.start()
        self.addCleanup(publish.stop)
        self.launches, self.open = Launches(), opened()


class TestRun(StoreCase):
    def run_plan(self, text, desktop=None, context=None, host="boxa", rows=ROWS):
        step = newthread.plan(text, desktop, context, host)
        return newthread.run("main", step, rows, None, self.launches, self.open)

    def test_case_one_the_focused_t3_window_names_the_project(self):
        line, block = self.run_plan("new thread", T3_FOCUSED)
        self.assertEqual(line, "New thread in AniBeam, ready for you.")
        self.assertIsNone(block)
        self.assertEqual(self.launches.calls, [("boxa", "t3_thread_launch", {"projectId": "p-ani-a", "title": "New thread"})])
        self.assertNotIn("message", self.launches.calls[0][2])
        argv = self.open.call_args.args[0]
        self.assertEqual(argv[-2:], ["thread", "t3code://threads/env-boxa/mcp%3At-1"])

    def test_case_two_a_named_project_wins_over_the_window(self):
        line, _ = self.run_plan("new thread in shop scan", T3_FOCUSED)
        self.assertEqual(line, "New thread in shop-scan, ready for you.")
        self.assertEqual(self.launches.calls[0][0], "boxb")

    def test_case_two_the_focused_box_decides_between_two_boxes(self):
        line, _ = self.run_plan("new thread in Diablo", T3_FOCUSED, host="boxb")
        self.assertEqual(line, "New thread in Diablo on boxb, ready for you.")
        self.assertEqual(self.launches.calls[0][2]["projectId"], "p-dia-b")
        self.run_plan("new thread in Diablo", None, host="boxb")
        self.assertEqual(self.launches.calls[1][2]["projectId"], "p-dia-a")

    def test_case_three_no_window_and_no_name_asks(self):
        line, block = self.run_plan("new thread")
        self.assertEqual(line, "Which project?")
        self.assertEqual(self.launches.calls, [])
        self.assertEqual(block["kind"], "question")
        self.assertEqual([o["label"] for o in block["options"]], ["diablo on boxa", "AniBeam", "Diablo on boxb", "shop-scan"])
        stored = questions.get(block["id"])
        self.assertEqual(stored["action"]["kind"], "new_thread")

    def test_an_unknown_name_asks_and_says_so(self):
        line, block = self.run_plan("new thread in weather")
        self.assertEqual(line, "No project called weather. Which project?")
        self.assertEqual(len(block["options"]), 4)

    def test_a_window_project_that_no_box_has_asks(self):
        desktop = {"windows": [{"class": "com.t3tools.T3Code", "title": "Gone / a thread", "focused": True}]}
        _, block = self.run_plan("new thread", desktop)
        self.assertIsNotNone(block)

    def test_one_project_anywhere_needs_no_question(self):
        line, block = self.run_plan("new thread", rows=[ROWS[0]])
        self.assertEqual(line, "New thread in AniBeam, ready for you.")
        self.assertIsNone(block)

    def test_no_box_up_says_so(self):
        self.assertEqual(self.run_plan("new thread", rows=[])[0], "No T3 Code box is up with a project on it.")

    def test_a_failed_focus_still_says_the_thread_is_made(self):
        self.open.return_value = subprocess.CompletedProcess([], 1, "T3 Code did not open\n", "")
        line, _ = self.run_plan("new thread", T3_FOCUSED)
        self.assertEqual(line, "New thread in AniBeam is made, but T3 Code would not come forward.")


class TestAsk(StoreCase):
    NAMES = ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India")
    MANY = [project("boxa", f"p{i}", name, f"2026-11-{20 - i:02d}T00:00:00Z") for i, name in enumerate(NAMES)]

    def answer(self, block, choices=(), text=""):
        found = questions.choose(questions.get(block["id"]), list(choices), text)
        return newthread.answer(found, self.MANY, "main", None, self.launches, self.open)

    def test_pages_of_three_with_more_then_the_rest(self):
        _, block = newthread.ask("main", self.MANY)
        self.assertEqual([o["label"] for o in block["options"]], ["Alpha", "Bravo", "Charlie", "More projects"])
        _, block = self.answer(block, [3])
        self.assertEqual([o["label"] for o in block["options"]], ["Delta", "Echo", "Foxtrot", "More projects"])
        _, block = self.answer(block, [3])
        self.assertEqual([o["label"] for o in block["options"]], ["Golf", "Hotel", "India"])

    def test_every_page_holds_two_to_four_options(self):
        for count in range(2, 15):
            rows = self.MANY[:2] + [project("boxb", f"q{i}", f"Other {i}") for i in range(count - 2)]
            start, pages = 0, 0
            while True:
                shown, more = newthread.page_of(rows, start)
                self.assertTrue(2 <= len(shown) + more <= 4, (count, start))
                if not more:
                    break
                start, pages = start + len(shown), pages + 1
            self.assertEqual(start + len(shown), count)

    def test_projects_group_by_box_with_the_focused_box_first(self):
        listed = newthread.ordered(ROWS, prefer="boxb")
        self.assertEqual([r["env"] for r in listed], ["boxb", "boxb", "boxa", "boxa"])

    def test_a_tap_makes_the_thread(self):
        _, block = newthread.ask("main", self.MANY)
        line, follow = self.answer(block, [1])
        self.assertEqual(line, "New thread in Bravo, ready for you.")
        self.assertIsNone(follow)
        self.assertEqual(self.launches.calls[0][2]["projectId"], "p1")

    def test_his_own_words_match_like_a_name(self):
        _, block = newthread.ask("main", self.MANY)
        line, _ = self.answer(block, text="the hotel one")
        self.assertEqual(line, "New thread in Hotel, ready for you.")

    def test_a_typed_or_spoken_reply_counts_only_when_it_could_name_one(self):
        _, block = newthread.ask("main", self.MANY)
        found = questions.get(block["id"])
        self.assertEqual(newthread.reply_choices(found, "two", self.MANY), [1])
        self.assertEqual(newthread.reply_choices(found, "the hotel one", self.MANY), [])
        self.assertIsNone(newthread.reply_choices(found, "never mind", self.MANY))
        self.assertIsNone(newthread.reply_choices(found, "actually can you tell me what the weather is like in town today please", self.MANY))
        self.assertIsNone(newthread.reply_choices({"action": {"kind": "mail"}}, "two", self.MANY))
        self.assertFalse(newthread.might_answer(found, "can you tell me what the weather is like in town today please"))

    def test_the_diablo_one_names_diablo_not_option_one(self):
        rows = [project("boxa", "p-a", "Alpha", "2026-11-22"), project("boxa", "p-d", "Diablo", "2026-11-21"),
                project("boxa", "p-b", "Bravo", "2026-11-20")]
        _, block = newthread.ask("main", rows)
        found = questions.get(block["id"])
        self.assertEqual(newthread.reply_choices(found, "the diablo one", rows), [])
        found = questions.choose(found, [], "the diablo one")
        line, _ = newthread.answer(found, rows, "main", None, self.launches, self.open)
        self.assertEqual(line, "New thread in Diablo, ready for you.")
        self.assertEqual(self.launches.calls[0][2]["projectId"], "p-d")

    def test_labels_stay_distinct_so_a_tap_lands_on_what_it_showed(self):
        rows = [project("boxa", "p-x", "Diablo", "2026-11-22"), project("boxa", "p-y", "diablo", "2026-11-21"),
                project("boxa", "p-z", "Zulu", "2026-11-20")]
        _, block = newthread.ask("main", rows)
        labels = [o["label"] for o in block["options"]]
        self.assertEqual(len({label.lower() for label in labels}), 3)
        line, _ = newthread.answer(questions.choose(questions.get(block["id"]), [2]), rows, "main", None, self.launches, self.open)
        self.assertEqual(line, "New thread in Zulu, ready for you.")

    def test_options_carry_no_descriptions(self):
        _, block = newthread.ask("main", self.MANY)
        self.assertTrue(all(not o.get("description") for o in block["options"]))

    def test_more_on_a_card_for_a_name_pages_through_its_matches_only(self):
        rows = [project("boxa", f"m{i}", f"Ani {name}", f"2026-11-{20 - i:02d}") for i, name in
                enumerate(("Alpha", "Bravo", "Charlie", "Delta", "Echo"))] + self.MANY
        line, block = newthread.run("main", {"name": "ani", "window": "", "prefer": ""}, rows, None, self.launches, self.open)
        self.assertEqual([o["label"] for o in block["options"]], ["Ani Alpha", "Ani Bravo", "Ani Charlie", "More projects"])
        _, block = newthread.answer(questions.choose(questions.get(block["id"]), [3]), rows, "main", None, self.launches, self.open)
        self.assertEqual([o["label"] for o in block["options"]], ["Ani Delta", "Ani Echo"])

    def test_one_project_and_an_unknown_name_says_so_without_a_card(self):
        line, block = newthread.run("main", {"name": "weather", "window": "", "prefer": ""}, [ROWS[0]], None, self.launches, self.open)
        self.assertEqual((line, block), ("No project called weather.", None))
        self.assertEqual(self.launches.calls, [])

    def test_a_stale_more_falls_back_to_the_first_page(self):
        _, block = newthread.ask("main", self.MANY, start=50)
        self.assertEqual(block["options"][0]["label"], "Alpha")


class TestChat(StoreCase):
    def setUp(self):
        super().setUp()
        for target in (server, devices, standby):
            patch = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            patch.start()
            self.addCleanup(patch.stop)
        for patch in (mock.patch.object(server, "publish"),
                      mock.patch.object(newthread, "projects", return_value=ROWS),
                      mock.patch.object(t3link, "call_tool", self.launches),
                      mock.patch.object(desktop, "desktop_run", self.open)):
            patch.start()
            self.addCleanup(patch.stop)
        self.guard = mock.patch.object(server.lockguard, "for_turn", return_value=lockguard.Guard())
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.brain = mock.patch.object(brains, "ask", side_effect=AssertionError("the brain must not run"))
        self.brain.start()
        self.addCleanup(self.brain.stop)
        self.client = TestClient(server.app)

    def chat(self, text, **extra):
        response = self.client.post("/v1/chat", headers=HEADERS, json={"text": text, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_spoken_new_thread_over_t3_code_runs_without_the_brain(self):
        out = self.chat("Diablo, new thread", source="voice", desktop=T3_FOCUSED)
        self.assertEqual(out["reply"], "New thread in AniBeam, ready for you.")
        self.assertEqual(out["backend"], "t3")
        self.assertEqual(self.launches.calls[0][2]["projectId"], "p-ani-a")

    def test_a_typed_one_with_a_name(self):
        out = self.chat("new thread in shop-scan")
        self.assertEqual(out["reply"], "New thread in shop-scan, ready for you.")

    def test_the_question_card_then_a_spoken_number(self):
        out = self.chat("new thread", source="voice")
        self.assertEqual(out["reply"], "")
        self.assertTrue(out["say"].startswith("Which project gets the new thread? One, diablo on boxa."))
        out = self.chat("number two", source="voice")
        self.assertEqual(out["reply"], "New thread in AniBeam, ready for you.")
        self.assertTrue(out["answered"])

    def test_the_question_card_then_a_tap(self):
        block = self.chat("new thread")["objects"][0]
        response = self.client.post(f"/v1/questions/{block['id']}/answer", headers=HEADERS, json={"choices": [3]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["reply"], "New thread in shop-scan, ready for you.")

    def test_his_own_words_on_the_card(self):
        self.chat("new thread")
        self.assertEqual(self.chat("the shop scan one")["reply"], "New thread in shop-scan, ready for you.")

    def test_an_unrelated_line_with_a_card_open_goes_to_the_brain(self):
        self.chat("new thread")
        self.brain.stop()
        turn = brains.Turn(text="Sunny.", backend="claude")
        with mock.patch.object(brains, "ask", mock.AsyncMock(return_value=turn)) as ask:
            self.chat("what is the weather like in town today and tomorrow and the day after that")
        ask.assert_called_once()

    def test_a_retried_more_tap_gets_its_card_again(self):
        many = [project("boxa", f"p{i}", name) for i, name in enumerate(("Alpha", "Bravo", "Charlie", "Delta", "Echo"))]
        with mock.patch.object(newthread, "projects", return_value=many):
            block = self.chat("new thread")["objects"][0]
            tap = {"choices": [3], "client_id": "tap-1"}
            first = self.client.post(f"/v1/questions/{block['id']}/answer", headers=HEADERS, json=tap).json()
            again = self.client.post(f"/v1/questions/{block['id']}/answer", headers=HEADERS, json=tap).json()
        self.assertEqual(first["objects"][0]["options"][0]["label"], "Delta")
        self.assertEqual(again["objects"], first["objects"])

    def test_a_locked_desk_leaves_it_to_the_brain(self):
        self.guard.stop()
        locked = mock.patch.object(server.lockguard, "for_turn", return_value=lockguard.Guard(restricted=True))
        locked.start()
        self.addCleanup(locked.stop)
        self.brain.stop()
        turn = brains.Turn(text="The screen is locked.", backend="claude")
        with mock.patch.object(brains, "ask", mock.AsyncMock(return_value=turn)) as ask:
            self.chat("new thread in AniBeam")
        ask.assert_called_once()
        self.assertEqual(self.launches.calls, [])


class TestWindowVerb(unittest.TestCase):
    def test_it_opens_the_link_then_focuses_t3_code(self):
        launch = mock.Mock()
        with mock.patch.object(window, "clients", return_value=[{"class": "com.t3tools.T3Code", "address": "0xabc"}]), \
                mock.patch.object(window, "focus", return_value=(True, "")) as focus:
            self.assertEqual(window.thread("t3code://threads/env-1/mcp%3At-1", launch=launch), (True, ""))
        self.assertEqual(launch.call_args.args[0], ["xdg-open", "t3code://threads/env-1/mcp%3At-1"])
        focus.assert_called_once_with("0xabc")

    def test_it_refuses_anything_but_a_thread_link(self):
        launch = mock.Mock()
        self.assertFalse(window.thread("https://evil.example/x", launch=launch)[0])
        launch.assert_not_called()

    def test_it_waits_a_little_for_t3_code_to_start(self):
        rows = iter([[], [], [{"class": "com.t3tools.T3Code", "address": "0xabc"}]])
        with mock.patch.object(window, "clients", side_effect=lambda: next(rows)), \
                mock.patch.object(window, "focus", return_value=(True, "")):
            self.assertTrue(window.thread("t3code://threads/e/t", launch=mock.Mock(), sleep=lambda s: None)[0])
        with mock.patch.object(window, "clients", return_value=[]):
            self.assertEqual(window.thread("t3code://threads/e/t", launch=mock.Mock(), wait=0), (False, "T3 Code did not open"))


if __name__ == "__main__":
    unittest.main()
