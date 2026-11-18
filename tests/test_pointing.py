import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import lockguard, mcp_server, pointing, server
from tests.test_hub_api import HEADERS, ApiCase

_spec = importlib.util.spec_from_file_location("presence_busy_marks", Path(__file__).resolve().parent.parent / "presence" / "busy.py")
busy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(busy)

EXPORT = {"text": "Export", "x": 1200, "y": 300, "w": 80, "h": 30, "source": "atspi"}
FILE = {"text": "File", "x": 20, "y": 10, "w": 40, "h": 24, "source": "atspi"}


def finder(table, delays=None, calls=None):
    def find(text, window):
        if calls is not None:
            calls.append((text, window, time.monotonic()))
        if delays and text in delays:
            time.sleep(delays[text])
        return {"matches": table.get(text, []), "ms": 5}

    return find


class TestCleaning(unittest.TestCase):
    def test_marks_keep_their_kind_and_a_target_or_a_box(self):
        beats = pointing.clean_beats([{"say": "  Here  it is ", "marks": [
            {"kind": "ring", "target": {"text": " Export ", "near": "Share"}},
            {"kind": "arrow", "target": "Export"},
            {"kind": "underline", "target": {"box": {"x": 1, "y": 2, "w": 300, "h": 18}}},
            {"kind": "num", "n": 400, "point": {"x": 5, "y": 6}},
            {"kind": "zap", "target": "Export"},
            {"kind": "ring"},
            {"kind": "label", "target": "Export"},
        ]}])
        self.assertEqual(len(beats), 1)
        marks = beats[0]["marks"]
        self.assertEqual(beats[0]["say"], "Here it is")
        self.assertEqual(marks[0], {"kind": "ring", "target": {"text": "Export", "window": "front", "near": "Share"}})
        self.assertEqual(marks[1]["target"]["text"], "Export")
        self.assertEqual(marks[2]["box"], {"x": 1.0, "y": 2.0, "w": 300.0, "h": 18.0})
        self.assertEqual((marks[3]["n"], marks[3]["point"]), (1, {"x": 5.0, "y": 6.0}))
        self.assertEqual(len(marks), 4)

    def test_beats_stop_at_the_cap_and_empty_ones_go(self):
        many = [{"say": f"step {i}", "marks": [{"kind": "ring", "target": "x"}]} for i in range(12)]
        self.assertEqual(len(pointing.clean_beats(many + [{}, "junk"])), pointing.MAX_BEATS)
        self.assertEqual(pointing.clean_beats({"marks": [{"kind": "ring", "target": "x"}], "wait": "click"})[0]["wait"], "click")

    def test_a_label_needs_its_words_and_odd_numbers_are_refused(self):
        self.assertIsNone(pointing.clean_mark({"kind": "label", "target": "Export"}))
        self.assertEqual(pointing.clean_mark({"kind": "label", "target": "Export", "text": "x" * 90})["text"], "x" * 40)
        self.assertIsNone(pointing.clean_mark({"kind": "ring", "box": {"x": "nan", "y": 0, "w": 1, "h": 1}}))
        self.assertIsNone(pointing.clean_mark({"kind": "ring", "box": {"x": 1e9, "y": 0, "w": 1, "h": 1}}))

    def test_only_a_lazy_beat_carries_words_to_the_desktop(self):
        body = {"show": "ab-12", "n": 0, "count": 2, "marks": [{"kind": "ring", "target": {"text": "Save"}}]}
        self.assertIsInstance(pointing.clean_payload(body), str)
        lazy = pointing.clean_payload({**body, "lazy": True})
        self.assertEqual((lazy["show"], lazy["lazy"], lazy["marks"][0]["target"]["text"]), ("ab12", True, "Save"))
        self.assertEqual(pointing.clean_payload({"clear": True}), {"clear": True})
        self.assertIsInstance(pointing.clean_payload({"show": "a", "n": 99}), str)


