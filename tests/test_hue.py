import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import db, hue

BRIDGE = {"hue_bridge_ip": "10.0.0.2", "hue_app_key": "key", "hue_lamps": json.dumps({"up": "3", "right": "4", "left": "5"})}

LIGHTS = {
    "3": {"state": {"on": True, "bri": 254, "xy": [0.6, 0.3], "ct": 300},
          "capabilities": {"control": {"colorgamut": [[0.69, 0.30], [0.17, 0.70], [0.15, 0.06]], "ct": {"min": 153, "max": 500}}}},
    "4": {"state": {"on": False, "bri": 100, "xy": [0.3, 0.3], "ct": 400},
          "capabilities": {"control": {"colorgamut": [[0.69, 0.30], [0.17, 0.70], [0.15, 0.06]], "ct": {"min": 153, "max": 500}}}},
    "5": {"state": {"on": True, "bri": 200, "ct": 370},
          "capabilities": {"control": {"ct": {"min": 222, "max": 454}}}},
}

SCENES = {
    "sRelax": {"name": "Relax", "type": "GroupScene", "group": "2", "lights": ["3", "4", "5"],
               "lightstates": {"3": {"on": True, "xy": [0.6, 0.3]}, "5": {"on": True, "ct": 454}}},
    "sRead": {"name": "Read", "type": "GroupScene", "group": "2", "lights": ["3", "5"]},
    "sOther": {"name": "Hallway", "type": "GroupScene", "group": "7", "lights": ["9"]},
    "sLight": {"name": "Single", "type": "LightScene", "lights": ["3"]},
}


class Reply:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class HueCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for key, value in BRIDGE.items():
            db.set_setting(key, value)


class TestLampStates(HueCase):
    def test_every_lamp_comes_from_one_read_with_its_caps(self):
        with mock.patch.object(hue.httpx, "get", return_value=Reply(LIGHTS)) as get:
            states = hue.lamp_states()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], "http://10.0.0.2/api/key/lights")
        by_id = {s["id"]: s for s in states}
        self.assertEqual(list(by_id), ["left", "up", "right"])
        self.assertEqual(by_id["left"]["caps"], {"color": False, "ct": [2203, 4505]})
        self.assertEqual(by_id["left"]["ct"], 2703)
        self.assertEqual(by_id["up"]["caps"], {"color": True, "ct": [2000, 6536]})
        self.assertTrue(by_id["up"]["on"])
        self.assertFalse(by_id["right"]["on"])

    def test_an_unreachable_bridge_gives_no_lamps(self):
        with mock.patch.object(hue.httpx, "get", side_effect=hue.httpx.ConnectError("down")):
            self.assertEqual(hue.lamp_states(), [])


class TestScenes(HueCase):
    def test_only_the_rooms_group_scenes_are_listed_with_colours_when_known(self):
        with mock.patch.object(hue.httpx, "get", return_value=Reply(SCENES)):
            listed = hue.scenes()
        self.assertEqual([s["name"] for s in listed], ["Read", "Relax", "Single"])
        relax = next(s for s in listed if s["id"] == "sRelax")
        self.assertEqual(len(relax["colors"]), 2)
        self.assertTrue(all(c.startswith("#") and len(c) == 7 for c in relax["colors"]))
        read = next(s for s in listed if s["id"] == "sRead")
        self.assertNotIn("colors", read)
        self.assertEqual((read["group"], relax["group"]), ("2", "2"))

    def test_recalling_a_scene_puts_it_on_its_group(self):
        with mock.patch.object(hue.httpx, "get", return_value=Reply(SCENES)), \
                mock.patch.object(hue.httpx, "put", return_value=Reply([{"success": {"/groups/2/action/scene": "sRelax"}}])) as put:
            result = hue.recall_scene("sRelax")
        self.assertEqual(result, "Relax: scene on")
        self.assertEqual(put.call_args.args[0], "http://10.0.0.2/api/key/groups/2/action")
        self.assertEqual(put.call_args.kwargs["json"], {"scene": "sRelax"})

    def test_a_scene_without_a_group_is_listed_and_recalled_through_group_zero(self):
        with mock.patch.object(hue.httpx, "get", return_value=Reply(SCENES)), \
                mock.patch.object(hue.httpx, "put", return_value=Reply([{"success": {}}])) as put:
            listed = hue.scenes()
            self.assertIn("sLight", [s["id"] for s in listed])
            self.assertEqual(hue.recall_scene("sLight"), "Single: scene on")
        self.assertEqual(put.call_args.args[0], "http://10.0.0.2/api/key/groups/0/action")

    def test_a_known_group_and_name_skip_the_scene_read(self):
        with mock.patch.object(hue.httpx, "get") as get, \
                mock.patch.object(hue.httpx, "put", return_value=Reply([{"success": {}}])) as put:
            self.assertEqual(hue.recall_scene("sRelax", "2", "Relax"), "Relax: scene on")
        get.assert_not_called()
        self.assertEqual(put.call_args.args[0], "http://10.0.0.2/api/key/groups/2/action")

    def test_a_refusal_an_unknown_id_and_a_dead_bridge_each_say_so(self):
        with mock.patch.object(hue.httpx, "get", return_value=Reply(SCENES)):
            with mock.patch.object(hue.httpx, "put", return_value=Reply([{"error": {"type": 201}}])):
                self.assertEqual(hue.recall_scene("sRelax"), "Relax: refused")
            self.assertEqual(hue.recall_scene("nope"), "unknown scene nope")
        with mock.patch.object(hue.httpx, "get", side_effect=hue.httpx.ConnectError("down")):
            self.assertIn("did not answer", hue.recall_scene("sRelax"))

    def test_without_a_bridge_there_are_no_scenes(self):
        db.set_setting("hue_bridge_ip", "")
        self.assertEqual(hue.scenes(), [])
        self.assertIn("no hue bridge", hue.recall_scene("sRelax"))
