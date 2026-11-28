import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pushup"))

import catalog
import figure
import musclemap
import schedule
from hub import modules
from hub.modules import exercises, workout
from test_modules import ModuleCase


def day_of(ts):
    return (dt.datetime.fromtimestamp(ts) - dt.timedelta(hours=7)).date()


class TestAnatomyAndCatalog(unittest.TestCase):
    def test_pushup_variants_and_skills_have_distinct_muscles_tendons_and_points(self):
        for name in (
            "push-ups", "wide push-ups", "diamond push-ups", "incline push-ups", "decline push-ups",
            "planche lean", "pseudo planche push-ups", "frog stand", "tuck planche",
            "tuck front lever", "inverted rows", "wide inverted rows", "scapular rows",
            "lying leg raises", "hollow hold", "hollow rocks", "dragon flag negatives", "dead bugs",
        ):
            self.assertIn(name, catalog.EXERCISES, name)
            info = catalog.info(name)
            self.assertTrue(info["muscles"], name)
            self.assertTrue(info.get("point"), name)
        self.assertGreater(catalog.EXERCISES["wide push-ups"]["muscles"]["chest"], catalog.EXERCISES["wide push-ups"]["muscles"]["triceps"])
        self.assertGreater(catalog.EXERCISES["diamond push-ups"]["muscles"]["triceps"], catalog.EXERCISES["wide push-ups"]["muscles"]["triceps"])

    def test_home_gear_blocks_pullup_bar_moves_when_no_bar(self):
        home_gear = ["chair", "table"]
        self.assertFalse(catalog.can_do("assisted pull-ups", home_gear))
        self.assertFalse(catalog.can_do("pull-ups", home_gear))
        self.assertTrue(catalog.can_do("inverted rows", home_gear))
        self.assertTrue(catalog.can_do("tuck front lever", home_gear))
        self.assertTrue(catalog.can_do("planche lean", home_gear))
        self.assertTrue(catalog.can_do("lying leg raises", home_gear))
        self.assertTrue(catalog.can_do("incline push-ups", home_gear))

    def test_natural_language_phrase_resolution(self):
        self.assertEqual(exercises.resolve("laying on my back raising straight legs up and down"), "lying leg raises")
        self.assertEqual(exercises.resolve("hand lever"), "tuck planche")
        self.assertEqual(exercises.resolve("hanging lever"), "tuck front lever")
        self.assertEqual(exercises.resolve("close push-ups"), "diamond push-ups")
        self.assertEqual(exercises.resolve("inclines"), "incline push-ups")

    def test_tendon_spot_lookup_and_zoom_regions(self):
        for word, expected in (
            ("finger pulley", "finger pulleys"),
            ("a2 pulley", "finger pulleys"),
            ("golfer's elbow", "medial elbow"),
            ("tennis elbow", "lateral elbow"),
            ("rotator cuff", "shoulder tendon"),
            ("achilles", "achilles tendon"),
            ("wrist tendon", "wrist tendons"),
        ):
            inf = catalog.spot_info(word)
            self.assertIsNotNone(inf, word)
            self.assertEqual(inf["name"], expected, word)
            self.assertEqual(inf["kind"], "tendon", word)
        self.assertEqual(musclemap.region_at(11.0, 41.0)[0], "hand")
        self.assertIn(("finger pulleys", "Finger pulleys (A2/A4)", "tendon"), musclemap.region_items("hand"))
        self.assertIn(("medial elbow", "Inner elbow (golfer's elbow)", "tendon"), musclemap.region_items("arm"))

    def test_custom_exercise_registers_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.json"
            entry = catalog.register_custom("weighted ring dips", path=path)
            self.assertIn("chest", entry["muscles"])
            self.assertTrue(path.exists())
            catalog.CUSTOM.clear()
            loaded = catalog.load_custom(path)
            self.assertIn("weighted ring dips", loaded)


