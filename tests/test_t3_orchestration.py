import json

import pytest

from hub import t3link


def test_live_urls_and_refresh_reachable_are_separate_functions():
    assert t3link.reachable is not t3link.live_urls
    assert t3link.reachable.__code__.co_varnames[0] == "env"


def test_where_reports_down_and_up_machines(monkeypatch):
    monkeypatch.setattr(t3link, "environments", lambda: ["banditbox", "nemurenai"])
    monkeypatch.setattr(t3link, "is_up", lambda env, fresh=False: env == "nemurenai")
    monkeypatch.setattr(t3link, "call_tool", lambda env, name, args, timeout: {"projects": [
        {"id": "p1", "title": "AniBeam", "repositoryIdentity": {"canonicalKey": "github.com/marcusbandit/anibeam"}},
        {"id": "p2", "title": "No project", "repositoryIdentity": None},
        {"id": "p3", "title": "old", "deletedAt": "2026-10-01"},
    ]})
    t3link._where_cache[:] = [0.0, ""]
    text = t3link.where()
    assert "banditbox: down" in text
    assert "nemurenai: up, 2 projects: AniBeam [p1] (marcusbandit/anibeam); No project [p2]" in text


def test_wake_queues_the_order(monkeypatch):
    from hub import mcp_server
    store = {}
    monkeypatch.setattr(mcp_server.settings, "get", lambda key: store.get(key, ""))
    monkeypatch.setattr(mcp_server.settings, "set_setting", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(mcp_server.t3link, "is_up", lambda env, fresh=False: False)
    monkeypatch.setattr(mcp_server, "send_wake", lambda: True)
    reply = mcp_server.wake({"then": "fix the crash in banditshell with opus"})
    assert "wake packet sent" in reply
    assert json.loads(store["wake.pending"])[0]["then"] == "fix the crash in banditshell with opus"
    assert "say what" in mcp_server.wake({})
