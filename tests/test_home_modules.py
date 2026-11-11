import datetime as dt
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from hub import db, modules
from hub.modules import lights, machines, network, reminders

NOW = 1794199590.0


def iso(seconds_ago):
    return dt.datetime.fromtimestamp(NOW - seconds_ago, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0Z")


def peer(name, os_name, online, addr="", relay="fra", seen="0001-01-01T00:00:00Z", active=False):
    return {"HostName": name, "DNSName": f"{name}.tail.ts.net.", "OS": os_name, "Online": online,
            "CurAddr": addr, "Relay": relay, "LastSeen": seen, "Active": active, "Addrs": None}


def tailnet(**overrides):
    data = {
        "Self": peer("diablo-pi", "linux", True),
        "Peer": {
            "k1": peer("0,1x Engineer", "android", True, "192.168.1.238:42886"),
            "k2": peer("banditbox", "linux", True, "192.168.1.254:41641"),
            "k3": peer("nemurenai", "linux", True, "203.0.113.9:41641"),
            "k4": peer("homehub", "linux", True, "", "fra", active=True),
            "k5": peer("kangaeru", "linux", False, "", "ams", iso(3 * 3600)),
            "k6": peer("computer", "linux", False, "", "ams", iso(3 * 86400)),
            "k7": peer("old-ipad", "iOS", False),
        },
    }
    data.update(overrides)
    return data


def ran(data=None, code=0):
    return types.SimpleNamespace(returncode=code, stdout=json.dumps(data if data is not None else tailnet()))


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        network._cache.clear()
        machines._cache.clear()
        network._home_seen[0] = 0.0
        patch = mock.patch.object(network.time, "time", return_value=NOW)
        self.clock = patch.start()
        self.addCleanup(patch.stop)

    def panel(self, data=None, **kwargs):
        with mock.patch.object(network.subprocess, "run", return_value=ran(data), **kwargs):
            return network.panel({})


class TestNetwork(HomeCase):
    def test_a_phone_on_the_home_subnet_means_home(self):
        stats, devices = self.panel()
        self.assertEqual(stats["kind"], "stats")
        self.assertEqual(stats["items"], [{"label": "Marcus", "value": "Home", "icon": "home"}])
        self.assertEqual(stats["caption"], "Phone is on the home network")
        self.assertEqual(devices["kind"], "devices")

    def test_a_phone_on_the_tailnet_from_elsewhere_means_away(self):
        data = tailnet()
        data["Peer"]["k1"]["CurAddr"] = "198.51.100.7:5555"
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Away")

    def test_an_idle_online_phone_is_unknown_when_home_was_never_seen(self):
        data = tailnet()
        data["Peer"]["k1"]["CurAddr"] = ""
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Unknown")
        self.assertIn("idle", stats["caption"])

    def test_an_idle_online_phone_stays_home_for_a_few_hours_after_it_was_seen_there(self):
        self.panel()
        data = tailnet()
        data["Peer"]["k1"]["CurAddr"] = ""
        network._cache.clear()
        self.clock.return_value = NOW + 3600
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Home")
        self.assertEqual(stats["caption"], "Phone was on the home network 1 h ago")
        network._cache.clear()
        self.clock.return_value = NOW + 5 * 3600
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Unknown")

    def test_a_home_subnet_endpoint_in_the_address_list_means_home(self):
        data = tailnet()
        data["Peer"]["k1"]["CurAddr"] = ""
        data["Peer"]["k1"]["Addrs"] = ["198.51.100.7:5555", "192.168.1.238:42886"]
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Home")

    def test_away_needs_a_direct_address_that_is_not_home_even_after_a_home_sighting(self):
        self.panel()
        data = tailnet()
        data["Peer"]["k1"]["CurAddr"] = "198.51.100.7:5555"
        network._cache.clear()
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Away")

    def test_an_offline_phone_says_when_it_was_last_seen_and_stays_unknown(self):
        data = tailnet()
        data["Peer"]["k1"].update(Online=False, CurAddr="", LastSeen=iso(3 * 3600))
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Unknown")
        self.assertEqual(stats["caption"], "Phone last seen 3 h ago")

    def test_no_phone_is_unknown(self):
        data = tailnet()
        del data["Peer"]["k1"]
        del data["Peer"]["k7"]
        stats, _ = self.panel(data)
        self.assertEqual(stats["items"][0]["value"], "Unknown")

    def test_the_home_subnet_is_a_setting(self):
        db.set_setting("network.home_subnet", "10.1.0.0/16")
        stats, _ = self.panel()
        self.assertEqual(stats["items"][0]["value"], "Away")

    def test_devices_say_what_they_are_how_they_connect_and_when_they_were_seen(self):
        _, block = self.panel()
        rows = {row["name"]: row for row in block["items"]}
        self.assertEqual(rows["diablo-pi"], {"name": "diablo-pi", "kind": "server", "online": True, "never_seen": False, "how": "", "last_seen": "now", "os": "Linux"})
        self.assertEqual((rows["0,1x Engineer"]["kind"], rows["0,1x Engineer"]["how"], rows["0,1x Engineer"]["os"]), ("phone", "home network", "Android"))
        self.assertEqual((rows["banditbox"]["kind"], rows["banditbox"]["how"]), ("desktop", "home network"))
        self.assertEqual(rows["nemurenai"]["how"], "direct")
        self.assertEqual(rows["homehub"]["how"], "relayed")
        self.assertEqual(rows["old-ipad"]["how"], "")
        self.assertEqual((rows["kangaeru"]["kind"], rows["kangaeru"]["online"], rows["kangaeru"]["how"], rows["kangaeru"]["last_seen"]), ("laptop", False, "", "3 h ago"))
        self.assertEqual(rows["computer"]["last_seen"], "3 days ago")
        self.assertEqual((rows["old-ipad"]["kind"], rows["old-ipad"]["last_seen"]), ("phone", "not seen yet"))

    def test_an_idle_peer_with_no_direct_address_is_not_called_relayed(self):
        data = tailnet()
        data["Peer"]["k9"] = peer("quiet", "linux", True, "", "fra", active=False)
        rows = {row["name"]: row for row in self.panel(data)[1]["items"]}
        self.assertEqual(rows["quiet"]["how"], "")

    def test_online_devices_come_first_then_by_name(self):
        _, block = self.panel()
        names = [row["name"] for row in block["items"]]
        online = [row["online"] for row in block["items"]]
        self.assertEqual(online, sorted(online, reverse=True))
        self.assertEqual(names[:2], ["0,1x Engineer", "banditbox"])

    def test_an_unknown_machine_is_other_and_a_setting_can_name_it(self):
        data = tailnet()
        data["Peer"]["k9"] = peer("printer", "linux", True, "192.168.1.40:1")
        rows = {row["name"]: row for row in self.panel(data)[1]["items"]}
        self.assertEqual(rows["printer"]["kind"], "other")
        db.set_setting("network.kinds", "printer=server")
        network._cache.clear()
        rows = {row["name"]: row for row in self.panel(data)[1]["items"]}
        self.assertEqual(rows["printer"]["kind"], "server")

    def test_a_named_phone_that_is_gone_is_not_swapped_for_another_phone(self):
        db.set_setting("network.phone", "Pixel Fold")
        stats, _ = self.panel()
        self.assertEqual(stats["items"][0]["value"], "Unknown")
        self.assertEqual(stats["caption"], "No phone named Pixel Fold on the tailnet")
        db.set_setting("network.phone", "0,1x engineer")
        network._cache.clear()
        stats, _ = self.panel()
        self.assertEqual(stats["items"][0]["value"], "Home")

    def test_the_kinds_setting_overrides_the_os_guess(self):
        data = tailnet()
        data["Peer"]["k9"] = peer("mini", "macOS", True, "192.168.1.50:1")
        data["Peer"]["k10"] = peer("tvbox", "android", True, "192.168.1.51:1")
        before = {row["name"]: row["kind"] for row in self.panel(data)[1]["items"]}
        self.assertEqual((before["mini"], before["tvbox"]), ("laptop", "phone"))
        db.set_setting("network.kinds", "mini=server, tvbox=other")
        network._cache.clear()
        after = {row["name"]: row["kind"] for row in self.panel(data)[1]["items"]}
        self.assertEqual((after["mini"], after["tvbox"]), ("server", "other"))

    def test_a_peer_with_null_names_does_not_break_the_panel(self):
        data = tailnet()
        data["Peer"]["k9"] = {"HostName": None, "DNSName": None, "OS": "linux", "Online": False, "CurAddr": None, "LastSeen": None}
        data["Peer"]["k10"] = {"HostName": "", "DNSName": "label.tail.ts.net.", "OS": None, "Online": True}
        rows = {row["name"]: row for row in self.panel(data)[1]["items"]}
        self.assertEqual(rows["unknown"]["last_seen"], "not seen yet")
        self.assertEqual((rows["label"]["kind"], rows["label"]["os"]), ("other", ""))

    def test_ago_speaks_plainly(self):
        self.assertEqual([network.ago(s) for s in (5, 120, 7200, 90000, 400000)],
                         ["just now", "2 min ago", "2 h ago", "yesterday", "4 days ago"])

    def test_a_missing_slow_or_failing_tailscale_is_a_notice(self):
        for failure in (
            {"side_effect": FileNotFoundError("tailscale")},
            {"side_effect": subprocess.TimeoutExpired("tailscale", 5)},
        ):
            network._cache.clear()
            with mock.patch.object(network.subprocess, "run", **failure):
                blocks = network.panel({})
            self.assertEqual([b["kind"] for b in blocks], ["notice"])
        network._cache.clear()
        with mock.patch.object(network.subprocess, "run", return_value=ran(code=1)):
            self.assertEqual(network.panel({})[0]["kind"], "notice")
        network._cache.clear()
        with mock.patch.object(network.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout="not json")):
            self.assertEqual(network.panel({})[0]["kind"], "notice")

    def test_status_is_read_with_a_timeout_and_cached_for_a_while(self):
        with mock.patch.object(network.subprocess, "run", return_value=ran()) as run:
            network.panel({})
            network.panel({})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["tailscale", "status", "--json"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_a_failing_tailscale_costs_one_attempt_per_cache_period(self):
        with mock.patch.object(network.subprocess, "run", side_effect=subprocess.TimeoutExpired("tailscale", 5)) as run, \
                mock.patch("hub.modules.common.time.monotonic", side_effect=[100.0, 110.0, 120.0, 140.0]):
            for _ in range(3):
                self.assertEqual(network.panel({})[0]["kind"], "notice")
            self.assertEqual(run.call_count, 1)
            network.panel({})
            self.assertEqual(run.call_count, 2)

    def test_the_module_is_registered_and_tags_its_blocks(self):
        with mock.patch.object(network.subprocess, "run", return_value=ran()):
            blocks = modules.panel("network", {})
        self.assertEqual([b["module"] for b in blocks], ["network", "network"])


def host(name, **overrides):
    entry = {
        "name": name, "role": "server", "tried": 1794199565544, "reached": 1794199565544, "error": None,
        "system": {"uptime": 66516, "load": 7.19970703125, "cpus": 12, "mem_total": 33502818304,
                   "mem_avail": 17217511424, "disk_total": 511035379712, "disk_used": 219568934912},
    }
    entry.update(overrides)
    return entry


def fleet(*hosts):
    return {"at": 1794199590770, "every": 300000, "hosts": list(hosts)}


class TestMachines(HomeCase):
    def panel(self, data):
        with mock.patch.object(machines.httpx, "get") as get:
            get.return_value.json.return_value = data
            get.return_value.raise_for_status.return_value = None
            blocks = machines.panel({})
        return blocks, get

    def test_a_box_reports_uptime_load_memory_and_disk(self):
        full = dict(host("banditbox")["system"], disk_total=1999323095040, disk_used=1825296412672)
        blocks, get = self.panel(fleet(host("banditbox", system=full)))
        self.assertEqual(get.call_args.args[0], "http://nemurenai.tailnet.example.ts.net:8780/api/state")
        self.assertEqual(get.call_args.kwargs["timeout"], 5)
        self.assertEqual(blocks[0]["kind"], "machines")
        item = blocks[0]["items"][0]
        self.assertEqual(item, {
            "name": "banditbox", "online": True, "uptime": "18 h 28 min", "load": 7.2, "cpus": 12,
            "mem_used": 15.2, "mem_total": 31.2, "disk_used_pct": 91, "warnings": ["Disk is 91% full"]})

    def test_a_disk_under_the_line_has_no_warning(self):
        item = self.panel(fleet(host("a")))[0][0]["items"][0]
        self.assertEqual((item["disk_used_pct"], item["warnings"]), (43, []))

    def test_the_line_is_eighty_five_percent(self):
        for used, warned in ((84, False), (85, True)):
            system = dict(host("a")["system"], disk_used=used, disk_total=100)
            machines._cache.clear()
            item = self.panel(fleet(host("a", system=system)))[0][0]["items"][0]
            self.assertEqual(bool(item["warnings"]), warned, used)

    def test_a_box_gone_for_over_a_day_warns_and_drops_stale_numbers(self):
        gone = host("homehub", error="ssh: connect to host: Network is unreachable",
                    reached=1794199590770 - 23 * 86400 * 1000)
        item = self.panel(fleet(gone))[0][0]["items"][0]
        self.assertFalse(item["online"])
        self.assertEqual(item["warnings"], ["Unreachable for 23 days"])
        self.assertEqual((item["uptime"], item["load"], item["mem_used"], item["disk_used_pct"]), ("", None, None, None))
        self.assertEqual((item["cpus"], item["mem_total"]), (12, 31.2))

    def test_an_offline_box_does_not_warn_about_a_disk_reading_that_is_old(self):
        full = dict(host("a")["system"], disk_used=95, disk_total=100)
        item = self.panel(fleet(host("a", system=full, error="timeout", reached=1794199590770 - 3 * 86400 * 1000)))[0][0]["items"][0]
        self.assertEqual((item["disk_used_pct"], item["warnings"]), (None, ["Unreachable for 3 days"]))

    def test_a_box_out_for_less_than_a_day_is_offline_without_a_warning(self):
        recent = host("kangaeru", error="timeout", reached=1794199590770 - 3600 * 1000)
        item = self.panel(fleet(recent))[0][0]["items"][0]
        self.assertEqual((item["online"], item["warnings"]), (False, []))

    def test_a_box_the_fleet_stopped_probing_counts_as_offline(self):
        stale = host("kangaeru", tried=1794199590770 - 13 * 3600 * 1000, reached=1794199590770 - 13 * 3600 * 1000)
        item = self.panel(fleet(stale))[0][0]["items"][0]
        self.assertFalse(item["online"])

    def test_online_boxes_come_first(self):
        gone = host("aaa", error="x", reached=1794199590770 - 3600 * 1000)
        names = [i["name"] for i in self.panel(fleet(gone, host("zzz"), host("mmm")))[0][0]["items"]]
        self.assertEqual(names, ["mmm", "zzz", "aaa"])

    def test_the_url_is_a_setting(self):
        db.set_setting("fleet.url", "http://example.test:9/")
        _, get = self.panel(fleet(host("a")))
        self.assertEqual(get.call_args.args[0], "http://example.test:9/api/state")

    def test_the_state_is_cached_for_a_minute(self):
        with mock.patch.object(machines.httpx, "get") as get:
            get.return_value.json.return_value = fleet(host("a"))
            machines.panel({})
            machines.panel({})
        self.assertEqual(get.call_count, 1)

    def test_a_fleet_view_that_is_down_is_a_notice(self):
        with mock.patch.object(machines.httpx, "get", side_effect=httpx.ConnectError("down")):
            blocks = machines.panel({})
        self.assertEqual([b["kind"] for b in blocks], ["notice"])
        machines._cache.clear()
        with mock.patch.object(machines.httpx, "get") as get:
            get.return_value.json.side_effect = ValueError("bad")
            self.assertEqual(machines.panel({})[0]["kind"], "notice")

    def test_a_dead_fleet_view_costs_one_attempt_per_cache_period(self):
        with mock.patch.object(machines.httpx, "get", side_effect=httpx.ConnectError("down")) as get, \
                mock.patch("hub.modules.common.time.monotonic", side_effect=[100.0, 130.0, 150.0, 170.0]):
            for _ in range(3):
                self.assertEqual(machines.panel({})[0]["kind"], "notice")
            self.assertEqual(get.call_count, 1)
            machines.panel({})
            self.assertEqual(get.call_count, 2)

    def test_an_empty_fleet_is_a_notice(self):
        self.assertEqual(self.panel(fleet())[0][0]["kind"], "notice")

    def test_the_module_is_registered_and_tags_its_block(self):
        with mock.patch.object(machines.httpx, "get") as get:
            get.return_value.json.return_value = fleet(host("a"))
            blocks = modules.panel("machines", {})
        self.assertEqual(blocks[0]["module"], "machines")


class TestHomeFailures(HomeCase):
    def test_every_source_that_is_down_says_so_with_a_retry(self):
        with mock.patch.object(network.subprocess, "run", side_effect=OSError("no tailscale")):
            net = network.panel({})
        with mock.patch.object(machines.httpx, "get", side_effect=httpx.ConnectError("down")):
            fleet_down = machines.panel({})
        lights._cache.clear()
        with mock.patch.object(lights.hue, "lamp_states", return_value=[]), \
                mock.patch.object(lights.hue, "configured", return_value=True):
            bridge = lights.panel({})
        with mock.patch.object(reminders, "run", return_value={"ok": False, "error": "x"}):
            reminders._cache.clear()
            remind = reminders.panel({})
        for blocks in (net, fleet_down, bridge, remind):
            self.assertEqual(len(blocks), 1, blocks)
            self.assertEqual((blocks[0]["kind"], blocks[0]["tone"], blocks[0]["retry"]), ("notice", "fail", True))
            self.assertTrue(blocks[0]["title"])
            self.assertNotIn("fleet view", blocks[0]["text"])

    def test_an_unconfigured_bridge_is_a_setup_notice_not_a_failure_to_retry_forever(self):
        with mock.patch.object(lights.hue, "lamp_states", return_value=[]), \
                mock.patch.object(lights.hue, "configured", return_value=False):
            lights._cache.clear()
            block = lights.panel({})[0]
        self.assertTrue(block["setup"])
        self.assertFalse(block["retry"])
        self.assertIn("set up", block["text"])

    def test_retry_forgets_the_cache_and_reads_again(self):
        machines._cache.value, machines._cache.ready = None, True
        self.assertEqual(modules.act("machines", "retry", {}), "Reading it again.")
        self.assertFalse(machines._cache.ready)

    def test_a_module_that_raises_becomes_a_failure_notice_with_a_retry(self):
        with mock.patch.object(machines, "panel", side_effect=RuntimeError("boom")):
            block = modules.panel("machines", {})[0]
        self.assertEqual((block["kind"], block["tone"], block["retry"], block["module"]), ("notice", "fail", True, "machines"))

    def test_an_unreachable_lamp_is_marked(self):
        states = [{"id": "left", "name": "corner lamp", "on": True, "bri": 200, "color": "#ffffff", "ct": 2700,
                   "reachable": False, "caps": {"color": False, "ct": [2200, 6500]}}]
        lights._cache.clear()
        with mock.patch.object(lights.hue, "lamp_states", return_value=states), mock.patch.object(lights.hue, "scenes", return_value=[]):
            self.assertFalse(lights.panel({})[0]["lamps"][0]["reachable"])

    def test_a_device_never_seen_says_so_without_a_string_match(self):
        data = tailnet()
        data["Peer"]["k7"]["LastSeen"] = "0001-01-01T00:00:00Z"
        devices = self.panel(data)[1]["items"]
        old = next(row for row in devices if row["name"] == "old-ipad")
        self.assertTrue(old["never_seen"])
        self.assertFalse(next(row for row in devices if row["name"] == "kangaeru")["never_seen"])


class TestReminderRows(unittest.TestCase):
    def test_each_row_carries_a_cancel_and_an_empty_list_says_what_to_do(self):
        reminders._cache.clear()
        rows = [{"id": 4, "text": "tea", "next": "today at 15:00", "repeats": "once"}]
        with mock.patch.object(reminders, "run", return_value={"ok": True, "result": rows}):
            block = reminders.panel({})[0]
        cancel = block["items"][0]["actions"][0]
        self.assertEqual(cancel["act"], {"module": "reminders", "action": "cancel", "args": {"id": 4}})
        reminders._cache.clear()
        with mock.patch.object(reminders, "run", return_value={"ok": True, "result": []}):
            block = reminders.panel({})[0]
        self.assertEqual(block["items"], [])
        self.assertEqual((block["empty"], block["hint"]), ("Nothing is set.", "Ask me to remind you of something."))
