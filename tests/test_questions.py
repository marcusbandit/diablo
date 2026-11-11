import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import agent, bot, brains, db, devices, events, lockguard, mcp_server, questions, server, threads

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
SHIP = {
    "question": "Ship the hub tonight or wait for the morning?",
    "header": "Ship it",
    "options": [
        {"label": "Ship tonight", "description": "Restarts the hub now"},
        {"label": "Wait for morning", "description": "Ships at 8"},
        {"label": "Skip it", "description": "Leave it unshipped"},
    ],
    "recommended": 2,
}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        self.events = []
        publish = mock.patch.object(events, "publish", side_effect=self.events.append)
        publish.start()
        self.addCleanup(publish.stop)

    def kinds(self):
        return [(e["question"]["id"], e["question"]["state"]) for e in self.events if e.get("kind") == "question"]


class TestClean(StoreCase):
    def test_a_good_question_is_kept_with_a_zero_based_recommendation(self):
        q = questions.clean(SHIP)
        self.assertEqual(q["header"], "Ship it")
        self.assertEqual([o["label"] for o in q["options"]], ["Ship tonight", "Wait for morning", "Skip it"])
        self.assertEqual(q["recommended"], 1)
        self.assertFalse(q["multi"])
        self.assertTrue(q["allow_text"])
        self.assertFalse(q["harmless"])

    def test_the_recommendation_may_name_a_label(self):
        self.assertEqual(questions.clean(dict(SHIP, recommended="skip it"))["recommended"], 2)
        self.assertIsNone(questions.clean(dict(SHIP, recommended=9))["recommended"])

    def test_bad_questions_are_refused_with_a_reason(self):
        with self.assertRaisesRegex(ValueError, "question is missing"):
            questions.clean(dict(SHIP, question=" "))
        with self.assertRaisesRegex(ValueError, "two to four"):
            questions.clean(dict(SHIP, options=[{"label": "Only"}]))
        with self.assertRaisesRegex(ValueError, "two to four"):
            questions.clean(dict(SHIP, options=[{"label": str(i)} for i in range(5)]))
        with self.assertRaisesRegex(ValueError, "two to four"):
            questions.clean(dict(SHIP, options=[{"label": "Same"}, {"label": "same"}]))

    def test_options_may_arrive_as_a_json_string_or_plain_labels(self):
        q = questions.clean(dict(SHIP, options=json.dumps(["Red", "Blue"])))
        self.assertEqual([o["label"] for o in q["options"]], ["Red", "Blue"])


