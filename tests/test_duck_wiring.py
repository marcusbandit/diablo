"""The roads to the conversation duck: the hub's keep_sound_up tool and route, the desktop's
busy.py handing the `duck` event to the ears, and the ears' control panel."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import charter, lockguard, mcp_server, server
from tests.test_hub_api import HEADERS, ApiCase
from voice import duck, ears_api

_spec = importlib.util.spec_from_file_location("presence_busy_duck", Path(__file__).resolve().parent.parent / "presence" / "busy.py")
busy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(busy)


class TestHubRoute(ApiCase):
    def test_keep_up_goes_out_as_a_duck_event(self):
        for keep in (True, False):
            with mock.patch.object(server, "publish") as published:
                reply = self.client.post("/v1/ears/duck", headers=HEADERS, json={"keep_up": keep})
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(published.call_args.args[0], {"kind": "duck", "keep_up": keep})

    def test_only_the_hub_token_may(self):
        self.assertEqual(self.client.post("/v1/ears/duck", json={"keep_up": True}).status_code, 403)


class TestTool(unittest.TestCase):
    def test_listed_allowed_while_locked_and_in_the_charter(self):
        self.assertIn("keep_sound_up", [t["name"] for t in mcp_server.EXTRA])
        self.assertIn("keep_sound_up", lockguard.SAFE_HOUSE)
        self.assertIn("keep_sound_up", charter.CHARTER)

    def test_it_posts_keep_up(self):
        reply = mock.Mock(status_code=200)
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": "t"}), \
                mock.patch.object(mcp_server.httpx, "post", return_value=reply) as post:
            self.assertIn("stays up", mcp_server.call("keep_sound_up", {}))
            self.assertEqual(post.call_args.kwargs["json"], {"keep_up": True})
            self.assertTrue(post.call_args.args[0].endswith("/v1/ears/duck"))
            self.assertIn("turned down again", mcp_server.call("keep_sound_up", {"on": False}))
            self.assertEqual(post.call_args.kwargs["json"], {"keep_up": False})


class TestBusyHandsItOn(unittest.TestCase):
    def test_the_event_reaches_the_ears_with_their_header(self):
        sent = []

        class Reply:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            sent.append(request)
            return Reply()

        self.assertTrue(busy.duck({"kind": "duck", "keep_up": True}, opener=opener))
        request = sent[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8758/v1/ears/duck")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-diablo-ears"), "1")
        self.assertEqual(json.loads(request.data), {"keep_up": True})


class TestControlPanel(unittest.TestCase):
    def setUp(self):
        self.fake = mock.Mock()
        self.fake.status.return_value = {"on": True, "level": 20}
        self.patch = mock.patch.object(duck, "DUCK", self.fake)
        self.patch.start()
        self.controller = ears_api.Controller(ears_api.Live(), None, lambda: True, lambda on: None,
                                              lambda: 0.0, lambda: 0.0)

    def tearDown(self):
        self.patch.stop()

    def test_settings_and_keep_up(self):
        code, body = self.controller.duck({"on": False, "level": 15})
        self.assertEqual(code, 200)
        self.fake.configure.assert_called_once_with({"on": False, "level": 15})
        self.fake.suspend.assert_not_called()
        self.controller.duck({"keep_up": True})
        self.fake.suspend.assert_called_once_with(True)

    def test_a_bad_level_is_refused(self):
        self.assertEqual(self.controller.duck({"level": "loud"})[0], 400)


if __name__ == "__main__":
    unittest.main()


class TestEars(unittest.TestCase):
    def test_saying_dont_turn_it_down_keeps_it_up(self):
        from voice import ears, timeline

        with mock.patch.object(duck, "DUCK") as fake, mock.patch.object(ears.feedback, "done"):
            words = ears.local_line(None, mock.Mock(get=mock.Mock(return_value={})), None,
                                    "Don't turn it down.", timeline.Timeline())
        self.assertEqual(words, "I'll leave the sound up.")
        fake.suspend.assert_called_once_with(True)

    def test_the_state_drives_the_duck(self):
        from voice import ears

        seen = []
        with mock.patch.object(duck, "DUCK") as fake, mock.patch.object(ears, "STATE_FILE", Path(tempfile.mkdtemp()) / "ears.json"), \
                mock.patch.object(ears, "STATE_DIR", Path(tempfile.mkdtemp())), mock.patch.object(ears, "REPORT", None), \
                mock.patch.object(ears, "LIVE", None), mock.patch.dict(ears.STICKY, {"loop": False, "dictation": False}):
            fake.update.side_effect = seen.append
            for state in ("waking", "listening", "thinking", "speaking", "asleep"):
                ears.set_state(state)
            ears.set_state("asleep", loop=True)
        self.assertEqual(seen, [False, True, True, True, False, True])
