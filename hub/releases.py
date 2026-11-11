"""The newest desktop app release, looked up on GitHub for apps that cannot read a private repo.

The app asks GET /v1/desktop/release with its device token. The hub answers from its own GitHub
access: github_token in config.toml, else GITHUB_TOKEN, else the token of the logged-in gh CLI.
The answer is cached for an hour so a fleet of apps costs one API call.
"""

import os
import re
import subprocess
import threading
import time

import httpx

from .config import get_config

REPO = "marcusbandit/diablo-archive"  # the Qt desktop app and its releases stayed in the private archive
PREFIX = "desktop-v"
TTL_S = 3600
FAIL_TTL_S = 300

_lock = threading.Lock()
_cache = {"at": 0.0, "value": None, "ok": False}
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$")


def parse(version):
    m = _SEMVER.match(version or "")
    if not m:
        return None
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre


def _pre_key(pre):
    if not pre:
        return (1,)
    return (0,) + tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre) + ((-1, 0, ""),)


def sort_key(version):
    p = parse(version)
    return (p[0], p[1], p[2], _pre_key(p[3])) if p else (-1, -1, -1, (0,))


def token():
    cfg = get_config()
    for value in (cfg.get("github_token"), (cfg.get("github") or {}).get("token") if isinstance(cfg.get("github"), dict) else None):
        if isinstance(value, str) and value.strip():
            return value.strip()
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return os.environ["GITHUB_TOKEN"].strip()
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    t = out.stdout.strip()
    return t if out.returncode == 0 and t else None


def fetch(repo=REPO):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "diablo-hub", "X-GitHub-Api-Version": "2022-11-28"}
    t = token()
    if t:
        headers["Authorization"] = f"Bearer {t}"
    r = httpx.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 50}, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def pick(releases, include_pre=False):
    """The newest published desktop release, release candidates only when asked for."""
    best = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = rel.get("tag_name") or ""
        if not tag.startswith(PREFIX):
            continue
        version = tag[len(PREFIX):]
        parsed = parse(version)
        if not parsed or (parsed[3] and not include_pre):
            continue
        if best is None or sort_key(version) > sort_key(best["version"]):
            assets = [
                {"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size")}
                for a in rel.get("assets") or []
                if isinstance(a, dict)
            ]
            best = {
                "tag": tag,
                "version": version,
                "name": rel.get("name") or tag,
                "notes": rel.get("body") or "",
                "url": rel.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}",
                "published": rel.get("published_at"),
                "prerelease": bool(parsed[3]),
                "assets": assets,
            }
    return best


def latest(include_pre=False, now=None, fetcher=None):
    """The cached answer: {"release": {...} or None, "checked": epoch seconds, "ok": bool}."""
    now = time.time() if now is None else now
    with _lock:
        ttl = TTL_S if _cache["ok"] else FAIL_TTL_S
        fresh = _cache["at"] and now - _cache["at"] < ttl
        releases, ok, at = _cache["value"], _cache["ok"], _cache["at"]
    if not fresh:
        try:
            releases, ok = (fetcher or fetch)(), True
        except (httpx.HTTPError, ValueError, OSError):
            with _lock:
                releases = _cache["value"] if _cache["value"] is not None else []
            ok = False
        at = now
        with _lock:
            _cache.update(at=now, value=releases, ok=ok)
    return {"release": pick(releases or [], include_pre), "checked": at, "ok": ok}


def reset():
    with _lock:
        _cache.update(at=0.0, value=None, ok=False)