class TestStore(StoreCase):
    def test_a_new_question_supersedes_the_open_one_in_its_conversation(self):
        first = questions.ask("main", SHIP)
        other = questions.ask("web-x", SHIP)
        second = questions.ask("main", dict(SHIP, question="Which model?"))
        self.assertEqual(questions.get(first["id"])["state"], "superseded")
        self.assertEqual(questions.get(other["id"])["state"], "pending")
        self.assertEqual([q["id"] for q in questions.pending("main")], [second["id"]])
        self.assertIn((first["id"], "superseded"), self.kinds())

    def test_questions_expire_on_read_and_in_the_sweep(self):
        q = questions.ask("main", SHIP, now=1000)
        self.assertEqual(questions.get(q["id"], now=1000 + questions.TTL_S - 1)["state"], "pending")
        self.assertEqual(questions.get(q["id"], now=1000 + questions.TTL_S)["state"], "expired")
        r = questions.ask("main", SHIP, now=5000)
        gone = questions.sweep(now=5000 + questions.TTL_S + 1)
        self.assertEqual([g["id"] for g in gone], [r["id"]])
        self.assertEqual(questions.pending("main"), [])

    def test_the_ttl_comes_from_settings(self):
        with mock.patch.object(questions.settings, "get", return_value="120"):
            q = questions.ask("main", SHIP, now=0)
        self.assertEqual(q["expires"], 120)

    def test_a_turn_claims_its_question_once(self):
        q = questions.ask("main", SHIP, now=100)
        self.assertEqual(questions.claim("main", 200), [])
        blocks = questions.claim("main", 99)
        self.assertEqual([b["id"] for b in blocks], [q["id"]])
        self.assertEqual(blocks[0]["kind"], "question")
        self.assertEqual(questions.claim("main", 99), [])

    def test_the_reply_that_carries_it_is_linked_and_patched_on_change(self):
        q = questions.ask("main", SHIP)
        block = questions.claim("main", 0)[0]
        message_id = db.add_message("desktop", "assistant", "One question, sir.",
                                    {"section": "diablo", "objects": [block], "threads": ["t1"]})
        questions.link(message_id, "desktop", db.get_message(message_id)["meta"])
        stored = questions.get(q["id"])
        self.assertEqual((stored["message"], stored["section"], stored["threads"]), (message_id, "diablo", ["t1"]))
        questions.choose(stored, [1], source="desktop")
        shown = db.get_message(message_id)["meta"]["objects"][0]
        self.assertEqual(shown["state"], "answered")
        self.assertEqual(shown["answer"]["labels"], ["Wait for morning"])
        event = [e for e in self.events if e.get("kind") == "question"][-1]
        self.assertEqual((event["message"], event["section"]), (message_id, "diablo"))

    def test_a_turn_claims_only_the_question_it_asked(self):
        mine = questions.ask("main", SHIP, now=100, turn="t-a")
        other = questions.ask("web-x", SHIP, now=100, turn="t-b")
        self.assertEqual(questions.claim("main", 0, "t-b"), [])
        self.assertEqual([b["id"] for b in questions.claim("main", 0, "t-a")], [mine["id"]])
        self.assertEqual([b["id"] for b in questions.claim("web-x", 0, "t-b")], [other["id"]])

    def test_the_brain_turn_tags_what_it_asks(self):
        questions.turns["main"] = "t-live"
        self.addCleanup(questions.turns.clear)
        self.assertEqual(questions.ask("main", SHIP)["turn"], "t-live")

    def test_cancel_withdraws_the_newest_or_a_named_one(self):
        q = questions.ask("main", SHIP)
        self.assertIsNone(questions.cancel("web-x"))
        self.assertEqual(questions.cancel("main")["id"], q["id"])
        self.assertIsNone(questions.cancel("main", q["id"]))
        self.assertEqual(questions.get(q["id"])["state"], "cancelled")

    def test_reopen_puts_a_failed_answer_back(self):
        q = questions.ask("main", SHIP)
        answered = questions.choose(q, [0])
        self.assertEqual(questions.reopen(answered)["state"], "pending")
        self.assertNotIn("answer", questions.get(q["id"]))


class TestChoose(StoreCase):
    def test_answers_are_checked_against_the_question(self):
        q = questions.ask("main", SHIP)
        for choices, text, status in (([7], "", 400), ([0, 1], "", 400), ([], "", 400), (["x"], "", 400)):
            with self.assertRaises(questions.Problem) as caught:
                questions.choose(questions.get(q["id"]), choices, text)
            self.assertEqual(caught.exception.status, status)
        closed = questions.ask("main", dict(SHIP, allow_text=False))
        with self.assertRaises(questions.Problem):
            questions.choose(closed, [], "my own idea")

    def test_a_closed_question_cannot_be_answered_twice(self):
        q = questions.ask("main", SHIP)
        questions.choose(q, [0])
        with self.assertRaises(questions.Problem) as caught:
            questions.choose(questions.get(q["id"]), [1])
        self.assertEqual(caught.exception.status, 409)
        late = questions.ask("main", SHIP, now=0)
        with self.assertRaises(questions.Problem) as caught:
            questions.choose(questions.get(late["id"], now=0), [1], now=questions.TTL_S + 1)
        self.assertEqual(caught.exception.status, 410)

    def test_two_answers_at_once_count_once(self):
        q = questions.ask("main", SHIP)
        stale = questions.get(q["id"])
        questions.choose(stale, [0], source="telegram")
        with self.assertRaises(questions.Problem) as caught:
            questions.choose(stale, [1], source="desktop")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(questions.get(q["id"])["answer"]["labels"], ["Ship tonight"])

    def test_the_answer_line_names_the_labels_and_his_words(self):
        q = questions.ask("main", dict(SHIP, multi_select=True))
        one = questions.choose(q, [0])
        self.assertEqual(questions.answer_line(one), f"[answer to question {q['id']}: Ship tonight]")
        q = questions.ask("main", dict(SHIP, multi_select=True))
        both = questions.choose(q, [0, 2], "but tell Sam")
        self.assertEqual(questions.answer_line(both), f"[answer to question {q['id']}: Ship tonight; Skip it] He added: but tell Sam")
        self.assertEqual(questions.shown_text(both), "Ship tonight, Skip it. but tell Sam")
        q = questions.ask("main", SHIP)
        own = questions.choose(q, [], "Ship on Friday")
        self.assertEqual(questions.answer_line(own), f"[answer to question {q['id']}, in his own words: Ship on Friday]")


