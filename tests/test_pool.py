import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pool.brains import (
    Brains,
    ModelError,
    ProviderDown,
    ProviderError,
    RoundTimeout,
    resolve_brain,
)
from pool.panel import FORWARD_PREFIXES, Panel
from pool.pool import (
    Coordinator,
    Models,
    Pool,
    RETAKE_NAME,
    RETAKE_TEXT,
    SILENT_APOLOGY,
    TONGUE_APOLOGY,
    Task,
    WORKER_TONGUE_APOLOGY,
    answer_of,
    build_coordinator_prompt,
    build_continuation_prompt,
    build_language_prompt,
    build_worker_prompt,
    clean_out,
    intent_for,
    parse_line,
    parts_text,
    resolve_model,
)


def text_response(*texts):
    return {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": t} for t in texts]}


DEFAULT_SETTINGS = {
    "llm_base_url": "http://provider.test/v1",
    "llm_api_key": "sekrit",
    "llm_model": "default-model",
}


class FakeClient:
    def __init__(self, responses=None, settings=None):
        self.responses = list(responses or [])
        self.settings_value = dict(DEFAULT_SETTINGS) if settings is None else dict(settings)
        self.sessions = []
        self.messages = []
        self.calls = []
        self.aborts = []
        self.n = 0

    def settings(self):
        return dict(self.settings_value)

    def create_session(self, title, system=None):
        self.n += 1
        sid = f"ses_{self.n}"
        self.sessions.append((sid, title))
        self.messages.append((sid, system))
        return sid

    def message(self, sid, text, brain, timeout):
        self.calls.append({"sid": sid, "text": text, "brain": brain, "timeout": timeout})
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return text_response("done")

    def abort(self, sid):
        self.aborts.append(sid)

    def last_assistant(self, sid):
        return {"info": {}, "parts": []}

    def trim(self, sid, keep):
        pass


def make_models(llm="prov/llm", worker=None, fail=False):
    settings = {"llm_model": llm}
    if worker is not None:
        settings["rank.baron"] = worker
    return Models({}, get_settings=lambda cfg: {} if fail else settings)


class TestPromptAssembly(unittest.TestCase):
    def test_kind_intents_are_the_ported_lines(self):
        self.assertEqual(intent_for("think"), "This message arrived as a THINK line: answer it yourself for speech.")
        self.assertEqual(intent_for("task"), "This message arrived as a TASK line: set the workers on it as given.")
        self.assertEqual(intent_for("msg"), "This message is forwarded talk: judge whether it needs a worker, an answer, or both.")
        self.assertEqual(intent_for("anything"), intent_for("msg"))

    def test_coordinator_prompt_carries_intent_and_his_words(self):
        prompt = build_coordinator_prompt("sweep the crypt", "think")
        self.assertTrue(prompt.startswith("You are Raphael, the Lord of Wisdom"))
        self.assertIn("This message arrived as a THINK line: answer it yourself for speech.", prompt)
        self.assertIn("His words: sweep the crypt", prompt)
        self.assertIn("your final line always starts with ANSWER:.", prompt)

    def test_worker_first_round_prompt(self):
        prompt = build_worker_prompt("sweep the crypt")
        self.assertTrue(prompt.startswith("You are a Diablo, a worker demon dispatched by Raphael"))
        self.assertIn("Do this work yourself with tools, then report concisely: sweep the crypt", prompt)
        self.assertIn("Never end with an ANSWER line, a plain report is all Raphael needs.", prompt)

    def test_worker_continuation_prompt(self):
        prompt = build_continuation_prompt("sweep", "also mind the bats")
        self.assertIn("The task 'sweep' is already underway.", prompt)
        self.assertIn("more information arrived from Marcus: also mind the bats", prompt)
        self.assertIn("Fold the new information in", prompt)
        self.assertNotIn("opencode", prompt)

    def test_language_rerun_prompt(self):
        prompt = build_language_prompt("sweep the crypt")
        self.assertIn("Wrong language: report only in English, Japanese, or Danish.", prompt)
        self.assertIn("The task was: sweep the crypt", prompt)


class TestAnswerExtraction(unittest.TestCase):
    def test_takes_the_last_answer_line(self):
        text = "I shall see to it.\nANSWER: The sweep is underway, sir.\nANSWER: Done, sir."
        self.assertEqual(answer_of(text), "Done, sir.")

    def test_strips_only_the_prefix(self):
        self.assertEqual(answer_of("ANSWER:   At once, sir."), "At once, sir.")

    def test_no_answer_line(self):
        self.assertEqual(answer_of("plain words, no answer"), "")
        self.assertEqual(answer_of(""), "")

    def test_answer_amid_tool_noise(self):
        text = "step one ran\nANSWER: On it, sir.\ntrailing chatter"
        self.assertEqual(answer_of(text), "On it, sir.")


class TestCleanOut(unittest.TestCase):
    def test_joins_text_parts_and_collapses_whitespace(self):
        parts = [{"type": "text", "text": "  first  bit "}, {"type": "text", "text": "second \n bit"}]
        self.assertEqual(clean_out(parts), "first bit second bit")

    def test_strips_ansi(self):
        parts = [{"type": "text", "text": "\x1b[92mcolored\x1b[0m words"}]
        self.assertEqual(clean_out(parts), "colored words")

    def test_keeps_the_last_1500_chars(self):
        parts = [{"type": "text", "text": "x" * 2000 + "TAIL"}]
        self.assertEqual(clean_out(parts), "x" * 1496 + "TAIL")

    def test_falls_back_to_any_text_parts(self):
        parts = [{"type": "reasoning", "text": "thinking aloud"}]
        self.assertEqual(clean_out(parts), "thinking aloud")
        self.assertEqual(clean_out([]), "")

    def test_parts_text_joins_with_newlines_for_the_coordinator(self):
        parts = [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
        self.assertEqual(parts_text(parts), "one\ntwo")


class TestParseLine(unittest.TestCase):
    def test_parse_line_keeps_pipes_in_the_prompt(self):
        self.assertEqual(parse_line("task-1|sweep|clean it | then mop"), ("task-1", "sweep", "clean it | then mop"))

    def test_parse_line_of_a_bare_id(self):
        self.assertEqual(parse_line("task-1"), ("task-1", "", ""))


class TestBrainResolution(unittest.TestCase):
    SETTINGS = {
        "llm_base_url": "http://default.test/v1/",
        "llm_api_key": "default-key",
        "llm_model": "default-model",
        "rank.baron": "fireworks|fw-model",
        "provider.fireworks.base_url": "http://fireworks.test/v1",
        "provider.fireworks.api_key": "fw-key",
    }

    def test_a_rank_resolves_to_its_provider_brain(self):
        self.assertEqual(resolve_brain(self.SETTINGS, "baron"), ("http://fireworks.test/v1", "fw-key", "fw-model"))

    def test_a_provider_brain_splits_on_the_pipe(self):
        self.assertEqual(resolve_brain(self.SETTINGS, "fireworks|any-model"), ("http://fireworks.test/v1", "fw-key", "any-model"))

    def test_a_plain_model_rides_the_default_provider(self):
        self.assertEqual(resolve_brain(self.SETTINGS, "plain-model"), ("http://default.test/v1", "default-key", "plain-model"))

    def test_an_empty_brain_uses_the_default_model(self):
        self.assertEqual(resolve_brain(self.SETTINGS, ""), ("http://default.test/v1", "default-key", "default-model"))

    def test_an_unknown_provider_is_refused(self):
        with self.assertRaises(ModelError):
            resolve_brain(self.SETTINGS, "ghost|any-model")

    def test_missing_default_settings_are_refused(self):
        with self.assertRaises(ModelError):
            resolve_brain({}, "plain-model")

    def test_resolve_model_wraps_the_refusal(self):
        client = FakeClient(settings={})
        with self.assertRaises(ModelError):
            resolve_model(client, "prov/worker")


class TestModelSettings(unittest.TestCase):
    def test_baron_rank_wins_for_workers(self):
        models = make_models(llm="prov/llm", worker="prov/worker")
        self.assertEqual(models.llm, "prov/llm")
        self.assertEqual(models.worker, "prov/worker")

    def test_baron_rank_falls_back_to_llm_model(self):
        models = make_models(llm="prov/llm", worker=None)
        self.assertEqual(models.worker, "prov/llm")

    def test_refresh_failure_keeps_the_last_known_values(self):
        calls = []

        def flaky(cfg):
            calls.append(1)
            if len(calls) > 1:
                return {}
            return {"llm_model": "prov/llm", "rank.baron": "prov/worker"}

        models = Models({}, get_settings=flaky)
        self.assertEqual((models.llm, models.worker), ("prov/llm", "prov/worker"))
        models.refresh()
        self.assertEqual((models.llm, models.worker), ("prov/llm", "prov/worker"))

    def test_refresh_picks_up_new_settings(self):
        state = {"settings": {"llm_model": "prov/old"}}
        models = Models({}, get_settings=lambda cfg: state["settings"])
        self.assertEqual(models.worker, "prov/old")
        state["settings"] = {"llm_model": "prov/new", "rank.baron": "prov/worker"}
        models.refresh()
        self.assertEqual((models.llm, models.worker), ("prov/new", "prov/worker"))


class TestBrains(unittest.TestCase):
    def test_tool_loop_runs_tools_and_returns_the_final_text(self):
        client = Brains({}, get_settings=lambda cfg: dict(DEFAULT_SETTINGS))
        tool_reply = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "run_shell", "arguments": json.dumps({"command": "echo hi"})},
            }],
        }
        final = {"role": "assistant", "content": "the crypt is clean, sir"}
        with mock.patch.object(Brains, "_chat", side_effect=[tool_reply, final]):
            sid = client.create_session("sweep")
            out = client.message(sid, "sweep", ("http://x", "k", "m"), 60)
        self.assertEqual(parts_text(out["parts"]), "the crypt is clean, sir")
        session = client.sessions[sid]
        self.assertEqual(session[-2]["role"], "tool")
        self.assertEqual(session[-2]["content"], "hi")

    def test_a_failed_call_rolls_the_session_back(self):
        client = Brains({}, get_settings=lambda cfg: dict(DEFAULT_SETTINGS))
        with mock.patch.object(Brains, "_chat", side_effect=ProviderDown("down")):
            sid = client.create_session("sweep")
            with self.assertRaises(ProviderDown):
                client.message(sid, "sweep", ("http://x", "k", "m"), 60)
        self.assertEqual(client.sessions[sid], [])

    def test_abort_unwinds_the_round(self):
        client = Brains({}, get_settings=lambda cfg: dict(DEFAULT_SETTINGS))
        final = {"role": "assistant", "content": "too late, sir"}
        with mock.patch.object(Brains, "_chat", side_effect=[final, final]):
            sid = client.create_session("sweep")
            client.abort(sid)
            with self.assertRaises(RoundTimeout):
                client.message(sid, "sweep", ("http://x", "k", "m"), 60)

    def test_status_errors_split_down_from_broken(self):
        client = Brains({}, get_settings=lambda cfg: dict(DEFAULT_SETTINGS))
        with mock.patch("pool.brains.httpx.post") as post:
            post.return_value.status_code = 429
            post.return_value.text = "slow down"
            with self.assertRaises(ProviderDown):
                client._chat("http://x", "k", "m", [], 10)
            post.return_value.status_code = 400
            post.return_value.text = "bad model"
            with self.assertRaises(ProviderError):
                client._chat("http://x", "k", "m", [], 10)

    def test_trim_keeps_the_system_message(self):
        client = Brains({}, get_settings=lambda cfg: dict(DEFAULT_SETTINGS))
        sid = client.create_session("panel", system="be a butler")
        for i in range(40):
            client.sessions[sid].append({"role": "user", "content": str(i)})
        client.trim(sid, 10)
        self.assertEqual(client.sessions[sid][0]["content"], "be a butler")
        self.assertEqual(len(client.sessions[sid]), 11)


