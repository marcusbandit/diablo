import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import db, devices, mcp_server, server

TOKEN = "master-token-for-tests-0123456789abcdef"
HEADERS = {"X-Diablo-Token": TOKEN}


class PairCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        devices._failures.clear()
        devices._touched.clear()
        devices._admitted.clear()
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def mint(self, headers=HEADERS):
        response = self.client.post("/v1/pair/code", headers=headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def pair(self, code, name="Pixel 10 Pro Fold"):
        return self.client.post("/v1/pair", json={"code": code, "name": name})


class TestMint(PairCase):
    def test_minting_needs_a_token(self):
        self.assertEqual(self.client.post("/v1/pair/code").status_code, 403)

    def test_code_is_readable_and_links_carry_it(self):
        minted = self.mint()
        code = minted["code"].replace("-", "")
        self.assertRegex(minted["code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertFalse(set(code) & set("01OIL"))
        self.assertIn(f"code={code}", minted["url"])
        self.assertTrue(minted["link"].startswith("diablo://pair?"))
        self.assertIn(f"code={code}", minted["link"])
        self.assertAlmostEqual(minted["expires_at"], time.time() + devices.CODE_TTL_S, delta=5)

    def test_codes_are_stored_hashed(self):
        code = self.mint()["code"].replace("-", "")
        with db._lock:
            rows = db._conn.execute("SELECT code_hash FROM pair_codes").fetchall()
        self.assertNotIn(code, [r["code_hash"] for r in rows])


class TestRedeem(PairCase):
    def test_a_code_buys_a_working_device_token(self):
        minted = self.mint()
        response = self.pair(minted["code"].lower())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(len(body["token"]), 40)
        self.assertNotEqual(body["token"], TOKEN)
        me = self.client.get("/v1/whoami", headers={"X-Diablo-Token": body["token"]})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["device"]["name"], "Pixel 10 Pro Fold")
        self.assertFalse(me.json()["master"])
        self.assertEqual(self.client.get("/v1/sections", headers={"X-Diablo-Token": body["token"]}).status_code, 200)

    def test_device_token_works_on_the_event_stream_query(self):
        token = self.pair(self.mint()["code"]).json()["token"]
        self.assertIsNotNone(devices.authorized(token))
        self.assertIsNone(devices.authorized(token + "x"))

    def test_device_token_works_as_bearer(self):
        token = self.pair(self.mint()["code"]).json()["token"]
        with mock.patch.object(server.agent, "run_agent", return_value=("Done, sir.", [])):
            db.set_setting("brain.backend", "api")
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": "diablo", "messages": [{"role": "user", "content": "hi"}]},
            )
        self.assertEqual(response.status_code, 200)

    def test_a_code_works_once(self):
        code = self.mint()["code"]
        self.assertEqual(self.pair(code).status_code, 200)
        self.assertEqual(self.pair(code).status_code, 403)

    def test_an_expired_code_is_refused(self):
        code = self.mint()["code"]
        with mock.patch.object(devices.db.time, "time", return_value=time.time() + devices.CODE_TTL_S + 1):
            self.assertEqual(self.pair(code).status_code, 403)

    def test_status_follows_the_code(self):
        code = self.mint()["code"]
        status = self.client.get("/v1/pair/status", params={"code": code}, headers=HEADERS).json()
        self.assertEqual(status["state"], "waiting")
        self.pair(code, "browser")
        status = self.client.get("/v1/pair/status", params={"code": code}, headers=HEADERS).json()
        self.assertEqual(status, {"state": "paired", "device": "browser"})

    def test_too_many_wrong_codes_lock_out_that_client_only(self):
        code = self.mint()["code"]
        for _ in range(devices.MAX_FAILURES):
            self.pair("ZZZZZZZZ")
        self.assertEqual(self.pair(code).status_code, 429)
        self.assertEqual(devices.redeem(code, "other", client="100.1.2.3")[2], "other")

    def test_wrong_codes_from_everywhere_burn_the_open_ones(self):
        code = self.mint()["code"]
        with mock.patch.object(devices, "GLOBAL_FAILURES", 5):
            for i in range(5):
                devices.redeem("ZZZZZZZZ", "x", client=f"100.0.0.{i}")
        self.assertEqual(self.pair(code).status_code, 403)

    def test_the_stored_name_is_the_one_returned(self):
        body = self.pair(self.mint()["code"], "  Pixel   10 " + "x" * 80).json()
        self.assertEqual(body["device"]["name"], ("Pixel 10 " + "x" * 80)[:60])


class TestDevices(PairCase):
    def test_list_marks_the_caller_and_revoke_locks_it_out(self):
        first = self.pair(self.mint()["code"], "phone").json()
        second = self.pair(self.mint()["code"], "browser").json()
        listed = self.client.get("/v1/devices", headers={"X-Diablo-Token": first["token"]}).json()["devices"]
        self.assertEqual([d["name"] for d in listed], ["phone", "browser"])
        self.assertEqual([d["current"] for d in listed], [True, False])
        self.assertNotIn("token_hash", listed[0])
        response = self.client.delete(f"/v1/devices/{second['device']['id']}", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/v1/sections", headers={"X-Diablo-Token": second["token"]}).status_code, 403)
        self.assertEqual(self.client.get("/v1/sections", headers={"X-Diablo-Token": first["token"]}).status_code, 200)
        self.assertEqual(self.client.delete(f"/v1/devices/{second['device']['id']}", headers=HEADERS).status_code, 404)

    def test_revoking_a_device_revokes_what_it_paired(self):
        browser = self.pair(self.mint()["code"], "browser").json()
        minted = self.mint({"X-Diablo-Token": browser["token"]})
        phone = self.pair(minted["code"], "phone").json()
        other = self.pair(self.mint()["code"], "laptop").json()
        self.assertEqual(devices.revoke(browser["device"]["id"]), [browser["device"]["id"], phone["device"]["id"]])
        self.assertIsNone(devices.authorized(phone["token"]))
        self.assertIsNotNone(devices.authorized(other["token"]))

    def test_a_revoked_device_loses_its_open_event_stream(self):
        import asyncio

        token = self.pair(self.mint()["code"]).json()["token"]

        async def run():
            response = await server.event_stream(token=token, x_diablo_token=None)
            stream = response.body_iterator
            first = await stream.__anext__()
            devices.revoke(1)
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(stream.__anext__(), 2)
            return first

        self.assertTrue(asyncio.run(run()).startswith("retry"))

    def test_placeholder_master_token_is_refused(self):
        with mock.patch.object(devices, "get_config", return_value={"hub_token": "CHANGE_ME"}):
            self.assertEqual(self.client.get("/v1/whoami", headers={"X-Diablo-Token": "CHANGE_ME"}).status_code, 403)

    def test_master_token_still_works(self):
        me = self.client.get("/v1/whoami", headers=HEADERS).json()
        self.assertTrue(me["master"])
        self.assertIsNone(me["device"])


class TestMcpTools(PairCase):
    def call(self, name, arguments=None):
        return mcp_server.handle({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})

    def test_brain_can_pair_list_and_revoke(self):
        names = [tool["name"] for tool in mcp_server.tools()]
        for wanted in ("pair_device", "list_devices", "revoke_device"):
            self.assertIn(wanted, names)
        text = self.call("pair_device")["content"][0]["text"]
        code = text.split("Pairing code ", 1)[1].split(",", 1)[0]
        token = self.pair(code, "browser").json()["token"]
        self.assertIn("1. browser", self.call("list_devices")["content"][0]["text"])
        self.assertIn("locked out", self.call("revoke_device", {"id": 1})["content"][0]["text"])
        self.assertIsNone(devices.authorized(token))



class TestTailnetDoor(PairCase):
    OWNER = "marcus@example.com"

    def door(self, proxies=("testclient",), owners=(OWNER,)):
        config = {"hub_token": TOKEN, "door": {"proxies": list(proxies), "owners": list(owners)}}
        for target in (server, devices):
            patch = mock.patch.object(target, "get_config", return_value=config)
            patch.start()
            self.addCleanup(patch.stop)

    def knock(self, login=OWNER, name="Firefox on banditbox"):
        headers = {"Tailscale-User-Login": login} if login else {}
        return self.client.post("/v1/pair/tailnet", json={"name": name}, headers=headers)

    def test_owner_through_the_door_gets_a_working_token(self):
        self.door()
        response = self.knock()
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]
        me = self.client.get("/v1/whoami", headers={"X-Diablo-Token": token})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(response.json()["device"]["name"], "Firefox on banditbox")

    def test_login_is_matched_without_case(self):
        self.door()
        self.assertEqual(self.knock(login="Marcus@Example.com").status_code, 200)

    def test_someone_else_on_the_tailnet_is_refused(self):
        self.door()
        self.assertEqual(self.knock(login="kristina@example.com").status_code, 403)

    def test_no_identity_header_is_refused(self):
        self.door()
        self.assertEqual(self.knock(login=None).status_code, 403)

    def test_header_from_an_untrusted_address_is_refused(self):
        self.door(proxies=("100.64.0.9",))
        self.assertEqual(self.knock().status_code, 403)

    def test_door_is_shut_when_not_configured(self):
        self.assertEqual(self.knock().status_code, 403)

    def test_admissions_are_capped_per_hour(self):
        self.door()
        devices._admitted.clear()
        self.addCleanup(devices._admitted.clear)
        for _ in range(devices.DOOR_PER_HOUR):
            self.assertEqual(self.knock().status_code, 200)
        self.assertEqual(self.knock().status_code, 429)

    def test_root_opens_the_web_app(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertIn(response.status_code, (302, 307))
        self.assertEqual(response.headers["location"], "/app/")


if __name__ == "__main__":
    unittest.main()
