import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import settings
from voice import agent, fast


class TestSentences(unittest.TestCase):
    def test_regroups_deltas_into_sentences_as_they_complete(self):
        deltas = ["Hel", "lo there. How", " are you? Fine", " then"]
        self.assertEqual(list(fast.sentences(deltas)), ["Hello there.", "How are you?", "Fine then"])

    def test_handles_japanese_terminators(self):
        self.assertEqual(list(fast.sentences(["はい。 かしこまりました。"])), ["はい。", "かしこまりました。"])

    def test_empty_stream_yields_nothing(self):
        self.assertEqual(list(fast.sentences([])), [])


class TestRoute(unittest.TestCase):
    def test_task_line(self):
        kind, name, prompt = fast.route("TASK: Reminder bell | Build a reminder in remind.py.\nUse cron.")
        self.assertEqual(kind, "task")
        self.assertEqual(name, "Reminder bell")
        self.assertEqual(prompt, "Build a reminder in remind.py. Use cron.")

    def test_think_line(self):
        kind, name, prompt = fast.route("THINK: Why does the mic hear him twice?")
        self.assertEqual((kind, name), ("think", None))
        self.assertEqual(prompt, "Why does the mic hear him twice?")

    def test_do_line_rejects_unknown_tools(self):
        self.assertEqual(fast.route('DO: {"tool": "run_shell", "args": {}}')[0], "say")
        self.assertEqual(fast.route("DO: not json")[0], "say")

    def test_plain_reply(self):
        self.assertEqual(fast.route("  Always running, sir.  "), ("say", None, "Always running, sir."))

    def test_forced_think_phrases(self):
        self.assertTrue(fast.forced_think("Think about this, what should I study?"))
        self.assertTrue(fast.forced_think("Ask your deep mind about it"))
        self.assertFalse(fast.forced_think("What time is it?"))

    def test_looks_routed_on_partial_heads(self):
        self.assertTrue(fast.looks_routed("TA"))
        self.assertTrue(fast.looks_routed("THINK: why"))
        self.assertFalse(fast.looks_routed("Always"))


class TestMessages(unittest.TestCase):
    def test_system_prompt_carries_facts_tasks_and_time(self):
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        msgs = fast.build_messages(
            "what now", history, ["Marcus speaks Japanese"], ["bell: done"], now=datetime(2026, 9, 13, 7, 30)
        )
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Marcus speaks Japanese", msgs[0]["content"])
        self.assertIn("bell: done", msgs[0]["content"])
        self.assertIn("Sunday 07:30", msgs[0]["content"])
        self.assertEqual(msgs[1:3], history)
        self.assertEqual(msgs[-1], {"role": "user", "content": "what now"})
        for bad in ("—", "–"):
            self.assertNotIn(bad, msgs[0]["content"])


class TestResolveBrain(unittest.TestCase):
    def test_imp_rank_with_a_provider_rides_that_provider(self):
        cfg = {
            "rank.imp": "openrouter|qwen/big",
            "provider.openrouter.base_url": "https://openrouter.example/v1",
            "provider.openrouter.api_key": "ok",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "lk",
            "llm_model": "llm-model",
        }
        self.assertEqual(fast.resolve_brain(cfg), ("https://openrouter.example/v1", "ok", "qwen/big"))

    def test_imp_rank_with_an_unknown_provider_falls_back_to_the_default_provider(self):
        cfg = {
            "rank.imp": "nope|some-model",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "lk",
            "llm_model": "llm-model",
        }
        self.assertEqual(fast.resolve_brain(cfg), ("https://llm.example/v1", "lk", "some-model"))

    def test_plain_imp_rank_rides_the_default_provider(self):
        cfg = {"rank.imp": "fast-model", "llm_base_url": "https://llm.example/v1/", "llm_api_key": "lk", "llm_model": "llm-model"}
        self.assertEqual(fast.resolve_brain(cfg), ("https://llm.example/v1", "lk", "fast-model"))

    def test_unset_imp_rank_falls_back_to_llm_settings(self):
        cfg = {"llm_base_url": "https://llm.example/v1/", "llm_api_key": "lk", "llm_model": "llm-model"}
        self.assertEqual(fast.resolve_brain(cfg), ("https://llm.example/v1", "lk", "llm-model"))

    def test_empty_cfg_resolves_to_empty(self):
        self.assertEqual(fast.resolve_brain({}), ("", "", ""))


class TestSettingsDefaults(unittest.TestCase):
    def test_rank_defaults_exist(self):
        for key in ("rank.imp", "rank.baron", "rank.marquis", "rank.duke", "rank.lord"):
            self.assertIn(key, settings.DEFAULTS, f"missing default: {key}")
            self.assertEqual(settings.DEFAULTS[key], "")


class TestAgentTools(unittest.TestCase):
    def test_swap_rank_is_a_tool_and_writes_through_the_hub(self):
        names = [tool["function"]["name"] for tool in agent.TOOLS]
        self.assertIn("swap_rank", names)
        with mock.patch.object(agent.hubclient, "set_setting") as set_setting:
            self.assertEqual(agent._execute("swap_rank", {"model": "openrouter|qwen/big"}, {}), "rank swapped")
        set_setting.assert_called_once_with({}, "rank.imp", "openrouter|qwen/big")


if __name__ == "__main__":
    unittest.main(verbosity=2)
