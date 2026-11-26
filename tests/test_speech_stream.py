import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, context, db, devices, route, sentences, server, standby

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude_partial.jsonl"


def streamed(text, step=3):
    splitter = sentences.Splitter()
    out = []
    for at in range(0, len(text), step):
        out += splitter.feed(text[at:at + step])
    return out + splitter.flush()


class TestSplitter(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(sentences.split(text), expected)
        for step in (1, 2, 5, 17):
            self.assertEqual(streamed(text, step), expected, f"step {step}")

    def test_sentences_end_at_a_stop_followed_by_a_space_or_the_end(self):
        self.check('Checking now. The command printed "hi". That is all!',
                   ["Checking now.", 'The command printed "hi".', "That is all!"])

    def test_a_sentence_is_not_said_until_it_has_ended(self):
        splitter = sentences.Splitter()
        self.assertEqual(splitter.feed("It costs 1"), [])
        self.assertEqual(splitter.feed(".5 euro"), [])
        self.assertEqual(splitter.feed(". Next"), ["It costs 1.5 euro."])
        self.assertEqual(splitter.flush(), ["Next"])

    def test_links_numbers_and_abbreviations_stay_whole(self):
        self.check("See https://example.com/a.b?c=d. Use e.g. this one. Version 1.5 is out.",
                   ["See https://example.com/a.b?c=d.", "Use e.g. this one.", "Version 1.5 is out."])

    def test_code_fences_are_never_spoken_and_list_items_are_said_without_bullets(self):
        self.check("Run this:\n```bash\necho hi. there\n```\nIt prints hi.\n- one item.\n- two.\n3. three.\nDone.",
                   ["Run this:", "It prints hi.", "one item.", "two.", "three.", "Done."])

    def test_each_list_item_is_a_sentence_of_its_own(self):
        self.check("You have three reminders:\n- dentist at 10\n- call mum\n- gym",
                   ["You have three reminders:", "dentist at 10.", "call mum.", "gym."])

    def test_every_kind_of_bullet_is_dropped(self):
        self.check("Steps:\n1. Open the app\n2) Tap save\n  - nested, with a comma\n* star\n+ plus\n• dot\nAfter.",
                   ["Steps:", "Open the app.", "Tap save.", "nested, with a comma.", "star.", "plus.", "dot.", "After."])

    def test_an_item_keeps_its_own_stops(self):
        self.check('- Call mum. She asked\n- he said "hi"\n- really?\n- done.',
                   ["Call mum.", "She asked.", 'he said "hi".', "really?", "done."])

    def test_a_dash_or_number_that_is_not_a_bullet_stays_prose(self):
        self.check("-5 degrees outside\n1.5 euro left", ["-5 degrees outside 1.5 euro left"])

    def test_a_list_inside_a_fence_is_not_spoken(self):
        self.check("Text\n```\n- inside\n```\n- after", ["Text", "after."])

    def test_headings_and_tables_are_not_spoken(self):
        self.check("## Weather\n| a | b |\n|---|---|\nSunny today. 12 degrees.", ["Sunny today.", "12 degrees."])

    def test_a_paragraph_break_ends_a_sentence(self):
        self.check("First paragraph without a stop\n\nSecond one. Yes", ["First paragraph without a stop", "Second one.", "Yes"])

    def test_inline_code_holds_its_stops(self):
        self.check("Type `a. b` here. Next", ["Type `a. b` here.", "Next"])

    def test_a_run_on_is_cut_near_the_limit_at_a_comma(self):
        text = ("This sentence goes on and on, " * 12).strip() + " and ends."
        out = sentences.split(text)
        self.assertGreater(len(out), 1)
        self.assertTrue(all(len(line) <= sentences.LIMIT for line in out))
        self.assertTrue(out[0].endswith(","))
        self.assertEqual(" ".join(out), " ".join(text.split()))
        for step in (1, 7):
            self.assertEqual(streamed(text, step), out)

    def test_a_long_link_is_never_cut(self):
        link = "https://example.com/" + "x" * 300
        self.assertEqual(sentences.split(f"Open {link} now"), [f"Open {link} now"])

    def test_flush_starts_fresh(self):
        splitter = sentences.Splitter()
        splitter.feed("```\ncode")
        self.assertEqual(splitter.flush(), [])
        self.assertEqual(splitter.feed("Back to prose. "), ["Back to prose."])

    def test_unsaid_drops_what_was_said(self):
        self.assertEqual(sentences.unsaid("On it. Done, the lights are off.", ["On it."]), "Done, the lights are off.")
        self.assertEqual(sentences.unsaid("Done.", ["done"]), "")
        self.assertEqual(sentences.unsaid("A. B.", []), "A. B.")

    def test_unsaid_keeps_a_sentence_that_is_only_part_of_one_said(self):
        self.assertEqual(sentences.unsaid("Done.", ["Checking whether the build is done."]), "Done.")
        self.assertEqual(sentences.unsaid("The build is done. Done.", ["The build is done!"]), "Done.")

    def test_unsaid_keeps_list_items(self):
        text = "You have three reminders:\n- dentist at 10\n- call mum\n- gym"
        self.assertEqual(sentences.unsaid(text, ["You have three reminders:"]), "dentist at 10. call mum. gym.")
        self.assertEqual(sentences.unsaid(text, sentences.split(text)), "")


class FakeOut:
    def __init__(self, events):
        self.lines = [(json.dumps(e) + "\n").encode() for e in events]
        self.read = 0

    async def readline(self):
        if not self.lines:
            return b""
        self.read += 1
        return self.lines.pop(0)


def read_events(events):
    session = brains.ClaudeSession("test")
    out = FakeOut(events)
    session.proc = mock.Mock(stdout=out)
    result, heard = brains.Turn(), []

    async def on_say(text):
        heard.append((text, list(result.steps), out.read))

    async def on_step(label):
        pass

    with mock.patch.object(brains.usage, "record_claude", return_value={}), \
            mock.patch.object(brains.settings, "set_setting"):
        asyncio.run(session._read(result, on_step, on_say))
    return result, heard


class TestPartialEvents(unittest.TestCase):
    def test_the_cli_is_asked_for_partial_messages(self):
        captured = {}

        async def spawn(*argv, **kwargs):
            captured["argv"] = argv
            return mock.Mock(stdout=None, stderr=None, returncode=None)

        async def drain():
            return None

        session = brains.ClaudeSession("test")
        session._drain_stderr = drain
        with mock.patch.object(brains, "executable", return_value="/usr/bin/claude"), \
                mock.patch.object(brains, "write_private"), \
                mock.patch.object(brains, "mcp_config", return_value={}), \
                mock.patch.object(brains.lockguard, "hook_settings", return_value="{}"), \
                mock.patch.object(brains.charter, "charter", return_value=""), \
                mock.patch.object(brains.asyncio, "create_subprocess_exec", side_effect=spawn):
            asyncio.run(session.spawn("haiku", "high", None))
        self.assertIn("--include-partial-messages", captured["argv"])

    def test_the_recorded_stream_says_each_sentence_as_it_ends(self):
        events = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        result, heard = read_events(events)
        said = [text for text, _, _ in heard]
        self.assertEqual(said, ["Checking now.", 'The command printed "hi".', "That is the only output it produced."])
        self.assertEqual(heard[0][1], [], "the first line goes before the tool runs")
        final_block = next(n for n, e in enumerate(events, 1)
                           if e["type"] == "assistant" and "That is" in json.dumps(e))
        self.assertLess(heard[1][2], final_block, "a sentence goes out before its whole block is done")
        self.assertEqual(result.said, said)
        self.assertEqual(result.text, 'The command printed "hi". That is the only output it produced.')
        self.assertEqual(result.first_text, "Checking now.")
        self.assertGreater(result.first_said_at, 0)

    def test_a_tool_start_cuts_the_pending_sentence_loose(self):
        def delta(text):
            return {"type": "stream_event", "parent_tool_use_id": None,
                    "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}}

        events = [
            delta("Turning the lights off"),
            {"type": "stream_event", "parent_tool_use_id": None,
             "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "name": "hue_light"}}},
            {"type": "result", "result": "Done."},
        ]
        result, heard = read_events(events)
        self.assertEqual([h[0] for h in heard], ["Turning the lights off"])
        self.assertEqual(heard[0][2], 2)

    def test_subagent_text_is_not_spoken(self):
        events = [
            {"type": "stream_event", "parent_tool_use_id": "toolu_x",
             "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Inner work. "}}},
            {"type": "result", "result": "Done."},
        ]
        result, heard = read_events(events)
        self.assertEqual(heard, [])

    def test_without_partial_events_whole_blocks_are_split(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking the weather. One moment."}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": "weather"}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Sunny."}]}},
            {"type": "result", "result": "Sunny."},
        ]
        result, heard = read_events(events)
        self.assertEqual([(h[0], h[1]) for h in heard],
                         [("Checking the weather.", []), ("One moment.", []), ("Sunny.", ["WebSearch: weather"])])
        self.assertEqual(result.first_text, "Checking the weather. One moment.")
        self.assertEqual(result.text, "Sunny.")


