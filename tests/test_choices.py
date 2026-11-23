import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import choices, pointing

SCREEN = {"x": 0, "y": 0, "w": 1920, "h": 1080}
QUESTION = {
    "id": "q1", "conv": "main", "state": "pending", "header": "Venue", "question": "Which room for the demo?",
    "options": [{"label": "Hall A", "description": "Seats forty"}, {"label": "Hall B", "description": "Has a projector"},
                {"label": "Online", "description": ""}],
    "recommended": 0,
}


def cards(beats):
    return [m for b in beats for m in b["marks"] if m["kind"] == "choice"]


class TestPlan(unittest.TestCase):
    def test_a_title_then_one_card_per_option_with_the_pick_marked(self):
        beats = choices.plan(QUESTION, SCREEN, show="s1")
        self.assertEqual([b["n"] for b in beats], [0, 1, 2, 3])
        self.assertEqual(beats[0]["marks"][0], {"kind": "title", "text": "Which room for the demo?", "box": beats[0]["marks"][0]["box"],
                                                "size": beats[0]["marks"][0]["size"]})
        self.assertEqual([(c["num"], c["text"], c["pick"]) for c in cards(beats)], [(1, "Hall A", True), (2, "Hall B", False), (3, "Online", False)])
        self.assertEqual([b["say"] for b in beats[1:]], ["One, Hall A.", "Two, Hall B.", "Three, Online."])
        self.assertTrue(all(b["keep"] and b["count"] == 4 and b["show"] == "s1" for b in beats))

    def test_the_hub_keeps_every_piece(self):
        for beat in choices.plan(QUESTION, SCREEN):
            cleaned = pointing.clean_payload(beat)
            self.assertIsInstance(cleaned, dict)
            self.assertEqual(cleaned["marks"], beat["marks"])

    def test_no_pick_marks_none_and_too_few_options_draw_nothing(self):
        beats = choices.plan(dict(QUESTION, recommended=None), SCREEN)
        self.assertFalse(any(c["pick"] for c in cards(beats)))
        self.assertEqual(choices.plan(dict(QUESTION, options=QUESTION["options"][:1]), SCREEN), [])

    def test_cards_sit_in_one_row_or_two_columns_inside_the_monitor(self):
        four = dict(QUESTION, options=QUESTION["options"] + [{"label": "Later", "description": "Next week"}])
        wide = cards(choices.plan(four, SCREEN))
        self.assertEqual(len({c["box"]["y"] for c in wide}), 1)
        narrow_screen = {"x": 100, "y": 0, "w": 1280, "h": 800}
        narrow = cards(choices.plan(four, narrow_screen))
        self.assertEqual(len({c["box"]["y"] for c in narrow}), 2)
        for card in wide + narrow:
            box = card["box"]
            self.assertGreaterEqual(box["w"], choices.MIN_W - 1)
        for card in narrow:
            box = card["box"]
            self.assertGreaterEqual(box["x"], narrow_screen["x"])
            self.assertLessEqual(box["x"] + box["w"], narrow_screen["x"] + narrow_screen["w"])

    def test_a_longer_description_makes_taller_cards(self):
        short = cards(choices.plan(QUESTION, SCREEN))[0]["box"]["h"]
        long_text = "A description long enough to need three or four lines inside one card on the desk"
        tall = cards(choices.plan(dict(QUESTION, options=[{"label": "A", "description": long_text}, {"label": "B"}]), SCREEN))[0]["box"]["h"]
        self.assertGreater(tall, short)

    def test_a_long_question_titles_with_its_header(self):
        long_q = dict(QUESTION, question="Which of the rooms on the second floor should the whole team use for the demo on Friday?")
        self.assertEqual(choices.plan(long_q, SCREEN)[0]["marks"][0]["text"], "Venue")

    def test_choice_pieces_need_keep(self):
        beat = choices.plan(QUESTION, SCREEN)[1]
        self.assertEqual(pointing.clean_payload(dict(beat, keep=False))["marks"], [])


class TestListener(unittest.TestCase):
    def setUp(self):
        choices._current.update(show="", question="")
        self.sent = []
        screen = mock.patch("hub.mcp_server.desktop_screen", return_value=SCREEN)
        screen.start()
        self.addCleanup(screen.stop)

    def ask(self, question, desk=True):
        with mock.patch.object(choices, "from_desk", return_value=desk):
            choices.on_question(question, start=lambda job: job(), publish=self.sent.append)

    def test_a_question_from_the_desk_is_drawn_once(self):
        self.ask(QUESTION)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual([e["marks"]["n"] for e in self.sent], [0, 1, 2, 3])
        self.ask(QUESTION)
        self.assertEqual(len(self.sent), 4)

    def test_elsewhere_or_an_action_draws_nothing(self):
        self.ask(QUESTION, desk=False)
        self.ask(dict(QUESTION, id="q2", action={"kind": "mail"}))
        self.assertEqual(self.sent, [])

    def test_an_answer_takes_its_cards_down(self):
        self.ask(QUESTION)
        self.ask(dict(QUESTION, state="answered"))
        self.assertEqual(self.sent[-1], {"kind": "screen", "marks": {"clear": True}})
        self.ask(dict(QUESTION, state="answered"))
        self.assertEqual(len(self.sent), 5)

    def test_cards_another_show_replaced_are_left_alone(self):
        self.ask(QUESTION)
        choices.replaced("dg123")
        self.ask(dict(QUESTION, state="expired"))
        self.assertEqual(len(self.sent), 4)

    def test_its_own_beats_do_not_count_as_replacing_it(self):
        self.ask(QUESTION)
        choices.replaced(self.sent[0]["marks"]["show"])
        self.ask(dict(QUESTION, state="cancelled"))
        self.assertEqual(self.sent[-1]["marks"], {"clear": True})


if __name__ == "__main__":
    unittest.main()
