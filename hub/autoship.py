"""A merged PR reaches the running hub by itself.

The primary hub (diablo-pi) asks GitHub every few minutes for the head of main. When that commit
changes hub/ or web/ against the REVISION it runs, it waits until restart_self would call a restart
safe, fetches the commit's tarball, installs a changed requirements.txt, checks that the new hub
imports, keeps the live hub/ and web/ as dated backups (the newest 5), swaps the new ones in and
asks selfrestart for the restart, which tells Marcus in one line which PR he restarted for.

The standby follows the primary: when the primary's /v1/health reports a revision the standby does
not run, and the standby is not serving, it fetches that commit the same way and restarts.

`autoship.enabled` set to off stops both. Anything that fails leaves the running code in place, and
Marcus hears about it once per commit.
"""

import asyncio
import io
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import httpx

from . import brains, releases, selfrestart, settings, standby

logger = logging.getLogger(__name__)

# The hub ships from the public repo; releases.REPO is the private archive that keeps the old
# desktop app's releases, so the two must never share a name.
REPO = "marcusbandit/diablo"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ("hub/", "web/")
KEEP_BACKUPS = 5
SHORT = 7

ENABLED_KEY = "autoship.enabled"
INTERVAL_KEY = "autoship.interval_s"
SEEN_KEY = "autoship.seen"
FAILED_KEY = "autoship.failed"
DEFAULT_INTERVAL_S = 180
FIRST_DELAY_S = 60
SAFE_WAIT_S = 30
IMPORT_TIMEOUT_S = 120
INSTALL_TIMEOUT_S = 600


class ShipError(Exception):
    """A commit that cannot ship. transient ones (a download that failed) are tried again next time."""

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


# What the standby failed on. Its settings live in the mirrored database, which the next pull replaces.
_standby_failed = {"sha": None}


def enabled():
    return selfrestart.truthy(settings.get(ENABLED_KEY) or "on")


def interval():
    try:
        return max(30.0, float(settings.get(INTERVAL_KEY) or DEFAULT_INTERVAL_S))
    except ValueError:
        return float(DEFAULT_INTERVAL_S)


def revision(root=None):
    """The commit this hub runs, as ship-hub.sh or a previous autoship wrote it."""
    try:
        return ((root or ROOT) / "hub" / "REVISION").read_text().strip()
    except OSError:
        return ""