class TestFinding(unittest.TestCase):
    def test_the_same_words_beat_a_longer_line_and_accessible_beats_ocr(self):
        matches = [
            {"text": "Export settings", "x": 0, "y": 0, "w": 100, "h": 20, "source": "atspi"},
            {"text": "Export", "x": 500, "y": 0, "w": 60, "h": 20, "source": "ocr"},
            {"text": "export", "x": 900, "y": 0, "w": 60, "h": 20, "source": "atspi"},
        ]
        self.assertEqual(pointing.pick(matches, "Export")["x"], 900)

    def test_near_picks_the_one_beside_the_other_words(self):
        matches = [{"text": "Export", "x": 100, "y": 100, "w": 60, "h": 20}, {"text": "Export", "x": 1500, "y": 900, "w": 60, "h": 20}]
        self.assertEqual(pointing.pick(matches, "Export", {"x": 1400, "y": 880, "w": 40, "h": 20})["x"], 1500)
        box, why = pointing.resolve({"text": "Export", "near": "Share"},
                                    finder({"Export": matches, "Share": [{"text": "Share", "x": 1400, "y": 880, "w": 40, "h": 20}]}))
        self.assertEqual((box["x"], why), (1500, None))

    def test_nothing_found_says_why(self):
        self.assertEqual(pointing.resolve({"text": "Nope"}, finder({})), (None, "not on screen"))
        failing = lambda text, window: {"matches": [], "error": "no window named front"}
        self.assertEqual(pointing.resolve({"text": "Nope"}, failing), (None, "no window named front"))

    def test_find_text_answers_are_read_or_turned_into_errors(self):
        good = pointing.parse_found(0, 'noise\n{"matches": [{"text": "A", "x": 1, "y": 2, "w": 3, "h": 4}], "ms": 9}\n')
        self.assertEqual(good["matches"][0]["text"], "A")
        self.assertEqual(pointing.parse_found(1, '{"error": "no such window"}')["error"], "no such window")
        self.assertIn("failed", pointing.parse_found(0, "garbage")["error"])

    def test_the_ssh_runner_reaches_the_desktop_repo_with_quoted_words(self):
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps({"matches": [EXPORT], "ms": 3}), stderr=""))
        with mock.patch("socket.gethostname", return_value="the-pi"):
            find = pointing.ssh_runner("desk-box", "Projects/diablo", run=run)
        found = find("Save as…'x", "front")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:1] + argv[-2:-1], ["ssh", "desk-box"])
        self.assertIn("cd Projects/diablo && .venv-voice/bin/python -m voice.hands find-text", argv[-1])
        self.assertIn("'Save as…'\"'\"'x'", argv[-1])
        self.assertIn("--window front", argv[-1])
        self.assertEqual(found["matches"][0]["text"], "Export")

    def test_an_unreachable_desktop_is_an_error_not_a_crash(self):
        run = mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 12))
        find = pointing.ssh_runner("desk-box", "Projects/diablo", run=run)
        self.assertIn("could not reach desk-box", find("Export", "front")["error"])