class TestReplies(StoreCase):
    def question(self, **extra):
        return questions.clean(dict(SHIP, **extra))

    def test_numbers_in_many_forms_pick_an_option(self):
        q = self.question()
        for text, want in (("2", [1]), ("#2", [1]), ("option 2", [1]), ("number two", [1]), ("the second one", [1]),
                           ("two", [1]), ("Let's go with 3", [2]), ("1.", [0]), ("first", [0]), ("one", [0])):
            self.assertEqual(questions.parse_reply(q, text), want, text)

    def test_labels_pick_an_option(self):
        q = self.question()
        for text, want in (("Ship tonight", [0]), ("wait for morning please", [1]), ("skip it", [2]),
                           ("ok, ship tonight", [0])):
            self.assertEqual(questions.parse_reply(q, text), want, text)

    def test_ordinary_messages_are_not_answers(self):
        q = self.question()
        for text in ("what time is it", "5", "1 and 2", "turn the lights on in the kitchen please now",
                     "how long would the restart take if I wait", "", "tonight"):
            self.assertIsNone(questions.parse_reply(q, text), text)

    def test_a_reply_that_says_more_than_the_label_is_not_an_answer(self):
        q = self.question()
        for text in ("Don't ship tonight", "why not ship tonight?", "not 2", "no, wait for morning instead",
                     "ship tonight or wait"):
            self.assertIsNone(questions.parse_reply(q, text), text)

    def test_multi_select_takes_several(self):
        q = self.question(multi_select=True)
        self.assertEqual(questions.parse_reply(q, "1 and 3"), [0, 2])

    def test_match_reply_reads_the_newest_pending_question(self):
        q = questions.ask("main", SHIP)
        found, choices = questions.match_reply("main", "2")
        self.assertEqual((found["id"], choices), (q["id"], [1]))
        self.assertEqual(questions.match_reply("main", "what now"), (None, None))
        self.assertEqual(questions.match_reply("web-x", "2"), (None, None))

    def test_spoken_and_numbered_text(self):
        q = self.question()
        self.assertEqual(questions.spoken_text(q), "Ship the hub tonight or wait for the morning? One, Ship tonight. "
                                                   "Two, Wait for morning. Three, Skip it. I would pick two.")
        text = questions.numbered_text(q)
        self.assertIn("2. Wait for morning (recommended): Ships at 8", text)
        self.assertTrue(text.endswith("Reply with the number, or in your own words."))