def same(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return n >= SHORT and a[:n] == b[:n]


def touches(files):
    return [f for f in files if f.startswith(SHIPPED)]


# GitHub ---------------------------------------------------------------------------------------


def headers():
    out = {"Accept": "application/vnd.github+json", "User-Agent": "diablo-hub", "X-GitHub-Api-Version": "2022-11-28"}
    token = releases.token()
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


def gh(path, **params):
    r = httpx.get(f"https://api.github.com/repos/{REPO}/{path}", params=params or None, headers=headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def resolve(ref):
    """A commit: {"sha", "title"}."""
    data = gh(f"commits/{ref}")
    return {"sha": data["sha"], "title": (data.get("commit", {}).get("message") or "").splitlines()[0][:200]}


def head():
    """The newest commit on main."""
    return resolve(BRANCH)


def changed(base, sha):
    """The shipped files that differ between what runs and sha, and the commit titles that changed them.
    An unknown base counts as everything changed."""
    if not base:
        return ["hub/REVISION"], []
    try:
        data = gh(f"compare/{base}...{sha}")
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return ["hub/REVISION"], []
        raise
    files = touches([f.get("filename", "") for f in data.get("files") or []])
    titles = [(c.get("commit", {}).get("message") or "").splitlines()[0][:200] for c in data.get("commits") or []]
    return files, titles


def tarball(sha):
    r = httpx.get(f"https://api.github.com/repos/{REPO}/tarball/{sha}", headers=headers(), timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.content


# The swap -------------------------------------------------------------------------------------


def unpack(blob, into):
    """hub/ and web/ of a GitHub tarball, written under into/."""
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        wanted = []
        for member in archive.getmembers():
            parts = member.name.split("/", 1)
            if len(parts) != 2 or not parts[1].startswith(SHIPPED):
                continue
            member.name = parts[1]
            wanted.append(member)
        if not any(m.name.startswith("hub/") for m in wanted):
            raise ShipError("the commit has no hub/ folder")
        archive.extractall(into, members=wanted, filter="data")


def requirements_changed(stage, root):
    new, old = stage / "hub" / "requirements.txt", root / "hub" / "requirements.txt"
    return new.exists() and not (old.exists() and new.read_bytes() == old.read_bytes())


def installer(python):
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "-q", "--python", python, "-r"]
    if subprocess.run([python, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        return [python, "-m", "pip", "install", "-q", "-r"]
    raise ShipError("hub/requirements.txt changed and this venv has neither uv nor pip, so the packages need installing by hand")


def install(requirements, python):
    out = subprocess.run(installer(python) + [str(requirements)], capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
    if out.returncode != 0:
        raise ShipError(f"installing {requirements.name} failed: {(out.stderr or out.stdout).strip()[-300:]}")


def check(stage, python):
    """The new hub has to import with this venv before it may replace the running one."""
    env = {**os.environ, "PYTHONPATH": str(stage), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([python, "-c", "import hub.server"], cwd=stage, env=env, capture_output=True, text=True, timeout=IMPORT_TIMEOUT_S)
    if out.returncode != 0:
        raise ShipError(f"the new hub does not import: {(out.stderr or out.stdout).strip()[-300:]}")


def swap(stage, root, sha):
    """Move the live hub/ and web/ aside as dated backups and the staged ones in. Fonts the checkout
    lacks stay, the way ship-hub.sh leaves web/fonts alone."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old_fonts, new_fonts = root / "web" / "fonts", stage / "web" / "fonts"
    if old_fonts.is_dir():
        new_fonts.mkdir(parents=True, exist_ok=True)
        for font in old_fonts.iterdir():
            if font.is_file() and not (new_fonts / font.name).exists():
                shutil.copy2(font, new_fonts / font.name)
    (stage / "hub" / "REVISION").write_text(sha[:SHORT] + "\n")
    moved = []
    try:
        for name in ("hub", "web"):
            live, new = root / name, stage / name
            if not new.exists():
                continue
            if live.exists():
                backup = root / f"{name}.bak-{stamp}"
                live.rename(backup)
                moved.append((live, backup))
            new.rename(live)
    except OSError as error:
        for live, backup in reversed(moved):
            if live.exists():
                shutil.rmtree(live, ignore_errors=True)
            backup.rename(live)
        raise ShipError(f"could not swap the code in: {error}")
    for name in ("hub", "web"):
        for old in sorted(root.glob(f"{name}.bak-2*"))[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)
    return stamp


def ship(sha, root=None, python=None, fetch=tarball):
    """Fetch sha, install, check and swap it in. Raises ShipError and leaves the live code alone on failure."""
    root, python = root or ROOT, python or sys.executable
    stage = root / f".autoship-{sha[:SHORT]}"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        try:
            blob = fetch(sha)
        except (httpx.HTTPError, OSError) as error:
            raise ShipError(f"could not download {sha[:SHORT]}: {error}", transient=True)
        try:
            unpack(blob, stage)
        except (tarfile.TarError, OSError) as error:
            raise ShipError(f"could not unpack {sha[:SHORT]}: {error}")
        if requirements_changed(stage, root):
            installer(python)
            install(stage / "hub" / "requirements.txt", python)
            try:
                check(stage, python)
            except ShipError:
                # Put the running hub's packages back before saying it keeps running what it has.
                install(root / "hub" / "requirements.txt", python)
                raise
        else:
            check(stage, python)
        return swap(stage, root, sha)
    except subprocess.TimeoutExpired as error:
        raise ShipError(f"{error.cmd[0]} took longer than {error.timeout} s", transient=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# The loops ------------------------------------------------------------------------------------


def record(outcome, sha, detail=""):
    logger.info("autoship %s %s %s", outcome, sha[:SHORT], detail)
    try:
        brains.STATE.mkdir(parents=True, exist_ok=True)
        with open(brains.STATE / "autoship.jsonl", "a") as handle:
            handle.write(json.dumps({"ts": time.time(), "outcome": outcome, "sha": sha, "detail": detail[:300]}) + "\n")
    except OSError:
        pass


def reason_for(title, titles):
    title = title or "a new commit"
    more = len([t for t in titles if t != title])
    return f"merged on main: {title}" + (f" (and {more} more)" if more else "")


async def failed(sha, error, deliver):
    """A transient failure is tried again next time. Anything else: tell Marcus once and wait for the next merge."""
    record("failed", sha, str(error))
    if error.transient:
        return
    settings.set_setting(SEEN_KEY, sha)
    if settings.get(FAILED_KEY) == sha:
        return
    settings.set_setting(FAILED_KEY, sha)
    if deliver:
        try:
            await deliver(f"I did not update myself to {sha[:SHORT]}: {error}. I keep running the code I have.",
                          {"conv": brains.MAIN, "thread_id": "autoship"})
        except Exception:
            logger.exception("could not tell Marcus the autoship failed")


async def wait_until_safe():
    while not selfrestart.assess(None)["safe"] or selfrestart._scheduled["conv"] is not None:
        if not enabled():
            return False
        await asyncio.sleep(SAFE_WAIT_S)
    return True


async def step(deliver=None):
    """One look at main on the primary. Returns what happened, for the log and the tests."""
    if not enabled():
        return "off"
    top = await asyncio.to_thread(head)
    sha = top["sha"]
    running = revision()
    if sha == settings.get(SEEN_KEY) or same(running, sha):
        settings.set_setting(SEEN_KEY, sha)
        return "current"
    files, titles = await asyncio.to_thread(changed, running, sha)
    if not files:
        record("skipped", sha, "nothing under hub/ or web/ changed")
        settings.set_setting(SEEN_KEY, sha)
        return "skipped"
    if not await wait_until_safe():
        return "off"
    top = await asyncio.to_thread(head)
    if top["sha"] != sha:
        return "moved"
    try:
        stamp = await asyncio.to_thread(ship, sha)
    except ShipError as error:
        await failed(sha, error, deliver)
        return "failed"
    settings.set_setting(SEEN_KEY, sha)
    record("swapped", sha, f"backups hub.bak-{stamp} and web.bak-{stamp}")
    return await restart_into(sha, reason_for(top["title"], titles))


async def restart_into(sha, reason):
    """The new code is on disk: keep asking for the restart until it happens. A restart that was
    scheduled and then cancelled (he started speaking) is asked for again. Switched off, the new code
    waits on disk for the next restart."""
    while True:
        if not enabled():
            record("waiting", sha, "autoship switched off, the new code starts with the next restart")
            return "swapped"
        verdict = selfrestart.request(brains.MAIN, reason, handover=False)
        if verdict["restarting"]:
            task = selfrestart._scheduled["task"]
            if task is not None:
                await asyncio.shield(task)
            record("waiting", sha, "the restart was cancelled, asking again")
        else:
            record("waiting", sha, "; ".join(verdict["reasons"]))
        await asyncio.sleep(SAFE_WAIT_S)


def primary_revision():
    url = standby.primary_url()
    if not url:
        return ""
    r = httpx.get(f"{url.rstrip('/')}/v1/health", timeout=5)
    r.raise_for_status()
    return str(r.json().get("revision") or "")


async def standby_step():
    """One look at the primary from the standby: follow its revision while the standby is idle."""
    if not enabled() or standby.serving():
        return "idle"
    try:
        target = await asyncio.to_thread(primary_revision)
    except (httpx.HTTPError, ValueError):
        return "unreachable"
    if not target or same(revision(), target) or _standby_failed["sha"] == target:
        return "current"
    try:
        sha = (await asyncio.to_thread(resolve, target))["sha"]
        await asyncio.to_thread(ship, sha)
    except ShipError as error:
        record("failed", target, str(error))
        if not error.transient:
            _standby_failed["sha"] = target
        return "failed"
    # The primary may have gone down during the download or the install: never drop the phone mid-service.
    while standby.serving():
        await asyncio.sleep(SAFE_WAIT_S)
    record("restarting", sha, "following the primary")
    if selfrestart.under_systemd():
        os.kill(os.getpid(), signal.SIGTERM)
    return "restarting"


async def run(deliver):
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            outcome = await (standby_step() if standby.is_standby() else step(deliver))
            if outcome == "restarting":
                return
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning("autoship could not reach GitHub: %s", error)
        except Exception:
            logger.exception("autoship step failed")
        await asyncio.sleep(interval())
