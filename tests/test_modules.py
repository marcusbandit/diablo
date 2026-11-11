import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, devices, mailbox, mcp_server, modules, server
from hub.modules import lights, reminders, workout

LAMP_STATES = [
    {"id": "left", "name": "corner lamp", "on": True, "color": "#f2f1ee", "bri": 127, "ct": 2700,
     "caps": {"color": False, "ct": [2203, 4505]}},
    {"id": "up", "name": "ceiling panel", "on": True, "color": "#ff8800", "bri": 254, "ct": None,
     "caps": {"color": True, "ct": [2000, 6535]}},
]

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


def overview(generated=None, **overrides):
    data = {
        "schema": 1, "generated": time.time() if generated is None else generated, "day": workout.day_of(time.time()),
        "mode": "challenge", "today": {"sets": 3, "line": "30 push-ups in 3 sets", "trained": []},
        "streak": 5, "last": {"ts": 1, "exercise": "push-ups", "did": 10},
        "week": {"neglected": ["obliques", "calves"], "work": {"chest": 1.0, "abs": 0.4, "quads": 0.25}, "stretch": {"hamstrings": 0.05}},
        "timeline": {
            "first": "2026-10-10", "days": 28,
            "sets": {g: [0] * 27 + [n] for g, n in (("push", 3), ("pull", 0), ("legs", 1), ("core", 0))},
            "recent": [{"date": "2026-11-06", "sets": 3, "lines": ["30 push-ups in 3 sets"]},
                       {"date": "2026-11-05", "sets": 9, "lines": ["99 push-ups in 7 sets"]}],
        },
        "challenge": {"day": 8, "days": 30, "done": 30, "target": 75, "left": 45},
    }
    data.update(overrides)
    return data


class ModuleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        db.init(str(root / "diablo.db"))
        mail_db = mock.patch.dict("os.environ", {"DIABLO_MAIL_DB": str(root / "mail.db")})
        mail_db.start()
        self.addCleanup(mail_db.stop)
        mailbox.reset_for_tests()
        self.addCleanup(mailbox.reset_for_tests)
        self.state = root / "pushup"
        self.inbox = root / "pushup-inbox"
        self.state.mkdir()
        db.set_setting("workout.dir", str(self.state))
        db.set_setting("workout.inbox", str(self.inbox))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def write_overview(self, **kwargs):
        (self.state / "overview.json").write_text(json.dumps(overview(**kwargs)))

    def kinds(self, blocks):
        return [b["kind"] for b in blocks]


class TestWorkoutLog(ModuleCase):
    def test_a_set_lands_in_this_hubs_own_inbox_file(self):
        entry = workout.log({"kind": "set", "exercise": "Push-Ups", "did": 12}, now=1793900000)
        files = list(self.inbox.glob("diablo-*.jsonl"))
        self.assertEqual(len(files), 1)
        line = json.loads(files[0].read_text().strip())
        self.assertEqual(line, entry)
        self.assertEqual((line["result"], line["exercise"], line["did"], line["ts"], line["source"]),
                         ("manual", "push-ups", 12, 1793900000, "diablo"))
        self.assertTrue(line["id"].startswith("diablo-"))

    def test_a_session_keeps_known_activities_and_labels_the_rest(self):
        known = workout.entry_for({"kind": "session", "activity": "climbing", "minutes": 90, "effort": "hard"}, 100)
        self.assertEqual((known["result"], known["activity"], known["minutes"], known["effort"]), ("session", "climbing", 90, "hard"))
        self.assertNotIn("label", known)
        odd = workout.entry_for({"kind": "session", "activity": "Padel", "minutes": 60.4, "effort": "brutal"}, 100)
        self.assertEqual((odd["activity"], odd["label"], odd["minutes"], odd["effort"]), ("padel", "padel", 60, "normal"))

    def test_minutes_ago_moves_the_time_back(self):
        entry = workout.entry_for({"kind": "set", "exercise": "squats", "did": 5, "minutes_ago": 30}, 10000)
        self.assertEqual(entry["ts"], 10000 - 1800)

    def test_bad_numbers_are_refused(self):
        for args in (
            {"kind": "set", "exercise": "squats", "did": 0},
            {"kind": "set", "exercise": "squats", "did": 1001},
            {"kind": "set", "exercise": "squats", "did": 2.5},
            {"kind": "set", "exercise": "squats", "did": True},
            {"kind": "set", "did": 5},
            {"kind": "session", "activity": "running", "minutes": 0},
            {"kind": "session", "activity": "running", "minutes": 1441},
            {"kind": "session", "minutes": 30},
            {"kind": "set", "exercise": "squats", "did": 5, "minutes_ago": -1},
            {"kind": "nap"},
        ):
            with self.assertRaises(ValueError, msg=args):
                workout.entry_for(args, 100)

    def test_the_tool_says_what_it_logged_or_why_not(self):
        self.assertIn("logged 10 push-ups", modules.call("workout_log", {"kind": "set", "exercise": "push-ups", "did": 10}))
        self.assertIn("not logged", modules.call("workout_log", {"kind": "set", "exercise": "push-ups", "did": -3}))


