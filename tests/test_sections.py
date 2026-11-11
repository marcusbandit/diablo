import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, devices, events, sections, server

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


def item(section_id="gym", **overrides):
    base = {
        "id": section_id, "name": "Gym", "icon": "dumbbell", "accent": "#112233",
        "brief": "", "position": 5, "hidden": False, "deleted": False, "updated": 100.0,
    }
    base.update(overrides)
    return base


class SectionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def ids(self, rows):
        return [row["id"] for row in rows]


class TestSeeds(SectionCase):
    def test_unset_list_gives_the_seeds(self):
        rows = sections.all_sections()
        self.assertEqual(self.ids(rows), ["diablo", "workout", "projects", "home", "mail", "jorbis", "usage"])
        diablo, workout, projects, home, mail, jorbis, usage = rows
        self.assertEqual([r["panels"] for r in rows], [["today"], ["workout"], [], ["network", "lights", "machines", "reminders"], ["mail"], ["jorbis"], ["usage"]])
        self.assertEqual((mail["icon"], mail["position"]), ("mail", 4))
        self.assertEqual((jorbis["icon"], jorbis["position"]), ("handshake", 5))
        self.assertEqual((usage["icon"], usage["position"]), ("insights", 6))
        self.assertEqual((diablo["name"], diablo["icon"], diablo["accent"], diablo["brief"], diablo["position"]),
                         ("Diablo", "flame", "#2d81ff", "", 0))
        self.assertEqual((workout["icon"], workout["accent"], workout["position"]), ("dumbbell", "#3fd6b8", 1))
        self.assertIn("Suggest the next session", workout["brief"])
        self.assertEqual((projects["icon"], projects["accent"], projects["position"]), ("folder", "#f2a516", 2))
        for row in rows:
            self.assertFalse(row["hidden"])
            self.assertFalse(row["deleted"])
            self.assertEqual(row["updated"], 0.0)

    def test_unset_list_is_not_stored_until_a_merge(self):
        sections.all_sections()
        self.assertIsNone(db.get_setting(sections.KEY))
        sections.merge([])
        self.assertEqual(self.ids(json.loads(db.get_setting(sections.KEY))), ["diablo", "workout", "projects", "home", "mail", "jorbis", "usage"])

    def test_corrupt_stored_list_falls_back_to_seeds(self):
        db.set_setting(sections.KEY, "{nope")
        self.assertEqual(self.ids(sections.all_sections()), ["diablo", "workout", "projects", "home", "mail", "jorbis", "usage"])

    def test_visible_skips_hidden_and_deleted_and_sorts(self):
        sections.merge([
            item("a", position=9), item("b", position=-1),
            item("c", hidden=True), item("d", deleted=True),
        ])
        self.assertEqual(self.ids(sections.visible()), ["b", "diablo", "workout", "projects", "home", "mail", "jorbis", "usage", "a"])

    def test_a_stored_list_from_before_panels_gets_new_seeds_and_default_panels(self):
        old = [dict(s, hidden=False, deleted=False, updated=1.0) for s in sections.SEEDS[:3]]
        for row in old:
            row.pop("panels")
        db.set_setting(sections.KEY, json.dumps(old))
        rows = sections.all_sections()
        self.assertEqual(self.ids(rows), ["diablo", "workout", "projects", "home", "mail", "jorbis", "usage"])
        self.assertEqual(rows[1]["panels"], ["workout"])

    def test_a_deleted_seed_stays_deleted(self):
        sections.merge([item("home", deleted=True, updated=5)])
        self.assertTrue(sections.find("home")["deleted"])

    def test_panels_survive_a_put_from_an_app_that_does_not_know_them(self):
        sections.merge([item("workout", name="Gym", updated=5)])
        self.assertEqual(sections.find("workout")["panels"], ["workout"])

    def test_panels_can_be_set_and_are_checked(self):
        sections.merge([item("gym", panels=["workout", "lights", "workout"])])
        self.assertEqual(sections.find("gym")["panels"], ["workout", "lights"])
        for bad in ("workout", [5], ["Bad Id"], ["x"] * 9):
            with self.assertRaises(sections.SectionError):
                sections.merge([item("gym", panels=bad, updated=200)])