class TestSayLines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def run_turn(self, said, first, final):
        async def ask(conv, text, **kwargs):
            on_say = kwargs.get("on_say")
            for line in said:
                await on_say(line)
            return brains.Turn(text=final, backend="claude", first_text=first, said=list(said))

        with mock.patch.object(server.brains, "ask", side_effect=ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS,
                                        json={"text": "lights", "source": "voice", "stream": True})
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    def test_one_say_line_per_sentence_then_done(self):
        lines = self.run_turn(["On it.", "The lights are off.", "Anything else?"], "On it.",
                              "The lights are off. Anything else?")
        self.assertEqual(lines[:3], [{"say": "On it."}, {"say": "The lights are off."}, {"say": "Anything else?"}])
        done = lines[-1]["done"]
        self.assertEqual(done["reply"], "On it.\n\nThe lights are off. Anything else?")
        self.assertEqual(done["rest"], "")

    def test_the_rest_holds_only_sentences_not_said(self):
        lines = self.run_turn(["Checking."], "Checking.", "Sunny today. Twelve degrees.")
        done = lines[-1]["done"]
        self.assertEqual(done["spoken_first"], "Checking.")
        self.assertEqual(done["rest"], "Sunny today. Twelve degrees.")
        lines = self.run_turn(["Checking.", "Sunny today."], "Checking.", "Sunny today. Twelve degrees.")
        self.assertEqual(lines[-1]["done"]["rest"], "Twelve degrees.")

    def test_a_short_closing_line_inside_a_said_one_is_still_left(self):
        lines = self.run_turn(["Checking whether the build is done."], "Checking whether the build is done.", "Done.")
        self.assertEqual(lines[-1]["done"]["rest"], "Done.")

    def test_a_tool_starting_goes_down_the_stream_so_the_ears_stop_speaking(self):
        async def ask(conv, text, **kwargs):
            await kwargs["on_say"]("I will find where that fix belongs.")
            await kwargs["on_step"]("Grep: body rows")
            return brains.Turn(text="Found it.", backend="claude", first_text="I will find where that fix belongs.",
                               said=["I will find where that fix belongs."])

        with mock.patch.object(server.brains, "ask", side_effect=ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS,
                                        json={"text": "fix the rows", "source": "voice", "stream": True})
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertEqual(lines[0], {"say": "I will find where that fix belongs."})
        self.assertEqual(lines[1], {"step": "Grep: body rows"})
        self.assertIn("done", lines[-1])

    def test_a_streamed_list_is_said_item_by_item(self):
        def delta(text):
            return {"type": "stream_event", "parent_tool_use_id": None,
                    "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}}

        text = "You have three reminders:\n- dentist at 10\n- call mum\n- gym"
        events = [delta(text[at:at + 4]) for at in range(0, len(text), 4)] + [{"type": "result", "result": text}]
        result, heard = read_events(events)
        self.assertEqual([h[0] for h in heard], ["You have three reminders:", "dentist at 10.", "call mum.", "gym."])
        self.assertEqual(sentences.unsaid(text, result.said), "")



DIM = {"tier": "fast", "tier_confidence": 0.95, "action": "dim", "action_confidence": 0.97,
       "target": "all", "target_confidence": 0.9, "amount": "normal", "color": "none", "color_confidence": 0.9,
       "work": "none", "work_confidence": 0.9, "refers_to": "none", "refers_to_confidence": 0.9}


class TestBrainLine(unittest.TestCase):
    """The stream says when the full brain has a spoken turn, so the ears can play a beat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        db.init(str(folder / "diablo.db"))
        db.set_setting("brain.backend", "claude")
        db.set_setting("jev.enabled", "on")
        db.set_setting("threads.enabled", "off")
        config = {"hub_token": TOKEN}
        for patch in (mock.patch.object(server, "get_config", return_value=config),
                      mock.patch.object(devices, "get_config", return_value=config),
                      mock.patch.object(standby, "get_config", return_value=config),
                      mock.patch.object(context, "STATE", folder / "context.json"),
                      mock.patch.object(brains, "STATE", folder),
                      mock.patch.object(route, "LOG", folder / "route.jsonl")):
            patch.start()
            self.addCleanup(patch.stop)
        context._events.clear()
        context._answered.clear()
        standby._state = {"state": "mirror"}
        self.client = TestClient(server.app)
        self.lamps = [{"id": "left", "name": "corner lamp", "on": True, "bri": 254}]

    def hue_light(self, action, target="all", brightness=None, color=None, temperature=None):
        for lamp in self.lamps:
            if target in ("all", lamp["id"]):
                lamp["on"] = action == "on"
                if brightness is not None:
                    lamp["bri"] = round(brightness * 254 / 100)
        return "ok"

    def lines(self, text, jev=None, source="voice"):
        async def claude(conv, message, images, on_step, decision=None, **kwargs):
            if kwargs.get("on_say"):
                await kwargs["on_say"]("The lamp is off.")
            return brains.Turn(backend="claude", text="The lamp is off.", first_text="The lamp is off.",
                               said=["The lamp is off."] if kwargs.get("on_say") else [])

        with mock.patch.object(brains, "jev_answer", return_value=jev), \
                mock.patch.object(brains, "lamps_now", side_effect=lambda *a: [dict(l) for l in self.lamps]), \
                mock.patch.object(brains.hue, "hue_light", side_effect=self.hue_light), \
                mock.patch.object(brains.hue, "lamp_states", side_effect=lambda: [dict(l) for l in self.lamps]), \
                mock.patch.object(brains, "run_claude", side_effect=claude), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS, json={
                "text": text, "source": source, "stream": True, "client_id": f"c-{text}-{source}"})
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    def test_a_line_for_claude_says_so_before_his_first_sentence(self):
        lines = self.lines("Write me a poem about the lamp.")
        self.assertEqual(lines[0]["brain"], "claude")
        self.assertEqual(lines[1], {"say": "The lamp is off."})
        self.assertEqual(lines[-1]["done"]["rest"], "")

    def test_a_direct_answer_has_no_brain_line(self):
        lines = self.lines("Dim the lights.", jev=DIM)
        self.assertFalse(any("brain" in line for line in lines))
        self.assertEqual(lines[-1]["done"]["backend"], "direct")

    def test_only_spoken_turns_get_it(self):
        lines = self.lines("Write me a poem about the lamp.", source="desktop")
        self.assertFalse(any("brain" in line for line in lines))

    def test_a_question_gets_no_beat_and_an_order_does(self):
        self.assertFalse(self.lines("What can you do?")[0]["beat"])
        self.assertFalse(self.lines("Nah, it's fine.")[0]["beat"])
        self.assertTrue(self.lines("Check my mail and tell me what is urgent.")[0]["beat"])


if __name__ == "__main__":
    unittest.main()
