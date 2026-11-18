import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import hands

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def row(level, left, top, width, height, conf, text, block=1, line=1, word=1):
    return "\t".join(str(v) for v in (level, 1, block, 1, line, word, left, top, width, height, conf, text))


TSV = "\n".join([
    HEADER,
    row(1, 0, 0, 800, 600, -1, ""),
    row(2, 10, 10, 400, 40, -1, ""),
    row(5, 20, 100, 40, 14, 95.1, "Sign"),
    row(5, 66, 100, 22, 14, 93.0, "in"),
    row(5, 400, 100, 70, 14, 91.0, "Settinqs", word=3),
    row(5, 20, 200, 60, 14, 90.0, "Delete", line=2),
    row(5, 86, 200, 70, 14, 88.0, "account", line=2, word=2),
    row(5, 20, 300, 20, 14, 70.0, "On", line=3),
    row(5, 600, 300, 20, 14, 70.0, "OK", line=3, word=2),
    row(5, 20, 400, 5, 5, -1, "   "),
    row(4, 20, 400, 300, 20, -1, ""),
])

CLIENTS = [
    {"address": "0xb", "mapped": True, "class": "editor", "title": "Notes", "pid": 22, "at": [100, 50], "size": [800, 600],
     "workspace": {"id": 2, "name": "2"}, "monitor": 0, "focusHistoryID": 1},
    {"address": "0xa", "mapped": True, "class": "browser", "title": "Example Domain", "pid": 11, "at": [1000, 40],
     "size": [1200, 900], "workspace": {"id": 1, "name": "1"}, "monitor": 0, "focusHistoryID": 0},
    {"address": "0xc", "mapped": False, "class": "ghost", "title": "", "pid": 33, "at": [0, 0], "size": [1, 1],
     "workspace": {"id": 1}, "monitor": 0, "focusHistoryID": 2},
]
MONITORS = [{"id": 0, "name": "DP-1", "x": 0, "y": 0, "width": 2560, "height": 1440, "scale": 1.0, "transform": 0,
             "focused": True, "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]


def fake_hypr(*args):
    if args == ("activewindow",):
        return CLIENTS[1]
    if args == ("monitors",):
        return MONITORS
    if args == ("clients",):
        return CLIENTS
    if args == ("locked",):
        return {"locked": False}
    if args == ("cursorpos",):
        return {"x": 5, "y": 6}
    raise AssertionError(args)


class Ocr(unittest.TestCase):
    def test_parse_keeps_words_only(self):
        words = hands.parse_tsv(TSV)
        self.assertEqual([w["text"] for w in words], ["Sign", "in", "Settinqs", "Delete", "account", "On", "OK"])
        self.assertEqual((words[0]["left"], words[0]["top"], words[0]["width"], words[0]["height"]), (20, 100, 40, 14))

    def test_phrase_across_words(self):
        found = hands.phrase_matches(hands.parse_tsv(TSV), "sign in")
        self.assertEqual(found[0][0], 1.0)
        self.assertEqual([w["text"] for w in found[0][1]], ["Sign", "in"])
        self.assertEqual(hands.union(found[0][1]), (20, 100, 68, 14))

    def test_light_fuzz_and_gap(self):
        found = hands.phrase_matches(hands.parse_tsv(TSV), "Settings")
        self.assertEqual([w["text"] for w in found[0][1]], ["Settinqs"])
        self.assertGreaterEqual(found[0][0], hands.OCR_MATCH)
        self.assertEqual(hands.phrase_matches(hands.parse_tsv(TSV), "in Settings"), [])

    def test_short_words_match_exactly(self):
        found = hands.phrase_matches(hands.parse_tsv(TSV), "ok")
        self.assertEqual([[w["text"] for w in f[1]] for f in found], [["OK"]])

    def test_matches_never_share_words(self):
        words = hands.parse_tsv(TSV)
        found = hands.phrase_matches(words, "delete account")
        self.assertEqual(len(found), 1)
        self.assertEqual(hands.phrase_matches(words, "nothing like it"), [])

    def test_image_pixels_to_global_logical(self):
        self.assertEqual(hands.to_global((40, 20, 100, 30), (1000, 500, 800, 600), (1600, 1200)), [1020, 510, 50, 15])
        self.assertEqual(hands.to_global((40, 20, 100, 30), (1000, 500, 800, 600), (800, 600)), [1040, 520, 100, 30])

    def test_ppm_header(self):
        self.assertEqual(hands.ppm_size(b"P6\n# made by a test\n1600 1200\n255\n\x00\x00"), (1600, 1200))
        self.assertEqual(hands.ppm_size(b"P6 64 32 255\n"), (64, 32))

    def test_ocr_matches_maps_into_the_window(self):
        client = dict(CLIENTS[1])
        with mock.patch.object(hands, "ocr", return_value=(hands.parse_tsv(TSV), (2400, 1800))) as read:
            found = hands.ocr_matches("sign in", client, MONITORS)
        self.assertEqual(read.call_args[0][0], (1000, 40, 1200, 900))
        self.assertEqual(found[0], {"text": "Sign in", "x": 1010, "y": 90, "w": 34, "h": 7, "source": "ocr", "score": 1.0})

    def test_region_is_clipped_to_the_monitor(self):
        client = dict(CLIENTS[1], at=[2000, 1000], size=[1200, 900])
        self.assertEqual(hands.ocr_region(client, MONITORS), (2000, 1000, 560, 440))
        self.assertEqual(hands.ocr_region(None, MONITORS), (0, 0, 2560, 1440))
        scaled = [dict(MONITORS[0], scale=2.0, transform=1)]
        self.assertEqual(hands.ocr_region(None, scaled), (0, 0, 720, 1280))


class Coordinates(unittest.TestCase):
    def test_window_relative(self):
        mapped, mode = hands.frame_mapper((0, 0, 800, 600), (100, 50, 800, 600))
        self.assertEqual(mode, "window")
        self.assertEqual(mapped((10, 20, 30, 40)), [110, 70, 30, 40])

    def test_global(self):
        mapped, mode = hands.frame_mapper((100, 50, 800, 600), (100, 50, 800, 600))
        self.assertEqual(mode, "global")
        self.assertEqual(mapped((110, 70, 30, 40)), [110, 70, 30, 40])

    def test_physical_pixels_at_scale(self):
        mapped, mode = hands.frame_mapper((0, 0, 1600, 1200), (100, 50, 800, 600), scale=2.0)
        self.assertEqual(mode, "physical")
        self.assertEqual(mapped((200, 100, 60, 40)), [200, 100, 30, 20])

    def test_logical_at_scale_stays_logical(self):
        mapped, mode = hands.frame_mapper((0, 0, 800, 600), (100, 50, 800, 600), scale=2.0)
        self.assertEqual(mode, "window")
        self.assertEqual(mapped((10, 10, 10, 10)), [110, 60, 10, 10])

    def test_zero_frame_means_no_boxes(self):
        mapped, mode = hands.frame_mapper((0, 0, 0, 0), (100, 50, 800, 600))
        self.assertEqual(mode, "none")
        self.assertIsNone(mapped((10, 10, 10, 10)))

    def test_zero_and_outside_boxes(self):
        mapped, _ = hands.frame_mapper((0, 0, 800, 600), (100, 50, 800, 600))
        self.assertIsNone(mapped((0, 0, 0, 0)))
        self.assertIsNone(mapped((-1, -1, -1, -1)))
        self.assertIsNone(mapped((900, 10, 50, 50)))
        self.assertEqual(mapped((780, 10, 50, 50)), [880, 60, 20, 50])

    def test_shadow_margin_is_centred(self):
        mapped, _ = hands.frame_mapper((0, 0, 840, 640), (100, 50, 800, 600))
        self.assertEqual(mapped((20, 20, 10, 10)), [100, 50, 10, 10])


class Steps(unittest.TestCase):
    def test_a_good_plan_is_normalised(self):
        plan = hands.validate(json.dumps([
            {"do": "focus"}, {"do": "click_text", "text": " Save "}, {"do": "click", "id": "3"},
            {"do": "click_at", "x": 10.4, "y": 20}, {"do": "scroll", "direction": "UP", "amount": 99},
            {"do": "fill", "field": "Name", "text": ""}, {"do": "wait", "seconds": 5}, {"do": "key", "key": "ctrl+s"},
        ]))
        self.assertEqual(plan[1], {"do": "click_text", "text": "Save", "button": "left", "double": False})
        self.assertEqual(plan[2]["id"], 3)
        self.assertEqual((plan[3]["x"], plan[3]["y"]), (10, 20))
        self.assertEqual((plan[4]["direction"], plan[4]["amount"]), ("up", 15))
        self.assertEqual(plan[5], {"do": "fill", "text": "", "field": "Name"})
        self.assertEqual(hands.validate({"do": "focus"}), [{"do": "focus"}])

    def test_bad_plans_name_the_step(self):
        cases = [
            ("[]", "non-empty"),
            ("{not json", "not json"),
            ([{"do": "dance"}], "step 1: do must be one of"),
            ([{"do": "focus"}, {"do": "click_text"}], "step 2 (click_text): text is needed"),
            ([{"do": "wait", "seconds": 6}], "seconds is 0 to 5"),
            ([{"do": "click", "id": 0}], "id starts at 1"),
            ([{"do": "click_at", "x": 1}], "y is needed"),
            ([{"do": "click_text", "text": "a", "button": "side"}], "button is one of"),
            ([{"do": "scroll", "direction": "sideways"}], "direction is one of"),
            ([{"do": "type", "text": ""}], "text is needed"),
            ([{"do": "focus"}] * (hands.MAX_STEPS + 1), "at most"),
            (["focus"], "each step is an object"),
        ]
        for steps, said in cases:
            with self.subTest(steps=steps), self.assertRaises(hands.HandsError) as caught:
                hands.validate(steps)
            self.assertIn(said, str(caught.exception))

    def test_typing_a_url_becomes_an_open(self):
        for plan in (
            [{"do": "focus"}, {"do": "key", "key": "ctrl+l"}, {"do": "type", "text": "example.com/docs"}, {"do": "key", "key": "Return"}],
            [{"do": "key", "key": "Ctrl + L"}, {"do": "fill", "text": "https://example.com"}, {"do": "key", "key": "enter"}],
            [{"do": "click_text", "text": "Address bar"}, {"do": "type", "text": "example.org"}, {"do": "key", "key": "Return"}],
            [{"do": "type", "text": "www.example.com\n"}],
        ):
            with self.subTest(plan=plan):
                steps, note = hands.rewrite(hands.validate(plan))
                self.assertEqual(steps[0]["do"], "open")
                self.assertEqual(len(steps), 1)
                self.assertIn("became an open", note)

    def test_other_plans_stay(self):
        for plan in (
            [{"do": "key", "key": "ctrl+l"}, {"do": "type", "text": "best pizza near me"}, {"do": "key", "key": "Return"}],
            [{"do": "click_text", "text": "Search"}, {"do": "type", "text": "example.com"}, {"do": "key", "key": "Return"}],
            [{"do": "type", "text": "example.com"}],
        ):
            with self.subTest(plan=plan):
                steps, note = hands.rewrite(hands.validate(plan))
                self.assertIsNone(note)
                self.assertEqual(steps, hands.validate(plan))

    def test_risky_words(self):
        for text in ("Delete", "Remove item", "Pay now", "Buy", "Send", "Submit order", "Sign out", "Log out", "Uninstall",
                     "Format disk", "Transfer", "Slet", "Fjern", "Betal", "Køb nu", "Log ud", "Afinstaller", "Overfør"):
            self.assertTrue(hands.risky(text), text)
        for text in ("Save", "Sender", "Information", "PayPal account", "Open", "Settings", "Deleted items"):
            self.assertFalse(hands.risky(text), text)


REAL_TREE = hands.Tree


class FakeTree:
    def __init__(self, controls=(), items=()):
        self.controls, self.items = list(controls), list(items)

    def control(self, control_id, name=None):
        return REAL_TREE.control(self, control_id, name)

    def at(self, x, y):
        return REAL_TREE.at(self, x, y)

    def range_box(self, node, start, end):
        return None

    def listing(self, max_controls, text_limit=0):
        return {"controls": [n.control() for n in self.controls[:max_controls]], "text": "after"}


def node(control_id, kind, name, box):
    made = hands.Node(None, None, kind, name, box, [])
    made.id = control_id
    return made


class Acting(unittest.TestCase):
    def setUp(self):
        for name, value in (("hypr", fake_hypr), ("SETTLE", {})):
            patch = mock.patch.object(hands, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.clicks = []
        patch = mock.patch.object(hands, "click_point", side_effect=lambda *a: self.clicks.append(a))
        patch.start()
        self.addCleanup(patch.stop)
        self.moves = []
        patch = mock.patch.object(hands, "move", side_effect=lambda x, y: self.moves.append((x, y)))
        patch.start()
        self.addCleanup(patch.stop)
        delete = node(1, "button", "Delete account", [1100, 100, 80, 20])
        keep = node(2, "button", "Keep", [1200, 100, 80, 20])
        self.tree = FakeTree([delete, keep], [delete, keep])
        patch = mock.patch.object(hands, "Tree", side_effect=lambda *a, **k: self.tree)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_risky_click_is_refused_before_anything_runs(self):
        result, code = hands.act("front", [{"do": "click_text", "text": "Keep"}, {"do": "click_text", "text": "Delete account"}])
        self.assertEqual(code, hands.EXIT_CONFIRM)
        self.assertIn("Delete account", result["needs_confirm"])
        self.assertEqual(self.clicks, [])
        self.assertFalse(result["ok"])

    def test_a_risky_control_id_is_refused(self):
        result, code = hands.act("front", [{"do": "click", "id": 1}])
        self.assertEqual(code, hands.EXIT_CONFIRM)
        self.assertIn("Delete account", result["needs_confirm"])

    def test_a_risky_point_is_refused(self):
        result, code = hands.act("front", [{"do": "click_at", "x": 1110, "y": 110}])
        self.assertEqual(code, hands.EXIT_CONFIRM)
        result, code = hands.act("front", [{"do": "click_at", "x": 1210, "y": 110}])
        self.assertEqual(code, 0)

    def test_a_risky_match_found_while_acting_is_refused(self):
        matches = {"matches": [{"text": "Delete everything", "x": 1100, "y": 300, "w": 100, "h": 20, "source": "ocr"}]}
        with mock.patch.object(hands, "find_text", return_value=matches):
            result, code = hands.act("front", [{"do": "click_text", "text": "everything"}])
        self.assertEqual(code, hands.EXIT_CONFIRM)
        self.assertEqual(result["failed_step"], 1)
        self.assertEqual(self.clicks, [])

    def test_confirmed_clicks_run_and_the_pointer_comes_back(self):
        result, code = hands.act("front", [{"do": "click_text", "text": "Delete account", "confirmed": True}])
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(self.clicks, [(1140, 110, "left", False)])
        self.assertEqual(self.moves[-1], (5, 6))
        self.assertEqual(result["front"]["address"], "0xa")
        self.assertEqual([c["name"] for c in result["controls"]], ["Delete account", "Keep"])
        self.assertIn("ms", result)

    def test_near_picks_the_closest(self):
        found = [{"text": "Edit", "x": 0, "y": 0, "w": 10, "h": 10}, {"text": "Edit", "x": 500, "y": 500, "w": 10, "h": 10}]
        anchor = [{"text": "Photo", "x": 480, "y": 520, "w": 10, "h": 10}]
        self.assertEqual(hands.nearest(found, anchor), found[1])
        self.assertEqual(hands.nearest(found, []), found[0])

    def test_the_first_failure_stops_the_plan(self):
        with mock.patch.object(hands, "find_text", return_value={"matches": []}), mock.patch.object(hands.time, "sleep"):
            result, code = hands.act("front", [{"do": "click_text", "text": "Missing"}, {"do": "key", "key": "Return"}])
        self.assertEqual(code, 1)
        self.assertEqual(result["failed_step"], 1)
        self.assertEqual(len(result["steps"]), 1)
        self.assertIn("Missing", result["error"])

    def test_typing_into_another_window_focuses_it_first(self):
        with mock.patch.object(hands.window, "bring", return_value=(True, "")) as bring, \
                mock.patch.object(hands, "type_text", return_value="typed"):
            result, code = hands.act("editor", [{"do": "type", "text": "hello"}])
        self.assertEqual(code, 0)
        bring.assert_called_once_with("0xb")
        self.assertEqual([s["do"] for s in result["steps"]], ["focus", "type"])

    def test_the_url_plan_opens_instead(self):
        with mock.patch.object(hands, "open_target", return_value=(True, "", "opened https://example.com")) as opened:
            result, code = hands.act("front", [{"do": "key", "key": "ctrl+l"}, {"do": "type", "text": "example.com"},
                                               {"do": "key", "key": "Return"}])
        self.assertEqual(code, 0)
        opened.assert_called_once_with("example.com")
        self.assertIn("rewritten", result)

    def test_nothing_runs_while_locked(self):
        def hypr_locked(*args):
            return {"locked": True} if args == ("locked",) else fake_hypr(*args)

        with mock.patch.object(hands, "hypr", hypr_locked), self.assertRaises(hands.HandsError) as caught:
            hands.act("front", [{"do": "click_at", "x": 1, "y": 1}])
        self.assertIn("locked", str(caught.exception))
        self.assertEqual(self.clicks, [])


class Matching(unittest.TestCase):
    def test_exact_names_first_and_controls_carry_ids(self):
        save = node(4, "button", "Save", [10, 10, 40, 20])
        label = node(None, "text", "Save your work before you leave", [10, 60, 300, 20])
        hidden = node(5, "button", "Save", None)
        found = hands.atspi_matches(FakeTree([save, hidden], [label, save, hidden]), "save")
        self.assertEqual(found[0], {"text": "Save", "x": 10, "y": 10, "w": 40, "h": 20, "source": "atspi", "control_id": 4})
        self.assertEqual(found[1]["source"], "atspi")
        self.assertNotIn("control_id", found[1])
        self.assertEqual(len(found), 2)

    def test_control_by_id_checks_the_name(self):
        tree = FakeTree([node(1, "button", "Open", [0, 0, 1, 1]), node(2, "button", "Close", [0, 0, 1, 1])])
        self.assertEqual(tree.control(2).name, "Close")
        self.assertEqual(tree.control(1, "Close").name, "Close")
        self.assertIsNone(tree.control(1, "Nothing"))
        self.assertIsNone(tree.control(9))


class Contract(unittest.TestCase):
    def run_main(self, argv, **patches):
        out = io.StringIO()
        with mock.patch.object(hands, "hypr", patches.get("hypr", fake_hypr)), mock.patch.object(hands, "prepare_env"), \
                mock.patch.object(hands.sys, "stdout", out):
            code = hands.main(argv)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0]), code

    def test_front(self):
        out, code = self.run_main(["front"])
        self.assertEqual(code, 0)
        for key in ("address", "class", "title", "pid", "x", "y", "w", "h", "monitor", "scale", "locked", "ms"):
            self.assertIn(key, out)
        self.assertEqual((out["class"], out["x"], out["monitor"], out["scale"]), ("browser", 1000, "DP-1", 1.0))

    def test_windows_front_to_back(self):
        out, code = self.run_main(["windows"])
        self.assertEqual([w["address"] for w in out["windows"]], ["0xa", "0xb"])
        self.assertEqual([w["visible"] for w in out["windows"]], [True, False])

    def test_errors_are_json_and_nonzero(self):
        for argv, said in ((["nope"], "invalid choice"), ([], "say a command"), (["act"], "--steps"),
                           (["find-text", "x", "--window", "0xdead"], "no window at 0xdead")):
            with self.subTest(argv=argv):
                out, code = self.run_main(argv)
                self.assertEqual(code, 1)
                self.assertEqual(list(out), ["error"])
                self.assertIn(said, out["error"])

    def test_needs_confirm_exits_three(self):
        tree = FakeTree([node(1, "button", "Send", [1100, 100, 40, 20])])
        with mock.patch.object(hands, "Tree", return_value=tree):
            out, code = self.run_main(["act", "--steps", '[{"do":"click","id":1}]'])
        self.assertEqual(code, hands.EXIT_CONFIRM)
        self.assertIn("needs_confirm", out)

    def test_find_text_stops_at_atspi_and_falls_back_to_ocr(self):
        tree = FakeTree([node(1, "button", "Save", [1100, 100, 40, 20])], [node(1, "button", "Save", [1100, 100, 40, 20])])
        with mock.patch.object(hands, "Tree", return_value=tree), mock.patch.object(hands, "ocr_matches") as ocr:
            out, code = self.run_main(["find-text", "Save"])
        ocr.assert_not_called()
        self.assertEqual(out["matches"][0]["source"], "atspi")
        self.assertIn("ms", out)
        ocr_hit = [{"text": "Later", "x": 1, "y": 2, "w": 3, "h": 4, "source": "ocr", "score": 1.0}]
        with mock.patch.object(hands, "Tree", return_value=tree), mock.patch.object(hands, "ocr_matches", return_value=ocr_hit):
            out, code = self.run_main(["find-text", "Later"])
        self.assertEqual(out["matches"], ocr_hit)

    def test_find_text_skips_ocr_on_a_locked_screen(self):
        def hypr_locked(*args):
            return {"locked": True} if args == ("locked",) else fake_hypr(*args)

        with mock.patch.object(hands, "Tree", return_value=FakeTree()), mock.patch.object(hands, "ocr_matches") as ocr:
            out, code = self.run_main(["find-text", "Anything"], hypr=hypr_locked)
        ocr.assert_not_called()
        self.assertTrue(out["locked"])
        self.assertEqual(out["matches"], [])

    def test_the_module_imports_without_gobject(self):
        done = subprocess.run([sys.executable, "-c", "import voice.hands"], cwd=Path(__file__).resolve().parent.parent,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
