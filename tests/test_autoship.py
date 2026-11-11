import asyncio
import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import autoship, brains, db, selfrestart, settings, standby

SHA = "abcdef1234567890abcdef1234567890abcdef12"
OLD = "1111111"


def make_tarball(files):
    """A GitHub style tarball: everything under one top folder."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"marcusbandit-diablo-{SHA[:7]}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


GOOD = {
    "hub/__init__.py": "",
    "hub/server.py": "VALUE = 2\n",
    "hub/requirements.txt": "httpx\n",
    "web/index.html": "new",
    "desktop/ignored.txt": "not shipped",
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    db.init(str(tmp_path / "diablo.db"))
    monkeypatch.setattr(brains, "STATE", tmp_path / "state")
    live = tmp_path / "live"
    (live / "hub").mkdir(parents=True)
    (live / "hub" / "__init__.py").write_text("")
    (live / "hub" / "server.py").write_text("VALUE = 1\n")
    (live / "hub" / "requirements.txt").write_text("httpx\n")
    (live / "hub" / "REVISION").write_text(OLD + "\n")
    (live / "web" / "fonts").mkdir(parents=True)
    (live / "web" / "index.html").write_text("old")
    (live / "web" / "fonts" / "Local.woff2").write_text("font")
    monkeypatch.setattr(autoship, "ROOT", live)
    return live


def test_revisions_match_on_a_shared_prefix():
    assert autoship.same("abcdef1", SHA)
    assert autoship.same(SHA, SHA[:9])
    assert not autoship.same("abcdef2", SHA)
    assert not autoship.same("", SHA)
    assert not autoship.same("abc", SHA)


def test_only_hub_and_web_count():
    assert autoship.touches(["hub/a.py", "web/x.css", "desktop/y.rs", "hubby.txt"]) == ["hub/a.py", "web/x.css"]


def test_ship_swaps_in_the_new_code_and_keeps_a_backup(root):
    stamp = autoship.ship(SHA, root=root, fetch=lambda sha: make_tarball(GOOD))
    assert (root / "hub" / "server.py").read_text() == "VALUE = 2\n"
    assert (root / "hub" / "REVISION").read_text().strip() == SHA[:7]
    assert (root / "web" / "index.html").read_text() == "new"
    assert (root / "web" / "fonts" / "Local.woff2").read_text() == "font"
    assert not (root / "desktop").exists()
    assert (root / f"hub.bak-{stamp}" / "server.py").read_text() == "VALUE = 1\n"
    assert autoship.revision(root) == SHA[:7]
    assert not list(root.glob(".autoship-*"))


def test_a_hub_that_does_not_import_leaves_the_live_code_alone(root):
    broken = {**GOOD, "hub/server.py": "def oops(:\n"}
    with pytest.raises(autoship.ShipError, match="does not import"):
        autoship.ship(SHA, root=root, fetch=lambda sha: make_tarball(broken))
    assert (root / "hub" / "server.py").read_text() == "VALUE = 1\n"
    assert autoship.revision(root) == OLD
    assert not list(root.glob("hub.bak-*"))
    assert not list(root.glob(".autoship-*"))


def test_changed_requirements_without_an_installer_need_a_manual_ship(root, monkeypatch):
    monkeypatch.setattr(autoship.shutil, "which", lambda name: None)
    no_pip = sys.executable  # replaced below by a python that has no pip module
    monkeypatch.setattr(autoship.subprocess, "run", _no_pip(autoship.subprocess.run))
    newer = {**GOOD, "hub/requirements.txt": "httpx\nnewthing\n"}
    with pytest.raises(autoship.ShipError, match="installing by hand"):
        autoship.ship(SHA, root=root, python=no_pip, fetch=lambda sha: make_tarball(newer))
    assert autoship.revision(root) == OLD


def _no_pip(real):
    class Done:
        returncode = 1
        stdout = stderr = ""

    def run(cmd, *args, **kwargs):
        if cmd[1:3] == ["-m", "pip"]:
            return Done()
        return real(cmd, *args, **kwargs)

    return run


def test_old_backups_are_pruned_to_five(root):
    for day in range(1, 8):
        (root / f"hub.bak-2026110{day}-000000").mkdir()
    autoship.ship(SHA, root=root, fetch=lambda sha: make_tarball(GOOD))
    assert len(list(root.glob("hub.bak-2*"))) == autoship.KEEP_BACKUPS


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def primary(root, monkeypatch):
    monkeypatch.setattr(autoship, "head", lambda: {"sha": SHA, "title": "Lamps fade (#300)"})
    monkeypatch.setattr(autoship, "changed", lambda base, sha: (["hub/hue.py"], ["Lamps fade (#300)"]))
    monkeypatch.setattr(selfrestart, "assess", lambda conv=None: {"safe": True, "reasons": [], "threads": []})
    monkeypatch.setattr(autoship, "SAFE_WAIT_S", 0)
    asked = []
    def request(conv, reason, handover=True):
        # A real restart ends the process; here the loop stops by being switched off.
        asked.append(reason)
        settings.set_setting(autoship.ENABLED_KEY, "off")
        return {"restarting": True, "reasons": []}

    monkeypatch.setattr(selfrestart, "request", request)
    shipped = []
    monkeypatch.setattr(autoship, "ship", lambda sha: shipped.append(sha) or "20261111-000000")
    return asked, shipped


def test_a_merge_that_touches_the_hub_ships_and_restarts(primary):
    asked, shipped = primary
    assert run(autoship.step()) == "swapped"
    assert shipped == [SHA]
    assert asked == ["merged on main: Lamps fade (#300)"]
    assert settings.get(autoship.SEEN_KEY) == SHA


def test_a_merge_outside_hub_and_web_is_only_noted(primary, monkeypatch):
    asked, shipped = primary
    monkeypatch.setattr(autoship, "changed", lambda base, sha: ([], ["Phone tweak"]))
    assert run(autoship.step()) == "skipped"
    assert shipped == [] and asked == []
    assert run(autoship.step()) == "current"


def test_the_running_commit_is_current(primary, root):
    asked, shipped = primary
    (root / "hub" / "REVISION").write_text(SHA[:7])
    assert run(autoship.step()) == "current"
    assert shipped == []


def test_switched_off_does_nothing(primary):
    asked, shipped = primary
    settings.set_setting(autoship.ENABLED_KEY, "off")
    assert run(autoship.step()) == "off"
    assert shipped == []


def test_it_waits_while_a_restart_is_unsafe(primary, monkeypatch):
    asked, shipped = primary
    answers = iter([False, False, True, True])
    monkeypatch.setattr(selfrestart, "assess", lambda conv=None: {"safe": next(answers), "reasons": ["he is speaking"], "threads": []})
    assert run(autoship.step()) == "swapped"
    assert shipped == [SHA]


def test_a_failed_ship_tells_marcus_once_and_waits_for_the_next_merge(primary, monkeypatch):
    asked, shipped = primary

    def boom(sha):
        raise autoship.ShipError("the new hub does not import: SyntaxError")

    monkeypatch.setattr(autoship, "ship", boom)
    told = []

    async def deliver(text, meta):
        told.append(text)

    assert run(autoship.step(deliver)) == "failed"
    assert run(autoship.step(deliver)) == "current"
    assert len(told) == 1 and "SyntaxError" in told[0]
    assert asked == []


def test_the_standby_follows_the_primary_while_idle(root, monkeypatch):
    monkeypatch.setattr(standby, "serving", lambda: False)
    monkeypatch.setattr(autoship, "primary_revision", lambda: SHA[:7])
    monkeypatch.setattr(autoship, "resolve", lambda ref: {"sha": SHA, "title": "x"})
    monkeypatch.setattr(selfrestart, "under_systemd", lambda: False)
    shipped = []
    monkeypatch.setattr(autoship, "ship", lambda sha: shipped.append(sha))
    assert run(autoship.standby_step()) == "restarting"
    assert shipped == [SHA]


def test_the_standby_holds_while_it_serves(root, monkeypatch):
    monkeypatch.setattr(standby, "serving", lambda: True)
    assert run(autoship.standby_step()) == "idle"


def test_the_standby_is_current_when_it_runs_the_primary_revision(root, monkeypatch):
    monkeypatch.setattr(standby, "serving", lambda: False)
    monkeypatch.setattr(autoship, "primary_revision", lambda: OLD)
    assert run(autoship.standby_step()) == "current"


def test_a_failed_download_is_tried_again(primary, monkeypatch):
    asked, shipped = primary
    tries = []

    def flaky(sha):
        tries.append(sha)
        raise autoship.ShipError("could not download: 502", transient=True)

    monkeypatch.setattr(autoship, "ship", flaky)
    told = []

    async def deliver(text, meta):
        told.append(text)

    assert run(autoship.step(deliver)) == "failed"
    assert run(autoship.step(deliver)) == "failed"
    assert len(tries) == 2 and told == []


def test_a_cancelled_restart_is_asked_for_again(primary, monkeypatch):
    asked, shipped = primary
    answers = iter([{"restarting": True, "reasons": []}, {"restarting": False, "reasons": ["he is speaking"]}, {"restarting": True, "reasons": []}])

    def request(conv, reason, handover=True):
        assert handover is False
        asked.append(reason)
        verdict = next(answers, None)
        if verdict is None:
            settings.set_setting(autoship.ENABLED_KEY, "off")
            return {"restarting": False, "reasons": ["done"]}
        return verdict

    monkeypatch.setattr(selfrestart, "request", request)
    assert run(autoship.step()) == "swapped"
    assert len(asked) == 4


def test_a_broken_hub_with_new_requirements_puts_the_old_packages_back(root, monkeypatch):
    installed = []
    monkeypatch.setattr(autoship, "installer", lambda python: ["true"])
    monkeypatch.setattr(autoship, "install", lambda req, python: installed.append(req.read_text()))
    broken = {**GOOD, "hub/server.py": "def oops(:\n", "hub/requirements.txt": "httpx\nnewthing\n"}
    with pytest.raises(autoship.ShipError, match="does not import"):
        autoship.ship(SHA, root=root, fetch=lambda sha: make_tarball(broken))
    assert installed == ["httpx\nnewthing\n", "httpx\n"]
    assert autoship.revision(root) == OLD


def test_the_standby_waits_when_it_starts_serving_mid_ship(root, monkeypatch):
    serving = iter([False, True, False])
    monkeypatch.setattr(standby, "serving", lambda: next(serving))
    monkeypatch.setattr(autoship, "primary_revision", lambda: SHA[:7])
    monkeypatch.setattr(autoship, "resolve", lambda ref: {"sha": SHA, "title": "x"})
    monkeypatch.setattr(autoship, "ship", lambda sha: None)
    monkeypatch.setattr(autoship, "SAFE_WAIT_S", 0)
    monkeypatch.setattr(selfrestart, "under_systemd", lambda: False)
    assert run(autoship.standby_step()) == "restarting"
    assert next(serving, "used") == "used"


def test_the_standby_remembers_a_failure_outside_the_mirrored_settings(root, monkeypatch):
    monkeypatch.setattr(autoship, "_standby_failed", {"sha": None})
    monkeypatch.setattr(standby, "serving", lambda: False)
    monkeypatch.setattr(autoship, "primary_revision", lambda: SHA[:7])
    monkeypatch.setattr(autoship, "resolve", lambda ref: {"sha": SHA, "title": "x"})

    def boom(sha):
        raise autoship.ShipError("the new hub does not import")

    monkeypatch.setattr(autoship, "ship", boom)
    assert run(autoship.standby_step()) == "failed"
    assert settings.get(autoship.FAILED_KEY) in (None, "")
    assert run(autoship.standby_step()) == "current"


def test_autoship_follows_the_public_repo_not_the_release_archive():
    from hub import releases

    assert autoship.REPO == "marcusbandit/diablo"
    assert autoship.REPO != releases.REPO
