import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, devices, server, standby

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class OriginCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def pair(self, name):
        token = f"device-{name}"
        db.create_device(name, devices._hash(token))
        return {"X-Diablo-Token": token}

    def chat(self, headers, **body):
        ask = mock.AsyncMock(return_value=brains.Turn(text="Done.", backend="claude"))
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=headers, json={"text": "hi", **body})
        self.assertEqual(response.status_code, 200)
        return ask.call_args.kwargs["origin"]


class TestOriginFor(unittest.TestCase):
    def test_machine_token_keeps_the_old_wording(self):
        self.assertEqual(devices.origin_for({"master": True}, "phone"), "phone app")
        self.assertEqual(devices.origin_for({"master": True}, ""), "fleet app")

    def test_paired_device_names_client_and_device(self):
        caller = {"master": False, "device": {"name": "banditbox desktop"}}
        self.assertEqual(devices.origin_for(caller, "desktop"), "desktop app on banditbox desktop")
        self.assertEqual(devices.origin_for(caller, "web"), "web app on banditbox desktop")

    def test_only_real_clients_have_kinds(self):
        self.assertEqual(sorted(devices.KINDS), ["desktop", "phone", "voice", "web"])


class TestChatOrigin(OriginCase):
    def test_paired_desktop_says_client_and_device(self):
        origin = self.chat(self.pair("banditbox desktop"), source="desktop")
        self.assertEqual(origin, "desktop app on banditbox desktop")

    def test_section_follows_the_device(self):
        from hub import sections
        section = sections.live("diablo")
        with mock.patch.object(server.sections, "live", return_value={**section, "id": "workout", "name": "Workout"}), \
                mock.patch.object(server.sections, "note_for", return_value="note"), \
                mock.patch.object(server.sections, "conv_for", return_value="sec-workout"):
            origin = self.chat(self.pair("banditbox desktop"), source="desktop", section="workout")
        self.assertEqual(origin, "desktop app on banditbox desktop, section Workout")

    def test_machine_token_is_unchanged(self):
        self.assertEqual(self.chat(HEADERS, source="phone"), "phone app")


class TestHostLine(unittest.TestCase):
    def test_primary_names_the_host(self):
        with mock.patch.object(brains.socket, "gethostname", return_value="diablo-pi"), \
                mock.patch.object(brains.standby, "is_standby", return_value=False):
            head = brains.prefix("desktop app on banditbox desktop", ["a note"])
        lines = head.split("\n")
        self.assertTrue(lines[0].startswith("[desktop app on banditbox desktop, "))
        self.assertEqual(lines[1], "[running on diablo-pi]")
        self.assertEqual(lines[2], "a note")

    def test_standby_is_marked(self):
        with mock.patch.object(brains.socket, "gethostname", return_value="nemurenai"), \
                mock.patch.object(brains.standby, "is_standby", return_value=True):
            self.assertEqual(brains.host_line(), "[running on nemurenai, standby]")

    def test_charter_is_untouched_by_the_host(self):
        first = brains.charter.charter()
        with mock.patch.object(brains.socket, "gethostname", return_value="elsewhere"):
            brains.prefix("telegram")
        self.assertEqual(brains.charter.charter(), first)


class TestMachineTurns(unittest.TestCase):
    def test_device_named_watcher_is_not_a_machine(self):
        with mock.patch.object(brains.settings, "get", return_value="on"), \
                mock.patch.object(brains.jev, "decide", return_value={"x": 1}) as decide:
            result = brains.jev_answer("hi", "desktop app on watcher tablet", "", ())
        self.assertEqual(result, {"x": 1})
        decide.assert_called_once()

    def test_machine_flag_skips_jev(self):
        with mock.patch.object(brains.settings, "get", return_value="on"), \
                mock.patch.object(brains.jev, "decide") as decide:
            self.assertIsNone(brains.jev_answer("hi", "t3 watcher", "", (), True))
        decide.assert_not_called()

    def test_tier_only_forced_for_machine_flag(self):
        decision = mock.MagicMock(tier="light", handoff=None)
        with mock.patch.object(brains.route, "route", return_value=decision) as route_call, \
                mock.patch.object(brains.route, "log"), mock.patch.object(brains.route, "enabled", return_value=True):
            brains.pick_tier("main", "hi", None, "desktop app on watcher tablet")
            self.assertTrue(route_call.call_args.kwargs["handoff"])
            brains.pick_tier("main", "hi", None, "t3 watcher", machine=True)
            self.assertFalse(route_call.call_args.kwargs["handoff"])

    def test_watchers_pass_the_flag(self):
        source = (Path(brains.__file__).parent / "watch.py").read_text()
        self.assertEqual(source.count("machine=True"), 2)