class TestWorkoutRead(ModuleCase):
    def test_stats_without_an_overview_say_so(self):
        self.assertIn("has not synced", modules.call("workout_stats", {}))

    def test_stats_read_the_overview_and_count_pending_lines(self):
        self.write_overview(generated=time.time() - 60)
        workout.log({"kind": "set", "exercise": "push-ups", "did": 10})
        text = modules.call("workout_stats", {})
        self.assertIn("30 push-ups in 3 sets", text)
        self.assertIn("day 8 of 30: 30 of 75", text)
        self.assertIn("Streak: 5 days", text)
        self.assertIn("obliques, calves", text)
        self.assertIn("not counted by pushup yet: 10 push-ups", text)

    def test_a_backdated_line_logged_after_the_overview_is_still_pending(self):
        self.write_overview(generated=time.time() - 60)
        workout.log({"kind": "set", "exercise": "push-ups", "did": 20, "minutes_ago": 30})
        self.assertEqual(len(workout.pending(overview()["generated"] - 1)), 1)

    def test_only_todays_push_ups_count_toward_the_challenge_and_aliases_match(self):
        now = time.time()
        data = {"day": workout.day_of(now)}
        waiting = [
            {"result": "manual", "exercise": "pushups", "did": 10, "ts": now},
            {"result": "manual", "exercise": "Decline Push-Ups", "did": 5, "ts": now},
            {"result": "manual", "exercise": "push-ups", "did": 7, "ts": now - 2 * 86400},
            {"result": "manual", "exercise": "squats", "did": 9, "ts": now},
        ]
        self.assertEqual(workout.push_ups_today(data, waiting), 15)

    def test_the_day_rolls_at_seven(self):
        import datetime as dt
        early = dt.datetime(2026, 11, 7, 6, 30).timestamp()
        late = dt.datetime(2026, 11, 7, 7, 30).timestamp()
        self.assertEqual((workout.day_of(early), workout.day_of(late)), ("2026-11-06", "2026-11-07"))

    def test_short_timeline_lists_are_padded_to_the_labels(self):
        data = overview()
        data["timeline"]["sets"]["core"] = [1]
        bars = workout._bars(data)
        self.assertTrue(all(len(s["values"]) == len(bars["labels"]) for s in bars["series"]))

    def test_lines_older_than_the_overview_are_not_pending(self):
        workout.log({"kind": "set", "exercise": "push-ups", "did": 10}, now=time.time() - 600)
        self.write_overview()
        self.assertEqual(workout.pending(overview()["generated"] - 1), [])

    def test_an_old_overview_says_when_it_last_refreshed(self):
        self.write_overview(generated=time.time() - 3 * 3600)
        self.assertIn("3 h old", modules.call("workout_stats", {}))
        goal = workout.panel({})[0]
        self.assertIn("has not refreshed since", goal["caption"])
        self.assertTrue(goal["stale"])
        self.assertAlmostEqual(goal["updated"]["age"], 3 * 3600, delta=5)
        self.assertRegex(goal["updated"]["at"], r"^\d\d:\d\d$")

    def test_a_fresh_overview_is_not_stale(self):
        self.write_overview(generated=time.time() - 60)
        goal = workout.panel({})[0]
        self.assertFalse(goal["stale"])
        self.assertNotIn("refreshed", goal["caption"])

    def test_no_text_the_panel_sends_names_a_file_or_a_machine(self):
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)

        workout.log({"kind": "set", "exercise": "push-ups", "did": 10})
        for setup in (lambda: None, lambda: self.write_overview(generated=time.time() - 5 * 3600),
                      lambda: (self.state / "overview.json").write_text("{")):
            setup()
            for text in strings(workout.panel({})):
                for word in ("pushup", "banditbox", ".json", "syncs", "Syncthing"):
                    self.assertNotIn(word.lower(), text.lower(), text)

    def test_the_log_button_takes_its_size_from_the_last_set(self):
        self.write_overview(last={"ts": 1, "exercise": "push-ups", "did": 14})
        first = workout.panel({})[1]["items"][0]
        self.assertEqual((first["label"], first["say"]), ("Log 14 push-ups", "Log 14 push-ups"))
        self.write_overview(last={"ts": 1, "exercise": "squats", "did": 14})
        first = workout.panel({})[1]["items"][0]
        self.assertEqual(first["label"], "Log push-ups")
        self.assertIs(first["send"], False)

    def test_week_rows_carry_their_date_and_sets_per_group_for_the_chart(self):
        self.write_overview()
        rows = next(b for b in workout.panel({}) if b.get("link") == "days")["items"]
        self.assertEqual(rows[0]["date"], "2026-11-06")
        self.assertEqual(rows[0]["groups"], {"push": 3, "legs": 1})
        self.assertEqual(rows[0]["lines"], ["30 push-ups in 3 sets"])
        bars = next(b for b in workout.panel({}) if b["kind"] == "bars")
        self.assertEqual(bars["dates"][-1], "2026-11-06")
        self.assertEqual([s["tone"] for s in bars["series"]], ["accent", "ok", "warn", "ink"])

    def test_a_day_with_sets_is_never_called_a_rest_day(self):
        data = overview()
        data["timeline"]["recent"] = [{"date": "2026-11-06", "sets": 2, "lines": []}, {"date": "2026-11-05", "sets": 0, "lines": []}]
        rows = workout.week_rows(data, workout.day_of(time.time()))
        self.assertEqual(rows[0]["detail"], "")
        self.assertEqual(rows[1]["detail"], "Rest day")

    def test_not_counted_yet_replaces_the_machine_name(self):
        self.write_overview(generated=time.time() - 60)
        workout.log({"kind": "set", "exercise": "push-ups", "did": 10})
        queue = next(b for b in workout.panel({}) if b.get("link") == "queue")
        self.assertEqual(queue["title"], "Not counted yet")

    def test_an_unreadable_overview_is_told_apart_from_a_missing_one(self):
        missing = workout.panel({})[0]
        self.assertEqual((missing["state"], missing["title"]), ("waiting", "No training logged yet"))
        self.assertIn("15", missing["text"])
        (self.state / "overview.json").write_text("{")
        broken = workout.panel({})[0]
        self.assertEqual(broken["state"], "unreadable")
        self.write_overview(schema=2)
        self.assertEqual(workout.panel({})[0]["state"], "unreadable")

    def test_an_overview_without_a_usable_stamp_is_unreadable(self):
        for stamp in (0, None, "now", True):
            self.write_overview(generated=stamp)
            data = json.loads((self.state / "overview.json").read_text())
            data["generated"] = stamp
            (self.state / "overview.json").write_text(json.dumps(data))
            self.assertEqual(workout.panel({})[0]["state"], "unreadable", stamp)
        goal = None
        self.write_overview(generated=time.time() - 60)
        goal = workout.panel({})[0]
        self.assertEqual(goal["updated"]["every"], workout.REFRESH_MIN)