class TestValidation(SectionCase):
    def bad(self, **overrides):
        with self.assertRaises(sections.SectionError):
            sections.merge([item(**overrides)])

    def test_id_rules(self):
        for bad in ("", "Upper", "has space", "under_score", "x" * 33, 5, None):
            self.bad(id=bad)
        sections.merge([item("a-1"), item("x" * 32)])

    def test_accent_rules(self):
        for bad in ("112233", "#12345", "#12345g", "red", 5):
            self.bad(accent=bad)

    def test_name_rules(self):
        for bad in ("", "   ", "x" * 41, 5):
            self.bad(name=bad)
        sections.merge([item(name="x" * 40)])

    def test_brief_rules(self):
        self.bad(brief="x" * 2001)
        sections.merge([item(brief="x" * 2000)])

    def test_other_fields(self):
        self.bad(position="1")
        self.bad(hidden="yes")
        self.bad(deleted=1)
        self.bad(updated="now")
        self.bad(icon="")
        with self.assertRaises(sections.SectionError):
            sections.merge("nope")
        with self.assertRaises(sections.SectionError):
            sections.merge(["nope"])

    def test_one_bad_item_saves_nothing(self):
        with self.assertRaises(sections.SectionError):
            sections.merge([item("ok"), item("Bad")])
        self.assertIsNone(db.get_setting(sections.KEY))

    def test_optional_fields_default(self):
        merged = sections.merge([{"id": "z", "name": "Z", "accent": "#ABCDEF", "updated": 1}])
        row = next(s for s in merged if s["id"] == "z")
        self.assertEqual((row["icon"], row["brief"], row["position"], row["hidden"], row["deleted"]),
                         ("folder", "", 0, False, False))
        self.assertEqual(row["accent"], "#abcdef")
        self.assertIsInstance(row["updated"], float)


class TestMerge(SectionCase):
    def get(self, section_id):
        return sections.find(section_id)

    def test_new_id_is_added(self):
        merged = sections.merge([item("gym")])
        self.assertIn("gym", self.ids(merged))
        self.assertEqual(self.get("gym")["name"], "Gym")

    def test_newest_wins(self):
        sections.merge([item("gym", name="Gym", updated=100)])
        sections.merge([item("gym", name="Older", updated=50)])
        self.assertEqual(self.get("gym")["name"], "Gym")
        sections.merge([item("gym", name="Newer", updated=200)])
        self.assertEqual(self.get("gym")["name"], "Newer")

    def test_equal_timestamp_keeps_the_stored_one(self):
        sections.merge([item("gym", name="First", updated=100)])
        sections.merge([item("gym", name="Second", updated=100)])
        self.assertEqual(self.get("gym")["name"], "First")

    def test_any_edit_beats_a_seed(self):
        sections.merge([item("workout", name="Lifting", updated=1)])
        self.assertEqual(self.get("workout")["name"], "Lifting")

    def test_tombstone_is_kept_and_returned(self):
        sections.merge([item("gym", updated=100)])
        merged = sections.merge([item("gym", deleted=True, updated=200)])
        self.assertTrue(next(s for s in merged if s["id"] == "gym")["deleted"])
        self.assertIsNone(sections.live("gym"))
        self.assertIsNotNone(sections.find("gym"))

    def test_older_edit_cannot_revive_a_tombstone(self):
        sections.merge([item("gym", deleted=True, updated=200)])
        sections.merge([item("gym", deleted=False, updated=100)])
        self.assertIsNone(sections.live("gym"))

    def test_result_is_saved(self):
        sections.merge([item("gym")])
        self.assertIn("gym", self.ids(json.loads(db.get_setting(sections.KEY))))

    def test_diablo_cannot_be_deleted(self):
        merged = sections.merge([item("diablo", deleted=True, updated=9999)])
        diablo = next(s for s in merged if s["id"] == "diablo")
        self.assertFalse(diablo["deleted"])
        self.assertIsNotNone(sections.live("diablo"))

    def test_diablo_can_be_renamed(self):
        sections.merge([item("diablo", name="Boss", updated=5)])
        self.assertEqual(self.get("diablo")["name"], "Boss")