class TestTools(StoreCase):
    def test_both_brains_list_the_tools(self):
        names = [t["name"] for t in mcp_server.tools()]
        self.assertIn("ask_user", names)
        self.assertIn("cancel_question", names)
        api = [t["function"]["name"] for t in agent.TOOLS]
        self.assertIn("ask_user", api)

    def test_the_api_brain_asks_in_its_conversation(self):
        said = agent._call_tool("ask_user", SHIP, {"guard_conv": "main"})
        q = questions.pending("main")[0]
        self.assertIn(q["id"], said)
        self.assertIn("End your turn", said)
        self.assertIn("not asked", agent._call_tool("ask_user", dict(SHIP, options=[]), {"guard_conv": "main"}))
        self.assertIn("withdrawn", agent._call_tool("cancel_question", {}, {"guard_conv": "main"}))

    def test_the_mcp_server_asks_through_the_hub_and_falls_back(self):
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {"question": {"id": "q1234abcd"}}
        post = mock.Mock(return_value=reply)
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}):
            said = mcp_server.ask_through_hub("ask_user", SHIP, "main", post=post)
            self.assertIn("q1234abcd", said)
            self.assertEqual(post.call_args.kwargs["json"]["conv"], "main")
            down = mock.Mock(side_effect=mcp_server.httpx.ConnectError("down"))
            said = mcp_server.ask_through_hub("ask_user", SHIP, "main", post=down)
        self.assertEqual(len(questions.pending("main")), 1)
        self.assertIn(questions.pending("main")[0]["id"], said)

    def test_the_mcp_server_never_writes_after_the_hub_may_have_stored_it(self):
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}):
            slow = mock.Mock(side_effect=mcp_server.httpx.ReadTimeout("slow"))
            self.assertIn("did not confirm", mcp_server.ask_through_hub("ask_user", SHIP, "main", post=slow))
            broken = mock.Mock(return_value=mock.Mock(status_code=500))
            self.assertIn("500", mcp_server.ask_through_hub("ask_user", SHIP, "main", post=broken))
        self.assertEqual(questions.pending("main"), [])

    def test_asking_is_allowed_while_locked(self):
        self.assertTrue(lockguard.allowed("mcp__diablo__ask_user"))
        self.assertTrue(lockguard.allowed("cancel_question"))

    def test_the_brain_claims_questions_into_the_turn(self):
        async def run(conv, message, *args, **kwargs):
            questions.ask(conv, SHIP)
            return brains.Turn(text="One question.", backend="api")

        with mock.patch.object(brains, "backend", return_value="api"), \
                mock.patch.object(brains, "run_api", side_effect=run), \
                mock.patch.object(brains.threads, "enabled", return_value=False), \
                mock.patch.object(brains, "record_turn"), mock.patch.object(brains.context, "answered"), \
                mock.patch.object(brains, "usage_log"), mock.patch.object(brains.lockguard, "for_turn", return_value=lockguard.Guard()):
            turn = asyncio.run(brains.ask("main", "should I ship", origin="desktop app"))
        self.assertEqual([o["kind"] for o in turn.objects], ["question"])

    def test_an_answer_goes_to_the_thread_it_was_asked_in(self):
        thread = threads.create("Shipping the hub", "")
        picked = asyncio.run(threads.pick("2", hint=[thread]))
        self.assertEqual((picked.attach, picked.source), ([thread], "answer"))


class ServerCase(StoreCase):
    def setUp(self):
        super().setUp()
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def asked(self, source="desktop", **extra):
        """A turn in which Diablo asks, stored the way the hub stores it."""
        db.listeners.append(questions.link)
        self.addCleanup(lambda: questions.link in db.listeners and db.listeners.remove(questions.link))

        async def ask(conv, text, **kwargs):
            questions.ask(conv, dict(SHIP, **extra))
            return brains.Turn(text="One question, sir.", backend="claude", objects=questions.claim(conv, 0),
                               threads=["t1"])

        with mock.patch.object(server.brains, "ask", side_effect=ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "ship it?", "source": source})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def answer(self, question_id, guard=None, **body):
        turn = brains.Turn(text="Shipping in the morning.", backend="claude")
        ask = mock.AsyncMock(return_value=turn)
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish"), \
                mock.patch.object(server.lockguard, "for_turn", return_value=guard or lockguard.Guard()):
            response = self.client.post(f"/v1/questions/{question_id}/answer", headers=HEADERS,
                                        json={"source": "desktop", **body})
        return response, ask