class TestPanelsApi(ModuleCase):
    def test_a_section_panel_lists_its_modules_blocks_tagged_with_the_module(self):
        self.write_overview()
        body = self.client.get("/v1/sections/workout/panel", headers=HEADERS).json()
        self.assertEqual(body["section"], "workout")
        self.assertEqual(body["objects"][0]["kind"], "goal")
        self.assertTrue(all(o["module"] == "workout" for o in body["objects"]))

    def test_unknown_sections_are_404_and_tokens_are_needed(self):
        self.assertEqual(self.client.get("/v1/sections/nope/panel", headers=HEADERS).status_code, 404)
        self.assertIn(self.client.get("/v1/sections/workout/panel").status_code, (401, 403))

    def test_a_failing_module_shows_a_notice_not_an_error_or_its_exception(self):
        with mock.patch.object(lights, "panel", side_effect=RuntimeError("bridge on fire")):
            blocks = modules.panel("lights", {})
        self.assertEqual((blocks[0]["kind"], blocks[0]["tone"], blocks[0]["retry"]), ("notice", "fail", True))
        self.assertNotIn("bridge on fire", json.dumps(blocks))

    def test_today_collects_one_tile_per_module(self):
        self.write_overview()
        with mock.patch.object(lights, "lamps", return_value=[{"id": "left", "name": "corner lamp", "on": True, "level": 50}]), \
                mock.patch.object(reminders, "upcoming", return_value=[{"text": "dinner", "next": "Sunday 8 November at 13:00", "next_local": "2099-11-08T13:00"}]):
            body = self.client.get("/v1/sections/diablo/panel", headers=HEADERS).json()
        items = body["objects"][0]["items"]
        self.assertEqual([i["open"] for i in items], ["workout", "lights", "reminders"])
        self.assertEqual(items[1]["value"], "1")
        self.assertEqual((items[2]["value"], items[2]["label"]), ("13:00", "8 Nov 2099"))

    def test_act_runs_the_module_and_returns_the_fresh_panel(self):
        with mock.patch.object(lights.hue, "hue_light", return_value="all lights off") as hue_light, \
                mock.patch.object(lights, "lamps", return_value=[]), \
                mock.patch.object(reminders, "upcoming", return_value=[]), \
                mock.patch.object(server, "publish") as publish:
            response = self.client.post("/v1/act", headers=HEADERS, json={
                "module": "lights", "action": "scene", "args": {"name": "off"}, "section": "home"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["message"], "all lights off")
        hue_light.assert_called_once_with("off", "all", None)
        self.assertEqual(body["panel"]["section"], "home")
        publish.assert_called_once_with({"kind": "panel", "modules": ["lights", "today"]})

    def test_act_levels_and_toggles(self):
        with mock.patch.object(lights.hue, "hue_light", return_value="ok") as hue_light:
            lights.act("level", {"lamp": "left", "level": 40})
            lights.act("level", {"lamp": "left", "level": 0})
            lights.act("toggle", {"lamp": "up"})
        self.assertEqual(hue_light.call_args_list, [
            mock.call("on", "left", 40), mock.call("off", "left"), mock.call("toggle", "up")])

    def test_a_tap_that_did_nothing_is_reported_as_failed(self):
        with mock.patch.object(lights.hue, "hue_light", return_value="no hue bridge configured"):
            response = self.client.post("/v1/act", headers=HEADERS, json={
                "module": "lights", "action": "toggle", "args": {"lamp": "left"}})
        self.assertEqual(response.status_code, 502)
        self.assertIn("no hue bridge", response.json()["detail"])
        with mock.patch.object(reminders, "run", return_value={"ok": False, "error": "there is no reminder #5"}):
            response = self.client.post("/v1/act", headers=HEADERS, json={
                "module": "reminders", "action": "cancel", "args": {"id": 5}})
        self.assertEqual(response.status_code, 502)

    def test_lamp_reads_are_cached_and_a_tap_clears_the_cache(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "lamp_states", return_value=[]) as states, \
                mock.patch.object(lights.hue, "hue_light", return_value="ok"):
            lights.lamps()
            lights.lamps()
            self.assertEqual(states.call_count, 1)
            lights.act("toggle", {"lamp": "up"})
            lights.lamps()
            self.assertEqual(states.call_count, 2)

    def test_color_and_temperature_reach_the_lamp(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "hue_light", return_value="ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES):
            lights.act("color", {"lamp": "up", "hex": "#ff8800"})
            lights.act("temperature", {"lamp": "left", "kelvin": 2700})
            lights.act("color", {"hex": "#00ff00"})
        self.assertEqual(hue_light.call_args_list, [
            mock.call("on", "up", color="#ff8800"),
            mock.call("on", "left", temperature=2700),
            mock.call("on", "up", color="#00ff00")])

    def test_a_colour_for_a_white_only_lamp_is_refused_with_a_reason(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "hue_light", return_value="ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES):
            with self.assertRaises(lights.ActFailed) as caught:
                lights.act("color", {"lamp": "left", "hex": "#ff8800"})
        self.assertIn("corner lamp cannot show colour", str(caught.exception))
        hue_light.assert_not_called()

    def test_all_skips_lamps_that_cannot_do_it_instead_of_failing_the_tap(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "hue_light", side_effect=lambda *a, **k: f"{a[1]}: ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES):
            self.assertEqual(lights.act("color", {"hex": "#00ff00"}), "up: ok")
        self.assertEqual(hue_light.call_args_list, [mock.call("on", "up", color="#00ff00")])

    def test_all_skips_a_lamp_without_a_white_range_for_temperature(self):
        lights._cache.clear()
        mixed = [LAMP_STATES[0], dict(LAMP_STATES[1], caps={"color": True, "ct": None})]
        with mock.patch.object(lights.hue, "hue_light", side_effect=lambda *a, **k: f"{a[1]}: ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=mixed):
            self.assertEqual(lights.act("temperature", {"lamp": "all", "kelvin": 3000}), "left: ok")
        hue_light.assert_called_once_with("on", "left", temperature=3000)

    def test_all_goes_out_as_all_when_every_lamp_can_do_it(self):
        lights._cache.clear()
        both = [dict(LAMP_STATES[0], caps={"color": True, "ct": [2203, 4505]}), LAMP_STATES[1]]
        with mock.patch.object(lights.hue, "hue_light", return_value="ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=both):
            lights.act("color", {"hex": "#00ff00"})
        hue_light.assert_called_once_with("on", "all", color="#00ff00")

    def test_all_with_no_capable_lamp_says_so(self):
        lights._cache.clear()
        white = [dict(LAMP_STATES[0])]
        with mock.patch.object(lights.hue, "hue_light", return_value="ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=white):
            with self.assertRaises(lights.ActFailed) as caught:
                lights.act("color", {"hex": "#00ff00"})
        self.assertIn("no lamp here can show colour", str(caught.exception))
        hue_light.assert_not_called()

    def test_aliases_of_a_lamp_are_checked_like_its_id(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "hue_light", return_value="ok") as hue_light, \
                mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES):
            for alias in ("corner", "Corner Lamp", "the corner"):
                with self.assertRaises(lights.ActFailed, msg=alias):
                    lights.act("color", {"lamp": alias, "hex": "#ff8800"})
            lights.act("color", {"lamp": "panel", "hex": "#ff8800"})
        hue_light.assert_called_once_with("on", "up", color="#ff8800")

    def test_bad_colour_and_temperature_values_are_value_errors(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "hue_light", return_value="ok"), \
                mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES):
            for action, args in (
                ("color", {"lamp": "up", "hex": "orange"}),
                ("color", {"lamp": "up"}),
                ("temperature", {"lamp": "up", "kelvin": 300}),
                ("temperature", {"lamp": "up", "kelvin": "warm"}),
                ("temperature", {"lamp": "up", "kelvin": True}),
                ("scene", {"id": "bad id!"}),
            ):
                with self.assertRaises(ValueError, msg=(action, args)):
                    lights.act(action, args)

    def test_a_refused_write_is_reported_as_failed(self):
        lights._cache.clear()
        with mock.patch.object(lights.hue, "hue_light", return_value="wood lamp: refused"), \
                mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES):
            with self.assertRaises(lights.ActFailed):
                lights.act("color", {"lamp": "right", "hex": "#ff0000"})
            with self.assertRaises(lights.ActFailed):
                lights.act("toggle", {"lamp": "right"})

    def test_a_scene_by_id_goes_to_the_bridge_and_a_refusal_fails(self):
        lights._scenes_cache.clear()
        listed = [{"id": "AbC123-x", "name": "Relax", "group": "2"}]
        with mock.patch.object(lights.hue, "scenes", return_value=listed) as read, \
                mock.patch.object(lights.hue, "recall_scene", return_value="Relax: scene on") as recall:
            self.assertEqual(lights.act("scene", {"id": "AbC123-x"}), "Relax: scene on")
            lights.act("scene", {"id": "AbC123-x"})
        recall.assert_called_with("AbC123-x", "2", "Relax")
        self.assertEqual(read.call_count, 1)
        with mock.patch.object(lights.hue, "recall_scene", return_value="Relax: refused"):
            with self.assertRaises(lights.ActFailed):
                lights.act("scene", {"id": "AbC123-x"})

    def test_a_scene_missing_from_the_cache_is_looked_up_on_the_bridge(self):
        lights._scenes_cache.clear()
        with mock.patch.object(lights.hue, "scenes", return_value=[]), \
                mock.patch.object(lights.hue, "recall_scene", return_value="unknown scene zzz") as recall:
            with self.assertRaises(lights.ActFailed):
                lights.act("scene", {"id": "zzz"})
        recall.assert_called_once_with("zzz")

    def test_the_named_scenes_still_work_beside_ids(self):
        with mock.patch.object(lights.hue, "hue_light", return_value="all lights off") as hue_light:
            lights.act("scene", {"name": "off"})
        hue_light.assert_called_once_with("off", "all", None)

    def test_the_panel_lists_lamps_with_caps_and_the_scenes(self):
        lights._cache.clear()
        lights._scenes_cache.clear()
        listed = [{"id": "s1", "name": "Relax", "colors": ["#ffb347", "#f2f1ee"]}, {"id": "s2", "name": "Concentrate"}]
        with mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES), \
                mock.patch.object(lights.hue, "scenes", return_value=[dict(item, group="2") for item in listed]):
            blocks = lights.panel({})
        self.assertEqual(self.kinds(blocks), ["lamps", "scenes", "actions"])
        corner = next(lamp for lamp in blocks[0]["lamps"] if lamp["id"] == "left")
        self.assertEqual((corner["ct"], corner["caps"]), (2700, {"color": False, "ct": [2203, 4505]}))
        panel_lamp = next(lamp for lamp in blocks[0]["lamps"] if lamp["id"] == "up")
        self.assertEqual(panel_lamp["caps"], {"color": True, "ct": [2000, 6535]})
        self.assertEqual(blocks[1], {"kind": "scenes", "title": "Scenes", "items": listed})

    def test_the_panel_has_no_scenes_block_when_the_bridge_lists_none(self):
        lights._cache.clear()
        lights._scenes_cache.clear()
        with mock.patch.object(lights.hue, "lamp_states", return_value=LAMP_STATES), \
                mock.patch.object(lights.hue, "scenes", return_value=[]):
            self.assertEqual(self.kinds(lights.panel({})), ["lamps", "actions"])

    def test_bad_acts_are_400_and_unknown_modules_404(self):
        self.assertEqual(self.client.post("/v1/act", headers=HEADERS, json={
            "module": "lights", "action": "explode", "args": {}}).status_code, 400)
        self.assertEqual(self.client.post("/v1/act", headers=HEADERS, json={
            "module": "today", "action": "x", "args": {}}).status_code, 404)
        self.assertEqual(self.client.post("/v1/act", headers=HEADERS, json={
            "module": "nope", "action": "x", "args": {}}).status_code, 404)

    def test_modules_are_listed(self):
        body = self.client.get("/v1/modules", headers=HEADERS).json()
        ids = [m["id"] for m in body["modules"]]
        self.assertEqual(ids, ["today", "workout", "lights", "reminders", "mail", "jorbis", "network", "machines", "usage"])


class TestReminders(ModuleCase):
    def setUp(self):
        super().setUp()
        reminders._cache.clear()

    def test_panel_lists_upcoming_ones(self):
        rows = [{"id": 1, "text": "dinner", "next": "Sunday 8 November at 13:00", "repeats": "once"},
                {"id": 2, "text": "stretch", "next": "Monday 9 November at 09:00", "repeats": "daily"}]
        with mock.patch.object(reminders, "run", return_value={"ok": True, "result": rows}):
            blocks = reminders.panel({})
        self.assertEqual([i["title"] for i in blocks[0]["items"]], ["dinner", "stretch"])
        self.assertEqual(blocks[0]["items"][1]["trailing"], "daily")

    def test_a_row_without_a_next_time_does_not_break_the_tiles(self):
        reminders._cache.clear()
        with mock.patch.object(reminders, "run", return_value={"ok": True, "result": [{"id": 1, "text": None, "next": None}]}):
            self.assertEqual(reminders.summary()["value"], "")
            self.assertEqual(reminders.summary()["label"], "Next reminder")
            item = reminders.panel({})[0]["items"][0]
            self.assertEqual((item["title"], item["detail"], item["trailing"]), ("", "", ""))

    def test_a_missing_cli_is_a_notice(self):
        reminders._cache.clear()
        with mock.patch.object(reminders, "REMIND", "/nowhere/remind"):
            self.assertEqual(reminders.panel({})[0]["kind"], "notice")
            self.assertIn("not installed", modules.call("add_reminder", {"text": "x", "due": "+5m"}))

    def test_add_passes_the_order_to_the_cli(self):
        with mock.patch.object(reminders, "run", return_value={"ok": True, "message": "#3 set"}) as run:
            self.assertEqual(modules.call("add_reminder", {"text": "tea", "due": "+20m", "recurrence": "daily"}), "#3 set")
        run.assert_called_once_with(["add", "--repeat", "daily", "--source", "diablo", "--", "+20m", "tea"])


class TestBrainWiring(ModuleCase):
    def test_claude_sees_the_module_tools_over_mcp(self):
        names = [t["name"] for t in mcp_server.tools()]
        for name in ("workout_log", "workout_stats", "add_reminder", "list_reminders", "cancel_reminder"):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)))

    def test_mcp_calls_reach_the_module(self):
        self.assertIn("logged 5 squats", mcp_server.call("workout_log", {"kind": "set", "exercise": "squats", "did": 5}))

    def test_a_writing_tool_in_a_turn_refreshes_its_panel_once_the_turn_is_done(self):
        turn = brains.Turn()
        with mock.patch.object(brains.events, "publish") as publish:
            brains.collect_object(turn, "mcp__diablo__workout_log", {"kind": "set"})
            brains.collect_object(turn, "mcp__diablo__workout_stats", {})
            publish.assert_not_called()
            brains.refresh_panels(turn)
        publish.assert_called_once_with({"kind": "panel", "modules": ["workout", "today"]})

    def test_a_reading_tool_refreshes_nothing(self):
        turn = brains.Turn()
        with mock.patch.object(brains.events, "publish") as publish:
            brains.collect_object(turn, "mcp__diablo__list_reminders", {})
            brains.refresh_panels(turn)
        publish.assert_not_called()

    def test_the_api_brain_refreshes_after_the_tool_ran(self):
        with mock.patch.object(modules.events, "publish") as publish:
            modules.call("workout_log", {"kind": "set", "exercise": "squats", "did": 5})
            self.assertTrue(list(self.inbox.glob("diablo-*.jsonl")))
            modules.call("workout_stats", {})
        publish.assert_called_once_with({"kind": "panel", "modules": ["workout", "today"]})


if __name__ == "__main__":
    unittest.main()