class TestConvAndNote(SectionCase):
    def test_conv_for_is_one_memory_by_default(self):
        self.assertEqual(sections.conv_for("diablo"), brains.MAIN)
        self.assertEqual(sections.conv_for("workout"), brains.MAIN)

    def test_conv_for_can_split_sections_again(self):
        db.set_setting("memory.unified", "0")
        self.assertEqual(sections.conv_for("diablo"), brains.MAIN)
        self.assertEqual(sections.conv_for("workout"), "sec-workout")

    def test_note_has_name_brief_and_instruction(self):
        note = sections.note_for(sections.find("workout"))
        self.assertTrue(note.startswith("(Section: Workout."))
        self.assertIn("its brief: Training:", note)
        self.assertIn("You are still the whole Diablo", note)
        self.assertTrue(note.endswith(")"))

    def test_note_includes_only_tagged_facts_newest_first(self):
        db.add_fact("bench 60 kg x 8", ["workout"])
        db.add_fact("likes tea", ["personal"])
        db.add_fact("squat 80 kg x 5", ["workout", "legs"])
        note = sections.note_for(sections.find("workout"))
        self.assertIn("Facts tagged workout:", note)
        self.assertIn("bench 60 kg x 8", note)
        self.assertIn("squat 80 kg x 5", note)
        self.assertNotIn("likes tea", note)
        self.assertLess(note.index("squat"), note.index("bench"))

    def test_note_without_facts_has_no_facts_clause(self):
        self.assertNotIn("Facts tagged", sections.note_for(sections.find("workout")))

    def test_note_takes_at_most_eight_facts(self):
        for n in range(12):
            db.add_fact(f"fact{n:02d}", ["workout"])
        note = sections.note_for(sections.find("workout"))
        self.assertEqual(note.count("fact"), 8)
        self.assertIn("fact11", note)
        self.assertNotIn("fact03", note)

    def test_note_stays_under_the_limit(self):
        long_brief = "b" * 2000
        sections.merge([item("big", name="B" * 40, brief=long_brief)])
        for n in range(12):
            db.add_fact("f" * 300, ["big"])
        note = sections.note_for(sections.find("big"))
        self.assertLessEqual(len(note), 1200)
        self.assertTrue(note.endswith(")"))
        self.assertIn("Facts tagged big:", note)