class TestDayScheduleAndRecovery(unittest.TestCase):
    def cfg(self, **over):
        base = {
            "gear": ["chair", "table"],
            "goals": ["planche", "front_lever", "core", "climbing", "physique"],
            "split": dict(schedule.DEFAULT_SPLIT),
            "exercises": list(catalog.DEFAULT_EXERCISES),
        }
        base.update(over)
        return base

    def test_day_split_selects_focus_moves_and_respects_home_gear(self):
        mon = dt.datetime(2026, 3, 2, 14, 0).timestamp()
        dp = schedule.day_plan(self.cfg(), [], now=mon, day_of=day_of)
        self.assertEqual(dp["focus"], "push")
        self.assertFalse(dp["rest"])
        self.assertIn("planche lean", dp["exercises"])
        self.assertNotIn("assisted pull-ups", dp["exercises"])

        tue = dt.datetime(2026, 3, 3, 14, 0).timestamp()
        dp_core = schedule.day_plan(self.cfg(), [], now=tue, day_of=day_of)
        self.assertEqual(dp_core["focus"], "core")
        self.assertIn("lying leg raises", dp_core["exercises"])

        wed = dt.datetime(2026, 3, 4, 14, 0).timestamp()
        dp_pull = schedule.day_plan(self.cfg(), [], now=wed, day_of=day_of)
        self.assertEqual(dp_pull["focus"], "pull")
        self.assertIn("inverted rows", dp_pull["exercises"])
        self.assertNotIn("assisted pull-ups", dp_pull["exercises"])

        sun = dt.datetime(2026, 3, 8, 14, 0).timestamp()
        dp_sun = schedule.day_plan(self.cfg(), [], now=sun, day_of=day_of)
        self.assertTrue(dp_sun["rest"])

    def test_day_after_hard_bouldering_never_demands_pull_and_is_gentle_or_rest(self):
        tue_evening = dt.datetime(2026, 3, 3, 19, 0).timestamp()
        wed_noon = dt.datetime(2026, 3, 4, 12, 0).timestamp()
        heavy_boulder = [{"ts": tue_evening, "result": "session", "activity": "bouldering", "minutes": 120, "effort": "hard"}]
        dp = schedule.day_plan(self.cfg(), heavy_boulder, now=wed_noon, day_of=day_of)
        self.assertTrue(dp["rest"] or dp["gentle"])
        self.assertNotIn("inverted rows", dp["exercises"])
        self.assertNotIn("tuck front lever", dp["exercises"])

        moderate_boulder = [{"ts": tue_evening, "result": "session", "activity": "bouldering", "minutes": 75, "effort": "normal"}]
        dp2 = schedule.day_plan(self.cfg(), moderate_boulder, now=wed_noon, day_of=day_of)
        self.assertTrue(dp2["gentle"])
        self.assertNotEqual(dp2["focus"], "pull")
        self.assertNotIn("inverted rows", dp2["exercises"])

    def test_weighted_sets_load_recovery_harder_than_light_bodyweight(self):
        now = dt.datetime(2026, 3, 4, 14, 0).timestamp()
        yesterday = now - 20 * 3600
        bw = [{"ts": yesterday, "result": "manual", "exercise": "push-ups", "did": 12}] * 3
        weighted = [{"ts": yesterday, "result": "manual", "exercise": "bench press", "did": 10, "weight_kg": 70}] * 4
        self.assertGreater(catalog.loads(weighted, now)["push"], catalog.loads(bw, now)["push"] * 2)

    def test_tendon_pain_strictly_blocks_loading_moves_for_multi_day_recovery(self):
        mon = dt.datetime(2026, 3, 2, 10, 0).timestamp()
        wed = dt.datetime(2026, 3, 4, 14, 0).timestamp()
        entries = [{"ts": mon, "result": "hurt", "part": "pull", "spots": [["finger pulleys", "left", "tendon"]]}]
        dp = schedule.day_plan(self.cfg(), entries, now=wed, day_of=day_of)
        self.assertTrue(dp["hurting_tendons"])
        self.assertNotIn("tuck front lever", dp["exercises"])
        self.assertNotIn("inverted rows", dp["exercises"])

        healed = entries + [{"ts": wed - 3600, "result": "healed", "part": "pull", "spots": [["finger pulleys", "left", "tendon"]]}]
        dp_healed = schedule.day_plan(self.cfg(), healed, now=wed, day_of=day_of)
        self.assertEqual(dp_healed["hurting_tendons"], [])
        self.assertIn("inverted rows", dp_healed["exercises"])

    def test_rest_day_flag_turns_day_into_calm_rest(self):
        mon = dt.datetime(2026, 3, 2, 14, 0).timestamp()
        entries = [{"ts": mon - 600, "result": "rest"}]
        dp = schedule.day_plan(self.cfg(), entries, now=mon, day_of=day_of)
        self.assertTrue(dp["rest"])


class TestIsometricFigures(unittest.TestCase):
    def test_all_moves_pass_ik_and_slide_checks(self):
        self.assertEqual(figure.check(), [])

    def test_wide_and_diamond_pushups_have_distinct_3d_hand_width(self):
        w_sk = figure.skeleton_3d(figure.MOVES["wide push-ups"].at(0))
        d_sk = figure.skeleton_3d(figure.MOVES["diamond push-ups"].at(0))
        wide_span = abs(w_sk["limbs"][0]["arm"][2][2] - w_sk["limbs"][1]["arm"][2][2])
        diam_span = abs(d_sk["limbs"][0]["arm"][2][2] - d_sk["limbs"][1]["arm"][2][2])
        self.assertGreater(wide_span, diam_span * 5)


class TestHubWorkoutRework(ModuleCase):
    def test_log_custom_exercise_with_weight_and_tendon_pain_and_config(self):
        said = modules.call("workout_log", {
            "kind": "sets",
            "exercise": "incline dumbbell flyes",
            "reps": [12, 12, 10],
            "weight_kg": 16,
            "muscles": {"chest": 1, "shoulders": 0.5},
        })
        self.assertIn("logged 3 sets of incline dumbbell flyes", said)
        self.assertIn("16 kg", said)
        self.assertIn("added to your exercise library", said)

        hurt_said = modules.call("workout_log", {"kind": "hurt", "where": "left finger pulley"})
        self.assertIn("finger pulleys", hurt_said)
        self.assertIn("(tendon)", hurt_said)

        rest_said = modules.call("workout_log", {"kind": "rest"})
        self.assertIn("rest day", rest_said)

        cfg_said = modules.call("workout_config", {
            "split": {"tue": "climbing", "fri": "climbing"},
            "goals": ["planche", "front_lever", "core", "climbing"],
            "gear": ["chair", "table"],
        })
        self.assertIn("tue=climbing", cfg_said)
        self.assertIn("planche", cfg_said)

        lines = workout.inbox_lines()
        kinds = [e.get("result") for e in lines]
        self.assertIn("manual", kinds)
        self.assertIn("hurt", kinds)
        self.assertIn("rest", kinds)
        self.assertIn("config", kinds)


if __name__ == "__main__":
    unittest.main()
