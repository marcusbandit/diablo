import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import fast, skills


class TestTasks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(skills, "TASKS_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def task(self, tid, name, status):
        (Path(self.tmp.name) / f"{tid}.name").write_text(name)
        (Path(self.tmp.name) / f"{tid}.status").write_text(status)

    def test_list_reports_running_and_recent(self):
        self.task("task-1", "fix the bell", "running")
        self.task("task-2", "check disk", "done")
        kind, ok, text = skills.run("list_tasks", {}, {})
        self.assertEqual((kind, ok), ("tell", True))
        self.assertIn("running: fix the bell (running)", text)
        self.assertIn("check disk (done", text)

    def test_empty_board(self):
        self.assertEqual(skills.run("list_tasks", {}, {})[2], "no tasks on the board")

    def test_cancel_by_part_of_the_name(self):
        self.task("task-1", "fix the bell", "running")
        self.task("task-2", "check disk", "queued")
        kind, ok, text = skills.run("cancel_task", {"name": "bell"}, {})
        self.assertTrue(ok)
        self.assertEqual((Path(self.tmp.name) / "task-1.status").read_text(), "canceled")
        self.assertEqual((Path(self.tmp.name) / "task-2.status").read_text(), "queued")

    def test_cancel_unknown_fails_and_names_the_live_ones(self):
        self.task("task-1", "fix the bell", "running")
        kind, ok, text = skills.run("cancel_task", {"name": "lamp"}, {})
        self.assertFalse(ok)
        self.assertIn("fix the bell", text)


class TestLightsAndUnknown(unittest.TestCase):
    def test_light_success_is_an_act(self):
        with mock.patch("voice.skills.hue.hue_light", return_value="corner lamp: off"):
            self.assertEqual(
                skills.run("set_light", {"action": "off", "target": "corner"}, {"cfg": {}}),
                ("act", True, "corner lamp: off"),
            )

    def test_light_refusal_fails(self):
        with mock.patch("voice.skills.hue.hue_light", return_value="the bridge did not answer, sir"):
            self.assertFalse(skills.run("set_light", {"action": "on", "target": "all"}, {"cfg": {}})[1])

    def test_unknown_skill_fails_cleanly(self):
        self.assertEqual(skills.run("fly", {}, {})[1], False)

    def test_every_spec_is_a_function_tool(self):
        for spec in skills.specs():
            self.assertEqual(spec["type"], "function")
            self.assertIn("parameters", spec["function"])

    def test_home_place_prefers_the_setting(self):
        self.assertEqual(skills._home_place({"weather_place": "Aarhus"}), "Aarhus")


class TestReminders(unittest.TestCase):
    def setUp(self):
        import remind

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(remind, "store_path", return_value=Path(self.tmp.name) / "r.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_list_cancel(self):
        kind, ok, text = skills.run("add_reminder", {"when": "+20m", "message": "stretch"}, {})
        self.assertTrue(ok)
        self.assertIn("stretch", text)
        kind, ok, text = skills.run("list_reminders", {}, {})
        self.assertEqual(kind, "tell")
        self.assertIn("stretch", text)
        kind, ok, text = skills.run("cancel_reminder", {"which": "stretch"}, {})
        self.assertTrue(ok)
        self.assertEqual(skills.run("list_reminders", {}, {})[2], "no reminders pending")

    def test_bad_time_fails(self):
        self.assertFalse(skills.run("add_reminder", {"when": "soonish", "message": "x"}, {})[1])


class TestToolStream(unittest.TestCase):
    def test_parse_events_gathers_tool_calls_and_text(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo."}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"set_light","arguments":"{\\"action\\": "}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"off\\", \\"target\\": \\"corner\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        calls = []
        text = "".join(fast.parse_events(lines, calls))
        self.assertEqual(text, "Hello.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "set_light")
        self.assertEqual(calls[0]["args"], {"action": "off", "target": "corner"})

    def test_tool_turn_shapes_the_follow_up(self):
        calls = [{"id": "c1", "name": "list_tasks", "arguments": "{}", "args": {}}]
        follow = fast.tool_turn([{"role": "user", "content": "tasks?"}], calls, ["nothing running"])
        self.assertEqual(follow[1]["role"], "assistant")
        self.assertEqual(follow[1]["tool_calls"][0]["function"]["name"], "list_tasks")
        self.assertEqual(follow[2], {"role": "tool", "tool_call_id": "c1", "content": "nothing running"})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestHandsAndPizza(unittest.TestCase):
    def test_home_defaults_to_the_city(self):
        self.assertEqual(skills._home_place({}), "Copenhagen")
        self.assertEqual(skills._home_place({"home_place": "Aarhus"}), "Aarhus")

    def test_pizza_opens_the_configured_page(self):
        with mock.patch("voice.apps.open_url", return_value=(True, "opened x")) as opened:
            kind, ok, text = skills.run("order_pizza", {}, {"cfg": {"pizza_url": "https://pizza.test"}})
        self.assertEqual((kind, ok), ("act", True))
        self.assertEqual(opened.call_args[0][0], {"url": "https://pizza.test"})

    def test_pizza_has_its_own_bank(self):
        self.assertEqual(skills.bank_for(["set_light", "order_pizza"]), "pizza")
        self.assertEqual(skills.bank_for(["set_light"]), "done")
        self.assertEqual(skills.bank_for(["take_photo"]), "camera")

    def test_typing_goes_through_his_hands(self):
        with mock.patch("voice.desktop.type_text", return_value="ok"):
            self.assertEqual(skills.run("type_text", {"text": "hi"}, {})[1], True)
        with mock.patch("voice.desktop.press_key", return_value="unknown key zz"):
            kind, ok, text = skills.run("press_keys", {"combo": "zz"}, {})
        self.assertFalse(ok)
        self.assertIn("unknown key", text)

    def test_scroll_up_is_negative(self):
        with mock.patch("voice.desktop.mouse_scroll", return_value="ok") as scrolled:
            skills.run("scroll", {"direction": "up", "amount": 3}, {})
        self.assertEqual(scrolled.call_args[0][0], -3)

    def test_modules_registered(self):
        for name in ("take_photo", "open_app", "media_control", "order_pizza"):
            self.assertIn(name, skills.BY_NAME)