class TestSectionsApi(SectionCase):
    def test_auth_is_required(self):
        for method, path in (("get", "/v1/sections"), ("put", "/v1/sections"),
                             ("get", "/v1/sections/diablo/messages"), ("post", "/v1/chat")):
            kwargs = {"json": {"sections": [], "text": "hi"}} if method != "get" else {}
            for headers in ({}, {"X-Diablo-Token": "nope"}):
                response = getattr(self.client, method)(path, headers=headers, **kwargs)
                self.assertEqual(response.status_code, 403, (method, path))

    def test_get_returns_seeds_and_now(self):
        response = self.client.get("/v1/sections", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(self.ids(body["sections"]), ["diablo", "workout", "projects", "home", "mail", "jorbis", "usage"])
        self.assertIsInstance(body["now"], float)

    def test_get_includes_tombstones(self):
        sections.merge([item("gym", deleted=True)])
        body = self.client.get("/v1/sections", headers=HEADERS).json()
        self.assertTrue(next(s for s in body["sections"] if s["id"] == "gym")["deleted"])

    def test_put_merges_and_publishes(self):
        with mock.patch.object(server, "publish") as publish:
            response = self.client.put("/v1/sections", headers=HEADERS, json={"sections": [item("gym")]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("gym", self.ids(body["sections"]))
        self.assertIn("now", body)
        publish.assert_called_once_with({"kind": "sections"})
        again = self.client.get("/v1/sections", headers=HEADERS).json()
        self.assertEqual(again["sections"], body["sections"])

    def test_put_newest_wins(self):
        self.client.put("/v1/sections", headers=HEADERS, json={"sections": [item("gym", name="New", updated=200)]})
        body = self.client.put(
            "/v1/sections", headers=HEADERS, json={"sections": [item("gym", name="Old", updated=100)]}
        ).json()
        self.assertEqual(next(s for s in body["sections"] if s["id"] == "gym")["name"], "New")

    def test_put_invalid_is_400_with_detail_and_no_publish(self):
        with mock.patch.object(server, "publish") as publish:
            response = self.client.put(
                "/v1/sections", headers=HEADERS, json={"sections": [item("gym", accent="blue")]}
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("accent", response.json()["detail"])
        publish.assert_not_called()

    def test_put_without_a_list_is_422(self):
        response = self.client.put("/v1/sections", headers=HEADERS, json={})
        self.assertEqual(response.status_code, 422)

    def test_put_diablo_tombstone_is_ignored(self):
        body = self.client.put(
            "/v1/sections", headers=HEADERS, json={"sections": [item("diablo", deleted=True, updated=99)]}
        ).json()
        self.assertFalse(next(s for s in body["sections"] if s["id"] == "diablo")["deleted"])


class TestChatSections(SectionCase):
    def chat(self, ask=None, **body):
        turn = brains.Turn(text="Done.", backend="claude", objects=[{"kind": "readout"}])
        ask = ask or mock.AsyncMock(return_value=turn)
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish") as publish:
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "hi", "source": "phone", **body})
        return response, ask, publish

    def test_default_section_is_diablo_on_the_main_conversation(self):
        response, ask, publish = self.chat(client_id="c1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.call_args.args, (brains.MAIN, "hi"))
        self.assertEqual(ask.call_args.kwargs["origin"], "phone app")
        self.assertIsNone(ask.call_args.kwargs["notes"])
        body = response.json()
        self.assertEqual(body["section"], "diablo")
        self.assertEqual(body["reply"], "Done.")
        self.assertEqual(body["backend"], "claude")
        self.assertEqual(body["objects"], [{"kind": "readout"}])

    def test_section_chat_uses_its_conversation_origin_and_note(self):
        db.add_fact("bench 60 kg x 8", ["workout"])
        response, ask, publish = self.chat(section="workout", client_id="c9")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.call_args.args, (brains.MAIN, "hi"))
        self.assertEqual(ask.call_args.kwargs["origin"], "phone app, section Workout")
        notes = ask.call_args.kwargs["notes"]
        self.assertEqual(len(notes), 1)
        self.assertIn("Section: Workout", notes[0])
        self.assertIn("bench 60 kg x 8", notes[0])

    def test_messages_are_stored_with_section_and_client_id(self):
        response, ask, publish = self.chat(section="workout", client_id="c9")
        body = response.json()
        rows = db.get_messages(10)
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
        self.assertEqual([r["source"] for r in rows], ["phone", "phone"])
        self.assertEqual(rows[0]["id"], body["user_id"])
        self.assertEqual(rows[1]["id"], body["id"])
        self.assertEqual(rows[0]["meta"], {"section": "workout", "client_id": "c9"})
        self.assertEqual(rows[1]["meta"], {"section": "workout", "client_id": "c9", "objects": [{"kind": "readout"}], "backend": "claude", "did": ""})

    def test_every_stored_message_publishes_an_event(self):
        with mock.patch.object(events, "publish") as publish:
            events.message_added(5, "phone", {"section": "projects"}, server.MAIN_SOURCES)
            events.message_added(6, "telegram", None, server.MAIN_SOURCES)
            events.message_added(7, "api", None, server.MAIN_SOURCES)
        self.assertEqual([c.args[0] for c in publish.call_args_list], [
            {"kind": "message", "section": "projects", "id": 5},
            {"kind": "message", "section": "diablo", "id": 6},
        ])

    def test_a_resent_message_returns_the_stored_reply_without_a_second_turn(self):
        first, ask, _ = self.chat(section="workout", client_id="c5")
        again, ask_again, _ = self.chat(section="workout", client_id="c5")
        ask_again.assert_not_called()
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(again.json()["user_id"], first.json()["user_id"])
        self.assertEqual(len(db.get_messages(10)), 2)

    def test_a_message_still_being_answered_is_409(self):
        db.add_message("phone", "user", "hi", {"section": "workout", "client_id": "c6"})
        response, ask, _ = self.chat(section="workout", client_id="c6")
        self.assertEqual(response.status_code, 409)
        ask.assert_not_called()

    def test_a_stale_unanswered_message_runs_again(self):
        old = db.add_message("phone", "user", "hi", {"section": "workout", "client_id": "c7"})
        with mock.patch.object(server.time, "time", return_value=time.time() + server.STALE_TURN_S + 5):
            response, ask, _ = self.chat(section="workout", client_id="c7")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(old, [m["id"] for m in db.get_messages(10)])

    def test_a_failed_turn_leaves_nothing_behind_so_a_retry_runs(self):
        response, ask, _ = self.chat(ask=mock.AsyncMock(side_effect=RuntimeError("down")), section="workout", client_id="c8")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(db.get_messages(10), [])
        retry, ask, _ = self.chat(section="workout", client_id="c8")
        self.assertEqual(retry.status_code, 200)

    def test_integer_client_id_is_kept(self):
        self.chat(client_id=7)
        self.assertEqual(db.get_messages(1)[0]["meta"]["client_id"], 7)

    def test_unknown_section_is_404_and_nothing_is_stored(self):
        response, ask, publish = self.chat(section="nope")
        self.assertEqual(response.status_code, 404)
        ask.assert_not_called()
        self.assertEqual(db.get_messages(10), [])

    def test_deleted_section_is_404(self):
        sections.merge([item("gym", deleted=True)])
        response, ask, publish = self.chat(section="gym")
        self.assertEqual(response.status_code, 404)

    def test_hidden_section_still_answers(self):
        sections.merge([item("gym", hidden=True)])
        response, ask, publish = self.chat(section="gym")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.call_args.args[0], brains.MAIN)

    def test_empty_text_is_400(self):
        response, ask, publish = self.chat(text="  ")
        self.assertEqual(response.status_code, 400)

    def test_failed_turn_is_reported_in_the_reply(self):
        turn = brains.Turn(error="claude exited with code 1", backend="claude")
        response, ask, publish = self.chat(ask=mock.AsyncMock(return_value=turn))
        self.assertIn("claude exited with code 1", response.json()["reply"])

    def test_ask_exception_is_502(self):
        response, ask, publish = self.chat(ask=mock.AsyncMock(side_effect=RuntimeError("boom")))
        self.assertEqual(response.status_code, 502)

    def test_old_clients_without_section_fields_still_work(self):
        turn = brains.Turn(text="ok", backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "hi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.get_messages(1)[0]["source"], "fleet")


class TestSectionMessages(SectionCase):
    def fetch(self, section_id, **params):
        response = self.client.get(f"/v1/sections/{section_id}/messages", headers=HEADERS, params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()["messages"]

    def test_unknown_section_is_404(self):
        response = self.client.get("/v1/sections/nope/messages", headers=HEADERS)
        self.assertEqual(response.status_code, 404)

    def test_message_shape(self):
        db.add_message("phone", "user", "squats", {"section": "workout", "client_id": "a"})
        rows = self.fetch("workout")
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"id", "ts", "role", "text", "meta", "source", "threads"})
        self.assertEqual(rows[0]["text"], "squats")
        self.assertEqual(rows[0]["source"], "phone")
        self.assertEqual(rows[0]["meta"]["client_id"], "a")

    def test_only_that_sections_messages_oldest_first(self):
        first = db.add_message("phone", "user", "one", {"section": "workout"})
        db.add_message("phone", "user", "other", {"section": "projects"})
        second = db.add_message("phone", "assistant", "two", {"section": "workout"})
        self.assertEqual([m["id"] for m in self.fetch("workout")], [first, second])
        self.assertEqual([m["text"] for m in self.fetch("projects")], ["other"])

    def test_after_cursor(self):
        ids = [db.add_message("phone", "user", f"m{n}", {"section": "workout"}) for n in range(5)]
        self.assertEqual([m["id"] for m in self.fetch("workout", after=ids[1])], ids[2:])
        self.assertEqual(self.fetch("workout", after=ids[-1]), [])

    def test_limit_and_cap(self):
        for n in range(5):
            db.add_message("phone", "user", f"m{n}", {"section": "workout"})
        self.assertEqual(len(self.fetch("workout", limit=2)), 2)
        self.assertEqual([m["text"] for m in self.fetch("workout", limit=2)], ["m3", "m4"])
        self.assertEqual(len(self.fetch("workout", limit=100000)), 5)
        self.assertEqual(len(self.fetch("workout", limit=0)), 1)

    def test_limit_caps_at_500(self):
        with mock.patch.object(db, "section_messages", return_value=[]) as query:
            self.fetch("workout", limit=100000)
        self.assertEqual(query.call_args.args[2], 500)

    def test_diablo_includes_the_main_thread(self):
        tg_user = db.add_message("telegram", "user", "lights", None)
        tg_reply = db.add_message("telegram", "assistant", "done", {"backend": "claude"})
        fleet = db.add_message("fleet", "user", "from fleet app", None)
        phone_old = db.add_message("phone", "user", "phone without section", None)
        tagged = db.add_message("phone", "user", "tagged", {"section": "diablo"})
        db.add_message("phone", "user", "workout", {"section": "workout"})
        db.add_message("api", "user", "web chat", {"chat": "scratch"})
        db.add_message("voice", "user", "voice loop", None)
        self.assertEqual(
            [m["id"] for m in self.fetch("diablo")],
            [tg_user, tg_reply, fleet, phone_old, tagged],
        )

    def test_diablo_after_cursor(self):
        first = db.add_message("telegram", "user", "a", None)
        second = db.add_message("telegram", "assistant", "b", None)
        self.assertEqual([m["id"] for m in self.fetch("diablo", after=first)], [second])

    def test_other_sections_do_not_get_untagged_telegram_messages(self):
        db.add_message("telegram", "user", "lights", None)
        self.assertEqual(self.fetch("workout"), [])

    def test_a_sectioned_telegram_source_message_stays_in_its_section(self):
        db.add_message("telegram", "user", "odd", {"section": "workout"})
        self.assertEqual(self.fetch("diablo"), [])
        self.assertEqual(len(self.fetch("workout")), 1)

    def test_deleted_section_history_is_still_readable(self):
        sections.merge([item("gym")])
        db.add_message("phone", "user", "kept", {"section": "gym"})
        sections.merge([item("gym", deleted=True, updated=200)])
        self.assertEqual([m["text"] for m in self.fetch("gym")], ["kept"])

    def test_chat_then_messages_round_trip(self):
        turn = brains.Turn(text="Nice set.", backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)):
            self.client.post("/v1/chat", headers=HEADERS,
                             json={"text": "bench 60x8", "source": "phone", "section": "workout", "client_id": "x"})
        self.assertEqual([m["text"] for m in self.fetch("workout")], ["bench 60x8", "Nice set."])
        self.assertEqual([m["text"] for m in self.fetch("diablo")], [])


if __name__ == "__main__":
    unittest.main()