class TestServer(ServerCase):
    def test_a_tap_delivers_the_answer_as_a_turn_in_the_same_thread(self):
        first = self.asked()
        block = first["objects"][0]
        self.assertEqual(block["kind"], "question")
        response, ask = self.answer(block["id"], choices=[1])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.call_args.args[1], f"[answer to question {block['id']}: Wait for morning]")
        self.assertEqual(ask.call_args.kwargs["thread_hint"], ["t1"])
        said = db.get_message(response.json()["user_id"])
        self.assertEqual(said["text"], "Wait for morning")
        self.assertEqual(said["meta"]["answers"], block["id"])
        card = db.get_message(first["id"])["meta"]["objects"][0]
        self.assertEqual(card["state"], "answered")
        again, _ = self.answer(block["id"], choices=[0])
        self.assertEqual(again.status_code, 409)

    def test_a_typed_number_answers_the_open_question(self):
        block = self.asked()["objects"][0]
        turn = brains.Turn(text="Right away.", backend="claude")
        ask = mock.AsyncMock(return_value=turn)
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "1", "source": "phone"})
        self.assertEqual(ask.call_args.args[1], f"[answer to question {block['id']}: Ship tonight]")
        self.assertEqual(db.get_message(response.json()["user_id"])["text"], "1")
        self.assertEqual(response.json()["answered"], block["id"])

    def test_a_locked_banditbox_only_answers_harmless_questions(self):
        locked = lockguard.Guard(kind="desktop", screen="locked", restricted=True)
        block = self.asked()["objects"][0]
        response, ask = self.answer(block["id"], guard=locked, choices=[0])
        self.assertEqual(response.status_code, 423)
        ask.assert_not_called()
        self.assertEqual(questions.get(block["id"])["state"], "pending")
        safe = self.asked(harmless=True)["objects"][0]
        response, _ = self.answer(safe["id"], guard=locked, choices=[0])
        self.assertEqual(response.status_code, 200)

    def test_a_failed_answer_turn_reopens_the_question(self):
        block = self.asked()["objects"][0]
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(side_effect=RuntimeError("down"))), \
                mock.patch.object(server, "publish"), mock.patch.object(server.lockguard, "for_turn", return_value=lockguard.Guard()):
            response = self.client.post(f"/v1/questions/{block['id']}/answer", headers=HEADERS, json={"choices": [0]})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(questions.get(block["id"])["state"], "pending")

    def test_a_turn_that_fails_without_text_reopens_the_question(self):
        block = self.asked()["objects"][0]
        broken = brains.Turn(text="", error="timeout", backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=broken)), \
                mock.patch.object(server, "publish"), mock.patch.object(server.lockguard, "for_turn", return_value=lockguard.Guard()):
            self.client.post(f"/v1/questions/{block['id']}/answer", headers=HEADERS, json={"choices": [0]})
        self.assertEqual(questions.get(block["id"])["state"], "pending")

    def test_a_bad_request_never_records_the_answer(self):
        block = self.asked()["objects"][0]
        questions.note(block["id"], section="gone")
        response, ask = self.answer(block["id"], choices=[0])
        self.assertEqual(response.status_code, 404)
        ask.assert_not_called()
        self.assertEqual(questions.get(block["id"])["state"], "pending")
        response, ask = self.answer(block["id"], choices=[9])
        self.assertEqual(questions.get(block["id"])["state"], "pending")

    def test_a_retried_tap_gets_the_stored_reply(self):
        block = self.asked()["objects"][0]
        first, _ = self.answer(block["id"], choices=[1], client_id="tap-1")
        again, ask = self.answer(block["id"], choices=[1], client_id="tap-1")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first.json()["id"])
        ask.assert_not_called()

    def test_a_question_from_a_t3_event_hangs_under_the_pushed_message(self):
        db.listeners.append(questions.link)
        self.addCleanup(lambda: db.listeners.remove(questions.link))
        q = questions.ask("main", SHIP)
        with mock.patch.object(bot, "_app", None):
            asyncio.run(server.deliver("A thread needs you.", {"conv": "main", "thread_id": "t1"}))
        stored = questions.get(q["id"])
        self.assertIsNotNone(stored["message"])
        self.assertEqual(db.get_message(stored["message"])["meta"]["objects"][0]["id"], q["id"])

    def test_a_spoken_turn_gets_the_question_to_say(self):
        body = self.asked(source="voice")
        self.assertTrue(body["say"].startswith("Ship the hub tonight or wait for the morning? One, Ship tonight."))

    def test_list_ask_and_cancel_routes(self):
        response = self.client.post("/v1/questions", headers=HEADERS, json=dict(SHIP, conv="main"))
        self.assertEqual(response.status_code, 200)
        qid = response.json()["question"]["id"]
        self.assertEqual(self.client.post("/v1/questions", headers=HEADERS, json={"conv": "main"}).status_code, 400)
        listed = self.client.get("/v1/questions", headers=HEADERS).json()["questions"]
        self.assertEqual([q["id"] for q in listed], [qid])
        done = self.client.post("/v1/questions/cancel", headers=HEADERS, json={"id": qid}).json()
        self.assertEqual(done["question"]["state"], "cancelled")
        self.assertEqual(self.client.get("/v1/questions", headers=HEADERS).json()["questions"], [])