class TestApiHostLine(unittest.TestCase):
    def test_run_api_hands_the_host_line_to_the_agent(self):
        seen = {}

        def fake(text, cfg, brain, ctx):
            seen.update(ctx)
            return "ok", []

        with mock.patch.object(brains.agent, "run_agent", fake), \
                mock.patch.object(brains.socket, "gethostname", return_value="nemurenai"), \
                mock.patch.object(brains.standby, "is_standby", return_value=True):
            asyncio.run(brains.run_api("main", "hi", "telegram"))
        self.assertEqual(seen["host_line"], "[running on nemurenai, standby]")

    def test_agent_prompt_carries_the_line(self):
        source = (Path(brains.__file__).parent / "agent.py").read_text()
        self.assertIn("ctx.get(\"host_line\")", source)


class SkillCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = Path(self.tmp.name) / "src"
        self.home = Path(self.tmp.name) / "home"
        (self.src / "alpha").mkdir(parents=True)
        (self.src / "alpha" / "SKILL.md").write_text("one")
        patch = mock.patch.object(brains, "SKILLS_SRC", self.src)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict("os.environ", {"DIABLO_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def installed(self, name="alpha"):
        return self.home / ".claude" / "skills" / name / "SKILL.md"


class TestInstallSkills(SkillCase):
    def test_copies_into_the_brain_home(self):
        self.assertEqual(brains.install_skills(), ["alpha"])
        self.assertEqual(self.installed().read_text(), "one")

    def test_unchanged_source_is_not_copied_again(self):
        brains.install_skills()
        with mock.patch.object(brains.shutil, "copytree") as copy:
            brains.install_skills()
        copy.assert_not_called()

    def test_changed_source_replaces_the_copy(self):
        brains.install_skills()
        (self.src / "alpha" / "SKILL.md").write_text("two")
        (self.installed().parent / "stray.txt").write_text("x")
        brains.install_skills()
        self.assertEqual(self.installed().read_text(), "two")
        self.assertFalse((self.installed().parent / "stray.txt").exists())

    def test_removed_skill_is_removed_but_foreign_ones_stay(self):
        brains.install_skills()
        foreign = self.home / ".claude" / "skills" / "mine"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("keep")
        (self.src / "beta").mkdir()
        (self.src / "beta" / "SKILL.md").write_text("b")
        brains.install_skills()
        self.assertTrue(self.installed("beta").exists())
        (self.src / "alpha" / "SKILL.md").unlink()
        (self.src / "alpha").rmdir()
        brains.install_skills()
        self.assertFalse(self.installed().parent.exists())
        self.assertTrue(self.installed("beta").exists())
        self.assertTrue((foreign / "SKILL.md").exists())

    def test_a_failing_copy_is_logged_not_raised(self):
        with mock.patch.object(brains.shutil, "copytree", side_effect=OSError("read only")):
            self.assertIsNone(brains.ensure_skills())

    def test_spawn_does_not_copy(self):
        import inspect
        self.assertNotIn("install_skills", inspect.getsource(brains.ClaudeSession.spawn))

    def test_startup_copies_off_the_loop(self):
        import inspect
        source = inspect.getsource(server.on_startup)
        self.assertIn("asyncio.to_thread(brains.ensure_skills)", source)


class TestShippedSkill(unittest.TestCase):
    def setUp(self):
        self.text = (Path(brains.__file__).parent / "skills" / "diablo-self" / "SKILL.md").read_text()

    def test_short_with_front_matter(self):
        self.assertLess(len(self.text.splitlines()), 120)
        self.assertTrue(self.text.startswith("---\nname: diablo-self\n"))
        self.assertIn("description:", self.text.split("\n---")[0])
        for dash in (chr(0x2014), chr(0x2013)):
            self.assertNotIn(dash, self.text)

    def test_health_check_uses_the_tailnet_address(self):
        self.assertNotIn("127.0.0.1:8755", self.text)
        self.assertIn("tailscale ip -4", self.text)

    def test_telegram_wording_matches_the_bot(self):
        bot = (Path(brains.__file__).parent / "bot.py").read_text()
        self.assertIn('origin="telegram"', bot)
        self.assertIn("telegram voice note", self.text)
        self.assertNotIn("Telegram,", self.text)


if __name__ == "__main__":
    unittest.main()