class TestPlaying(unittest.TestCase):
    def beats(self, raw):
        return pointing.clean_beats(raw)

    def test_all_lookups_run_at_once(self):
        calls, sent = [], []
        beats = self.beats([{"say": "a", "marks": [{"kind": "ring", "target": t}]} for t in ("A", "B", "C", "D")])
        table = {t: [{**EXPORT, "text": t}] for t in "ABCD"}
        started = time.monotonic()
        pointing.play(beats, finder(table, {t: 0.3 for t in "ABCD"}, calls), lambda body: sent.append(body))
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(len(calls), 4)
        self.assertEqual([b["n"] for b in sent], [0, 1, 2, 3])

    def test_the_first_beat_goes_out_while_later_ones_are_still_being_found(self):
        sent = []
        slow_done = threading.Event()

        def find(text, window):
            if text == "Slow":
                time.sleep(0.4)
                slow_done.set()
            return {"matches": [{**EXPORT, "text": text}]}

        def send(body):
            sent.append((body["n"], slow_done.is_set()))

        beats = self.beats([{"say": "one", "marks": [{"kind": "ring", "target": "Fast"}]},
                            {"say": "two", "marks": [{"kind": "ring", "target": "Slow"}]}])
        pointing.play(beats, find, send)
        self.assertEqual(sent, [(0, False), (1, True)])

    def test_a_target_not_found_drops_its_mark_and_is_reported(self):
        sent = []
        beats = self.beats([{"say": "here", "marks": [{"kind": "ring", "target": "Export"}, {"kind": "arrow", "target": "Gone"},
                                                      {"kind": "underline", "box": {"x": 1, "y": 1, "w": 10, "h": 2}}]}])
        report = pointing.play(beats, finder({"Export": [EXPORT]}), lambda body: sent.append(body))
        kinds = [m["kind"] for m in sent[0]["marks"]]
        self.assertEqual(kinds, ["ring", "underline"])
        self.assertEqual(sent[0]["marks"][0]["box"], {"x": 1200.0, "y": 300.0, "w": 80.0, "h": 30.0})
        self.assertNotIn("target", sent[0]["marks"][0])
        self.assertEqual(report["missing"], [{"beat": 1, "text": "Gone", "why": "not on screen"}])
        text = pointing.summary(report, beats)
        self.assertIn('"Gone" in beat 1', text)
        self.assertIn("Now say", text)

    def test_the_same_words_are_looked_up_once(self):
        calls = []
        beats = self.beats([{"say": "a", "marks": [{"kind": "ring", "target": "Export"}, {"kind": "arrow", "target": "Export"}]},
                            {"say": "b", "marks": [{"kind": "label", "target": "Export", "text": "here"}]}])
        pointing.play(beats, finder({"Export": [EXPORT]}, calls=calls), lambda body: None)
        self.assertEqual(len(calls), 1)

    def test_beats_after_a_wait_go_out_lazy_and_are_not_looked_up_yet(self):
        calls, sent = [], []
        beats = self.beats([{"say": "Open file.", "wait": "click", "marks": [{"kind": "ring", "target": "File"}]},
                            {"say": "Then export.", "wait": "click", "marks": [{"kind": "ring", "target": "Export"}]},
                            {"say": "Done.", "marks": [{"kind": "check", "target": "Saved"}]}])
        report = pointing.play(beats, finder({"File": [FILE]}, calls=calls), lambda body: sent.append(body))
        self.assertEqual([c[0] for c in calls], ["File"])
        self.assertEqual([(b["n"], b["lazy"], b["count"]) for b in sent], [(0, False, 3), (1, True, 3), (2, True, 3)])
        self.assertEqual(sent[1]["marks"][0]["target"]["text"], "Export")
        self.assertEqual(len({b["show"] for b in sent}), 1)
        self.assertIn("last 2 beats are looked up on screen", pointing.summary(report, beats))

    def test_a_failed_send_stops_and_says_so(self):
        beats = self.beats([{"say": "a", "marks": [{"kind": "ring", "box": {"x": 0, "y": 0, "w": 5, "h": 5}}]}] * 2)
        report = pointing.play(beats, finder({}), lambda body: "could not reach the hub: down")
        self.assertEqual(report["sent"], [])
        self.assertEqual(pointing.summary(report, beats), "not shown: could not reach the hub: down")

    def test_every_lazy_beat_resolves_on_the_desktop(self):
        beat = {"n": 1, "say": "Then export.", "marks": [{"kind": "ring", "target": {"text": "Export", "window": "front"}},
                                                        {"kind": "arrow", "target": {"text": "Gone"}}], "lazy": True}
        out = []
        with mock.patch.object(pointing, "local_runner", return_value=finder({"Export": [EXPORT]})), \
                mock.patch("builtins.print", side_effect=out.append):
            pointing.main(["resolve", json.dumps(beat)])
        got = json.loads(out[0])
        self.assertEqual(got["beat"]["lazy"], False)
        self.assertEqual([m["kind"] for m in got["beat"]["marks"]], ["ring"])
        self.assertEqual(got["missing"][0]["text"], "Gone")


class TestTool(unittest.TestCase):
    def test_it_is_listed_and_teaches_targeting_by_words(self):
        tool = next(t for t in mcp_server.EXTRA if t["name"] == "point_on_screen")
        self.assertIn("visible", tool["description"])
        self.assertIn("One call per answer", tool["description"])
        self.assertIn("beats", tool["inputSchema"]["properties"])

    def test_it_is_not_allowed_while_the_screen_is_locked(self):
        self.assertNotIn("point_on_screen", lockguard.SAFE_HOUSE)

    def test_a_call_resolves_and_posts_each_beat(self):
        posted = []
        with mock.patch.object(mcp_server, "screen_post", side_effect=lambda path, body: posted.append((path, body))), \
                mock.patch.object(pointing, "ssh_runner", return_value=finder({"Export": [EXPORT]})) as runner, \
                mock.patch.object(mcp_server.settings, "get", return_value="desk-box"), \
                mock.patch.object(mcp_server.lockguard, "refusal", return_value=None), \
                mock.patch.object(mcp_server.character, "refusal", return_value=None):
            text = mcp_server.call("point_on_screen", {"beats": [{"say": "Top right.", "marks": [{"kind": "ring", "target": {"text": "Export"}}]}]})
        self.assertEqual(runner.call_args.args[0], "desk-box")
        self.assertEqual(posted[0][0], "/v1/screen/marks")
        self.assertEqual(posted[0][1]["marks"][0]["box"]["x"], 1200.0)
        self.assertIn("1 beat on screen", text)

    def test_clear_alone_takes_the_marks_down(self):
        with mock.patch.object(mcp_server, "screen_post", return_value=None) as post:
            self.assertEqual(mcp_server.point_on_screen({"clear": True}), "every mark is down")
        post.assert_called_once_with("/v1/screen/marks", {"clear": True})

    def test_nothing_to_point_at_is_said_plainly(self):
        self.assertIn("nothing to point at", mcp_server.point_on_screen({"beats": [{"marks": [{"kind": "ring"}]}]}))