class TestTools(unittest.TestCase):
    def test_run_shell_refuses_the_destructive_list(self):
        from pool.brains import run_tool

        self.assertEqual(run_tool("run_shell", {"command": "sudo rm -rf /"}), "refused: destructive command")

    def test_write_read_edit_roundtrip(self):
        from pool.brains import run_tool

        tmp = Path(tempfile.mkdtemp(prefix="diablo-tools-"))
        target = tmp / "note.txt"
        self.assertIn("wrote", run_tool("write_file", {"path": str(target), "content": "hello demon"}))
        self.assertIn("hello demon", run_tool("read_file", {"path": str(target)}))
        self.assertEqual(run_tool("edit_file", {"path": str(target), "old": "hello", "new": "goodbye"}), "edited")
        self.assertIn("goodbye demon", run_tool("read_file", {"path": str(target)}))
        self.assertIn("not in the file", run_tool("edit_file", {"path": str(target), "old": "missing", "new": "x"}))

    def test_unknown_tool_says_so(self):
        from pool.brains import run_tool

        self.assertIn("unknown tool", run_tool("conjure", {}))


class TestPoolMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="diablo-pool-"))
        self.tasks = self.tmp / "tasks"
        self.spawned = []
        self.notified = []
        self.client = FakeClient()
        self.pool = Pool(
            self.client, make_models(worker="prov/worker"), state=self.tmp,
            max_workers=2, round_timeout=600,
            language_ok=lambda text: True,
            notify=lambda tid, name, result: self.notified.append((tid, name, result)),
            spawn=self.spawned.append,
        )
        (self.tmp / "fast.in").touch()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, tid, suffix, content):
        (self.tasks / f"{tid}.{suffix}").write_text(content)

    def read(self, tid, suffix):
        return (self.tasks / f"{tid}.{suffix}").read_text()

    def test_enqueue_writes_queued_files(self):
        task = self.pool.enqueue("task-1|sweep|clean it")
        self.assertEqual(task.name, "sweep")
        self.assertEqual(self.read("task-1", "status"), "queued")
        self.assertEqual(self.read("task-1", "prompt"), "clean it")

    def test_pump_starts_one_task_per_free_slot(self):
        for i in range(3):
            self.pool.enqueue(f"task-{i}|sweep {i}|work {i}")
        self.assertEqual(len(self.spawned), 2)
        self.assertEqual(len(self.pool.queue), 1)
        self.assertEqual(set(self.pool.active), {"task-0", "task-1"})

    def test_pump_refills_when_a_task_releases(self):
        for i in range(3):
            self.pool.enqueue(f"task-{i}|sweep {i}|work {i}")
        released = self.spawned[0]
        self.pool._release(released)
        self.assertEqual(len(self.spawned), 3)
        self.assertEqual(self.spawned[2].id, "task-2")

    def test_run_task_finishes_with_clean_status_and_dispatch(self):
        self.client.responses = [text_response("the crypt is \x1b[92mclean\x1b[0m, sir")]
        task = Task("task-9", "sweep", "clean it")
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.pool.run_task(task)
        self.assertEqual(self.read("task-9", "status"), "done")
        self.assertEqual(self.read("task-9", "clean"), "the crypt is clean, sir")
        self.assertIn("TASKDISPATCH:task-9|sweep|the crypt is clean, sir", (self.tmp / "fast.in").read_text())
        self.assertEqual(self.notified, [("task-9", "sweep", "the crypt is clean, sir")])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0]["brain"], ("http://provider.test/v1", "sekrit", "prov/worker"))
        self.assertEqual(self.client.calls[0]["timeout"], 600)

    def test_run_task_names_the_session_after_the_task(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(self.client.sessions, [("ses_1", "sweep")])

    def test_run_task_folds_the_inbox_into_a_continuation(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)

        def tell_midrun(sid, text, brain, timeout):
            self.client.calls.append({"sid": sid, "text": text})
            if len(self.client.calls) == 1:
                with (self.pool.tasks_dir / "task-9.inbox").open("a") as inbox:
                    inbox.write("[10:00:00] also mind the bats\n")
                return text_response("first report")
            return text_response("final report with the bats folded in")

        self.client.message = tell_midrun
        self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(len(self.client.calls), 2)
        self.assertIn("also mind the bats", self.client.calls[1]["text"])
        self.assertIn("Fold the new information in", self.client.calls[1]["text"])
        self.assertEqual(self.read("task-9", "clean"), "final report with the bats folded in")
        self.assertEqual(self.read("task-9", "status"), "done")

    def test_run_task_discards_canceled_work(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.write("task-9", "status", "canceled")
        self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(self.client.sessions, [])
        self.assertFalse((self.tasks / "task-9.clean").exists())

    def test_cancel_sweep_aborts_a_running_task(self):
        task = Task("task-9", "sweep", "clean it")
        task.sid = "ses_1"
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.pool.active["task-9"] = task
        self.write("task-9", "status", "canceled")
        self.pool.cancel_sweep()
        self.assertEqual(self.client.aborts, ["ses_1"])
        self.pool.cancel_sweep()
        self.assertEqual(self.client.aborts, ["ses_1"], "abort once, not per sweep")

    def test_language_rerun_uses_a_fresh_session(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.client.responses = [text_response("bericht op z’n nederlands"), text_response("the report, in english")]
        checks = []

        def flaky_language(text):
            checks.append(text)
            return len(checks) > 1

        self.pool.language_ok = flaky_language
        self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(len(self.client.sessions), 2, "the rerun is a fresh session")
        self.assertIn("Wrong language: report only in English, Japanese, or Danish.", self.client.calls[1]["text"])
        self.assertIn("The task was: clean it", self.client.calls[1]["text"])
        self.assertEqual(self.read("task-9", "clean"), "the report, in english")

    def test_language_rerun_that_fails_again_apologizes(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.client.responses = [text_response("bericht"), text_response("rapport")]
        self.pool.language_ok = lambda text: False
        self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(self.read("task-9", "clean"), WORKER_TONGUE_APOLOGY)

    def test_broken_model_setting_finishes_with_an_honest_report(self):
        client = FakeClient(settings={})
        pool = Pool(
            client, make_models(llm="no-slash-model", worker="also-broken"), state=self.tmp,
            max_workers=2, round_timeout=600, language_ok=lambda text: True,
            notify=lambda tid, name, result: self.notified.append((tid, name, result)),
            spawn=self.spawned.append,
        )
        pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(self.read("task-9", "status"), "done")
        self.assertIn("worker model setting is broken", self.read("task-9", "clean"))

    def test_provider_outage_retries_and_finishes(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.client.responses = [ProviderDown("down"), text_response("back up, sir")]
        with mock.patch("pool.pool.time.sleep"):
            self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(self.read("task-9", "clean"), "back up, sir")

    def test_server_error_finishes_the_task(self):
        self.pool.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.client.responses = [ProviderError("message failed: 400")]
        self.pool.run_task(Task("task-9", "sweep", "clean it"))
        self.assertEqual(self.read("task-9", "status"), "done")
        self.assertEqual(self.read("task-9", "clean"), "")


class TestCoordinator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="diablo-coord-"))
        self.tasks = self.tmp / "tasks"
        self.tasks.mkdir(parents=True, exist_ok=True)
        (self.tmp / "fast.in").touch()
        self.client = FakeClient([text_response("Working on it.\nANSWER: The sweep is underway, sir.")])
        self.coordinator = Coordinator(
            self.client, make_models(llm="prov/llm"), state=self.tmp, language_ok=lambda text: True
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, tid, suffix, content):
        (self.tasks / f"{tid}.{suffix}").write_text(content)

    def read(self, tid, suffix):
        return (self.tasks / f"{tid}.{suffix}").read_text()

    def test_msg_answer_returns_as_a_raphael_line(self):
        self.write("task-1", "kind", "msg")
        self.coordinator.process("task-1|sweep|clean the crypt")
        self.assertIn(
            "RAPHAEL:task-1|sweep|The sweep is underway, sir.", (self.tmp / "fast.in").read_text()
        )
        self.assertEqual(self.client.calls[0]["brain"][2], "prov/llm")
        self.assertIn("This message is forwarded talk:", self.client.calls[0]["text"])
        self.assertIn("His words: clean the crypt", self.client.calls[0]["text"])

    def test_think_answer_lands_as_a_done_task(self):
        self.write("task-2", "kind", "think")
        self.coordinator.process("task-2|deep thought|why is the sky dark at night")
        self.assertEqual(self.read("task-2", "status"), "done")
        self.assertEqual(self.read("task-2", "clean"), "The sweep is underway, sir.")
        self.assertEqual(self.read("task-2", "name"), "deep thought")
        self.assertIn("This message arrived as a THINK line:", self.client.calls[0]["text"])
        self.assertEqual((self.tmp / "fast.in").read_text(), "", "think answers are not spoken by the tongue")

    def test_task_kind_answer_goes_nowhere(self):
        self.write("task-3", "kind", "task")
        self.coordinator.process("task-3|sweep|clean the crypt")
        self.assertEqual((self.tmp / "fast.in").read_text(), "")
        self.assertFalse((self.tasks / "task-3.clean").exists())

    def test_wrong_language_msg_asks_for_a_retake(self):
        self.write("task-4", "kind", "msg")
        self.coordinator.language_ok = lambda text: False
        self.coordinator.process("task-4|sweep|clean the crypt")
        line = self.coordinator.retake.get_nowait()
        self.assertEqual(line, f"task-4|sweep {RETAKE_NAME}|{RETAKE_TEXT}clean the crypt")
        self.assertEqual((self.tmp / "fast.in").read_text(), "")

    def test_retake_still_in_the_wrong_language_apologizes(self):
        self.write("task-5", "kind", "msg")
        self.coordinator.language_ok = lambda text: False
        self.coordinator.process(f"task-5|sweep {RETAKE_NAME}|{RETAKE_TEXT}clean the crypt")
        self.assertIn(f"RAPHAEL:task-5|sweep {RETAKE_NAME}|{TONGUE_APOLOGY}", (self.tmp / "fast.in").read_text())

    def test_silent_raphael_apologizes_on_msg(self):
        self.write("task-6", "kind", "msg")
        self.client.responses = [{"info": {}, "parts": []}] * 4
        self.coordinator.process("task-6|sweep|clean the crypt")
        self.assertIn(f"RAPHAEL:task-6|sweep|{SILENT_APOLOGY}", (self.tmp / "fast.in").read_text())
        self.assertEqual(len(self.client.calls), 4, "three tries, a rescue session, one more")
        self.assertEqual(len(self.client.sessions), 2, "the first arm, then the rescue session")

    def test_silent_raphael_on_think_writes_nothing(self):
        self.write("task-7", "kind", "think")
        self.client.responses = [{"info": {}, "parts": []}] * 4
        self.coordinator.process("task-7|sweep|clean the crypt")
        self.assertFalse((self.tasks / "task-7.clean").exists())
        self.assertFalse((self.tasks / "task-7.status").exists(), "no status, the old script left it be too")

    def test_truncates_the_line_like_the_old_script(self):
        self.write("task-8", "kind", "msg")
        self.coordinator.process("task-8|sweep|" + "x" * 5000)
        his_words = self.client.calls[0]["text"].split("His words: ", 1)[1]
        self.assertLessEqual(len(his_words), 4000)

    def test_retake_reenters_through_its_own_queue(self):
        self.write("task-4", "kind", "msg")
        self.coordinator.language_ok = lambda text: False
        self.coordinator.process("task-4|sweep|clean the crypt")
        line = self.coordinator.retake.get_nowait()
        self.client.responses = [text_response("ANSWER: At once, sir.")]
        self.coordinator.language_ok = lambda text: True
        self.coordinator.process(line)
        self.assertIn("RAPHAEL:task-4|sweep (retake)|At once, sir.", (self.tmp / "fast.in").read_text())


class TestPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="diablo-panel-"))
        (self.tmp / "fast.in").touch()
        self.client = FakeClient()
        self.panel = Panel(self.client, state=self.tmp, language_ok=lambda text: True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def read_out(self):
        return (self.tmp / "fast.out").read_text()

    def test_a_plain_line_gets_answered_to_fast_out(self):
        self.client.responses = [text_response("Right away, sir.")]
        self.panel.process("diablo, the lights")
        self.assertEqual(self.read_out(), "Right away, sir.")

    def test_a_task_line_forwards_to_raphael_and_says_so(self):
        self.client.responses = [
            text_response("TASK: sweep | clean the crypt"),
            text_response("It is in Raphael's hands, sir."),
        ]
        os.mkfifo(self.tmp / "raphael.in")
        fd = os.open(self.tmp / "raphael.in", os.O_RDWR | os.O_NONBLOCK)
        try:
            self.panel.process("clean the crypt for me")
            forwarded = os.read(fd, 65536).decode()
        finally:
            os.close(fd)
        self.assertIn("TASK: sweep | clean the crypt", forwarded)
        self.assertIn("It is in Raphael's hands, sir.", self.read_out())

    def test_taskdispatch_folds_into_the_session_without_speaking(self):
        self.client.responses = [text_response("noted.")]
        self.panel.process("TASKDISPATCH:task-9|sweep|the crypt is clean, sir")
        self.assertFalse((self.tmp / "fast.out").exists())
        self.assertEqual(len(self.client.calls), 1)
        self.assertIn("Background task 'sweep' just finished", self.client.calls[0]["text"])

    def test_wrong_language_raphael_answer_is_sent_for_a_retake(self):
        self.panel.language_ok = lambda text: False
        os.mkfifo(self.tmp / "raphael.in")
        fd = os.open(self.tmp / "raphael.in", os.O_RDWR | os.O_NONBLOCK)
        try:
            self.panel.process("RAPHAEL:task-1|sweep|het rapport")
            forwarded = os.read(fd, 65536).decode()
        finally:
            os.close(fd)
        self.assertIn("task-1|sweep (retake)|", forwarded)
        self.assertIn("wrong language", forwarded)
        self.assertFalse((self.tmp / "fast.out").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
