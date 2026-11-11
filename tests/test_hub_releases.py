import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from hub import releases
from tests.test_hub_api import HEADERS, ApiCase


def rel(tag, draft=False, body="notes", assets=()):
    return {
        "tag_name": tag,
        "draft": draft,
        "name": f"Diablo desktop {tag}",
        "body": body,
        "html_url": f"https://github.com/marcusbandit/diablo/releases/tag/{tag}",
        "published_at": "2026-11-10T12:00:00Z",
        "assets": [{"name": a, "browser_download_url": f"https://example/{a}", "size": 1} for a in assets],
    }


LIST = [
    rel("phone-v9.0.0"),
    rel("v2.0"),
    rel("desktop-v0.2.0"),
    rel("desktop-v1.0.0", body="### Added\n- all of it", assets=["Diablo-1.0.0-x86_64.AppImage"]),
    rel("desktop-v1.1.0-rc.1"),
    rel("desktop-v2.0.0", draft=True),
    rel("desktop-vbogus"),
]


class TestPick(unittest.TestCase):
    def setUp(self):
        releases.reset()

    def test_the_newest_published_desktop_release_wins(self):
        best = releases.pick(LIST)
        self.assertEqual(best["version"], "1.0.0")
        self.assertEqual(best["notes"], "### Added\n- all of it")
        self.assertEqual(best["assets"][0]["name"], "Diablo-1.0.0-x86_64.AppImage")
        self.assertFalse(best["prerelease"])

    def test_release_candidates_only_when_asked(self):
        self.assertEqual(releases.pick(LIST, include_pre=True)["version"], "1.1.0-rc.1")

    def test_semver_order(self):
        chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.10.0"]
        keys = [releases.sort_key(v) for v in chain]
        self.assertEqual(keys, sorted(keys))

    def test_no_desktop_release_is_none(self):
        self.assertIsNone(releases.pick([rel("phone-v1.0.0")]))

    def test_answers_are_cached_and_failures_keep_the_last_list(self):
        calls = []

        def ok():
            calls.append(1)
            return LIST

        def down():
            raise httpx.ConnectError("offline")

        first = releases.latest(now=1000, fetcher=ok)
        self.assertTrue(first["ok"])
        releases.latest(now=1500, fetcher=ok)
        self.assertEqual(len(calls), 1)
        stale = releases.latest(now=1000 + releases.TTL_S + 1, fetcher=down)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["release"]["version"], "1.0.0")


class TestEndpoint(ApiCase):
    def setUp(self):
        super().setUp()
        releases.reset()

    def test_needs_a_token(self):
        self.assertEqual(self.client.get("/v1/desktop/release").status_code, 403)

    def test_answers_with_the_newest_release(self):
        with mock.patch.object(releases, "fetch", return_value=LIST):
            r = self.client.get("/v1/desktop/release", headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["release"]["tag"], "desktop-v1.0.0")
        self.assertTrue(r.json()["ok"])

    def test_github_down_is_an_empty_answer_not_an_error(self):
        with mock.patch.object(releases, "fetch", side_effect=httpx.ConnectError("offline")):
            r = self.client.get("/v1/desktop/release", headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["release"], None)
        self.assertFalse(r.json()["ok"])


if __name__ == "__main__":
    unittest.main()