class TestEndpoint(ApiCase):
    def test_a_beat_goes_out_as_a_screen_event(self):
        body = {"show": "s1", "n": 0, "count": 2, "say": "Here.", "marks": [{"kind": "ring", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}]}
        with mock.patch.object(server, "publish") as published:
            reply = self.client.post("/v1/screen/marks", headers=HEADERS, json=body)
        self.assertEqual(reply.status_code, 200)
        event = published.call_args.args[0]
        self.assertEqual(event["kind"], "screen")
        self.assertEqual((event["marks"]["show"], event["marks"]["n"], event["marks"]["count"]), ("s1", 0, 2))

    def test_clear_and_refusals(self):
        with mock.patch.object(server, "publish") as published:
            self.assertEqual(self.client.post("/v1/screen/marks", headers=HEADERS, json={"clear": True}).status_code, 200)
        self.assertEqual(published.call_args.args[0], {"kind": "screen", "marks": {"clear": True}})
        unresolved = {"show": "s1", "n": 0, "marks": [{"kind": "ring", "target": {"text": "Save"}}]}
        self.assertEqual(self.client.post("/v1/screen/marks", headers=HEADERS, json=unresolved).status_code, 400)
        self.assertEqual(self.client.post("/v1/screen/marks", json={"clear": True}).status_code, 403)


class TestDesktopSide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("STATE", Path(self.tmp.name)), ("CONFIG", Path(self.tmp.name) / "config.toml")):
            patcher = mock.patch.object(busy, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read(self):
        return json.loads((Path(self.tmp.name) / "marks.json").read_text())

    def beat(self, show, n, count=2, **extra):
        return {"kind": "screen", "marks": {"show": show, "n": n, "count": count, "say": f"beat {n}", "marks": [], **extra}}

    def test_beats_of_one_show_gather_in_order(self):
        self.assertEqual(busy.show(self.beat("s1", 1), now=10), "marked")
        busy.show(self.beat("s1", 0), now=11)
        body = self.read()
        self.assertEqual((body["show"], body["count"], body["at"]), ("s1", 2, 10))
        self.assertEqual([b["n"] for b in body["beats"]], [0, 1])
        self.assertEqual(body["beats"][0]["at"], 11)
        self.assertEqual(body["cps"], busy.MARKS_CPS)

    def test_a_new_show_replaces_the_old_one(self):
        busy.show(self.beat("s1", 0), now=10)
        busy.show(self.beat("s2", 0, count=1, wait="click"), now=20)
        body = self.read()
        self.assertEqual((body["show"], len(body["beats"]), body["beats"][0]["wait"]), ("s2", 1, "click"))

    def test_clear_takes_everything_down(self):
        busy.show(self.beat("s1", 0), now=10)
        self.assertEqual(busy.show({"kind": "screen", "marks": {"clear": True}}, now=12), "cleared")
        self.assertEqual((self.read()["beats"], self.read()["cleared"]), ([], True))

    def test_the_speaking_rate_comes_from_config(self):
        (Path(self.tmp.name) / "config.toml").write_text("marks_cps = 20\n")
        busy.show(self.beat("s1", 0), now=10)
        self.assertEqual(self.read()["cps"], 20.0)
        (Path(self.tmp.name) / "config.toml").write_text("marks_cps = 900\n")
        busy.show(self.beat("s1", 1), now=11)
        self.assertEqual(self.read()["cps"], busy.MARKS_CPS)

    def test_a_malformed_beat_writes_nothing(self):
        self.assertIsNone(busy.show({"kind": "screen", "marks": {"n": 0}}, now=1))
        self.assertIsNone(busy.show({"kind": "screen", "marks": "junk"}, now=1))
        self.assertFalse((Path(self.tmp.name) / "marks.json").exists())


if __name__ == "__main__":
    unittest.main()