class TestTelegram(StoreCase):
    def test_the_card_and_keyboard_of_a_pending_question(self):
        q = questions.ask("main", SHIP)
        card = bot.question_card(q)
        self.assertIn("<b>Ship it</b>", card)
        self.assertIn("2. <b>Wait for morning</b> (recommended): Ships at 8", card)
        self.assertIn("reply to this message", card)
        rows = bot.question_keyboard(q).inline_keyboard
        self.assertEqual([r[0].text for r in rows], ["1. Ship tonight", "2. Wait for morning (recommended)", "3. Skip it"])
        self.assertEqual(rows[1][0].callback_data, f"q:{q['id']}:1")

    def test_a_multi_select_keyboard_toggles_and_has_done(self):
        q = questions.note(questions.ask("main", dict(SHIP, multi_select=True))["id"], picked=[2])
        rows = bot.question_keyboard(q).inline_keyboard
        self.assertTrue(rows[2][0].text.startswith("[x] 3."))
        self.assertTrue(rows[0][0].text.startswith("[ ] 1."))
        self.assertEqual(rows[-1][0].callback_data, f"q:{q['id']}:done")

    def test_an_answered_card_shows_the_choice_and_no_keyboard(self):
        q = questions.choose(questions.ask("main", SHIP), [0], "and tell me after")
        card = bot.question_card(q)
        self.assertIn("You chose: <b>Ship tonight</b>", card)
        self.assertIn("You said: and tell me after", card)
        self.assertIsNone(bot.question_keyboard(q))
        lapsed = questions.get(questions.ask("main", SHIP, now=0)["id"], now=questions.TTL_S + 1)
        self.assertIn("lapsed", bot.question_card(lapsed))

    def test_questions_from_other_conversations_stay_off_telegram(self):
        questions.ask("web-x", SHIP)
        fake = mock.Mock()
        fake.bot.send_message = mock.AsyncMock()
        with mock.patch.object(bot, "_app", fake), mock.patch.object(bot.settings, "get", return_value="5"):
            self.assertEqual(asyncio.run(bot.push_questions("web-x")), 0)
        fake.bot.send_message.assert_not_called()

    def test_tap_data_parses(self):
        self.assertEqual(bot.parse_tap("q:q12ab:2"), ("q12ab", 2))
        self.assertEqual(bot.parse_tap("q:q12ab:done"), ("q12ab", "done"))
        for bad in ("mail:send:3", "q::1", "q:q1:x", "q:q1", None):
            self.assertIsNone(bot.parse_tap(bad))

    def test_a_reply_to_the_card_answers_in_his_words(self):
        q = questions.ask("main", SHIP)
        questions.note(q["id"], telegram={"chat": 5, "message": 77})
        message = mock.Mock(reply_to_message=mock.Mock(message_id=77))
        answered = bot.take_answer(message, "Ship it on Friday instead")
        self.assertEqual(answered["answer"]["text"], "Ship it on Friday instead")
        q = questions.ask("main", SHIP)
        answered = bot.take_answer(mock.Mock(reply_to_message=None), "3")
        self.assertEqual(answered["answer"]["labels"], ["Skip it"])
        self.assertIsNone(bot.take_answer(mock.Mock(reply_to_message=None), "what is the weather"))

    def test_questions_from_other_turns_are_pushed_once(self):
        q = questions.ask("main", SHIP)
        sent = mock.Mock(message_id=9)
        fake = mock.Mock()
        fake.bot.send_message = mock.AsyncMock(return_value=sent)
        with mock.patch.object(bot, "_app", fake), mock.patch.object(bot.settings, "get", return_value="5"):
            self.assertEqual(asyncio.run(bot.push_questions("main")), 1)
            self.assertEqual(asyncio.run(bot.push_questions("main")), 0)
        self.assertEqual(questions.get(q["id"])["telegram"], {"chat": 5, "message": 9})


@unittest.skipUnless(importlib.util.find_spec("numpy"), "the ears need numpy, which the hub job does not install")
class TestEars(unittest.TestCase):
    def test_the_question_is_said_in_full_after_the_reply(self):
        from voice import ears

        long = "Word. " * 200
        said = ears.said_aloud(long, "Ship it? One, Ship tonight. Two, Wait.")
        self.assertTrue(said.endswith("Ship it? One, Ship tonight. Two, Wait."))
        self.assertEqual(ears.said_aloud("Fine.", None), "Fine.")


if __name__ == "__main__":
    unittest.main()
