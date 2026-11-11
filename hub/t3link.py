import base64
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from . import localenv, settings

logger = logging.getLogger(__name__)

DEFAULT_ENVIRONMENTS = "nemurenai,banditbox,diablo-pi,kangaeru"
CLIENT_NAME = "Diablo"
REDIRECT_URI = "http://127.0.0.1:8799/callback"
ACCESS = "full-access"
SCOPES = ("orchestration:read", "orchestration:operate", "settings:write", "source-control:write")
REFRESH_BEFORE_S = 3 * 24 * 3600
T3_BIN = "$HOME/.local/bin/t3"
TOKEN_LINE = re.compile(r"^Token:\s*(\S+)", re.MULTILINE)
LOCAL = socket.gethostname().split(".")[0]


def store_path():
    chosen = os.environ.get("DIABLO_T3_TOKENS")
    if chosen:
        return Path(chosen).expanduser()
    return Path.home() / ".local" / "state" / "diablo" / "t3-mcp.json"


def environments():
    raw = str(settings.get("t3.environments") or DEFAULT_ENVIRONMENTS)
    return [name.strip() for name in raw.split(",") if name.strip()]


def base_url(env):
    return str(settings.get(f"t3.{env}.url") or f"https://{localenv.host(env)}").rstrip("/")


def load():
    try:
        data = json.loads(store_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data):
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _pair_command():
    scopes = " ".join(f"--scope {scope}" for scope in SCOPES)
    return f"{T3_BIN} pair {scopes} --ttl 5m --label diablo"


def mint_pairing_code(env, timeout=40):
    command = _pair_command()
    if env == LOCAL:
        argv = ["bash", "-lc", command]
    else:
        argv = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", env, command]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    match = TOKEN_LINE.search(done.stdout or "")
    if done.returncode != 0 or not match:
        detail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"t3 pair on {env} failed: {detail[0][:200]}")
    return match.group(1)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def exchange(base, code, client=None):
    client = client or httpx.Client(timeout=20)
    resource = f"{base}/mcp"
    registered = client.post(f"{base}/oauth/mcp/register", json={
        "client_name": CLIENT_NAME,
        "redirect_uris": [REDIRECT_URI],
        "token_endpoint_auth_method": "none",
    })
    registered.raise_for_status()
    client_id = registered.json()["client_id"]
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    authorization = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    }
    decided = client.post(f"{base}/oauth/mcp/decision", json={
        "authorization": authorization,
        "decision": {"_tag": "pairing-code", "access": ACCESS, "code": code},
    })
    if decided.status_code >= 400:
        raise RuntimeError(f"approval refused: {decided.text[:300]}")
    query = parse_qs(urlparse(decided.json()["redirectTo"]).query)
    if "code" not in query:
        raise RuntimeError(f"approval returned no code: {query.get('error', ['?'])[0]}")
    if query.get("state", [""])[0] != state:
        raise RuntimeError("approval state mismatch")
    token = client.post(f"{base}/oauth/mcp/token", data={
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": resource,
    })
    if token.status_code >= 400:
        raise RuntimeError(f"token exchange failed: {token.text[:300]}")
    body = token.json()
    return {
        "url": resource,
        "token": body["access_token"],
        "expires_at": time.time() + float(body.get("expires_in") or 0),
        "scope": body.get("scope", ""),
    }


def authorize(env):
    base = base_url(env)
    code = mint_pairing_code(env)
    entry = exchange(base, code)
    entry["authorized_at"] = time.time()
    data = load()
    data[env] = entry
    save(data)
    logger.info("t3 link to %s authorized until %s", env, time.strftime("%Y-%m-%d", time.localtime(entry["expires_at"])))
    return entry


def reachable(env):
    try:
        response = httpx.get(f"{base_url(env)}/.well-known/oauth-authorization-server", timeout=5)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def refresh(force=False):
    data = load()
    report = {}
    for env in environments():
        entry = data.get(env) or {}
        fresh = entry.get("token") and entry.get("expires_at", 0) - time.time() > REFRESH_BEFORE_S
        if fresh and not force:
            report[env] = "ok"
            continue
        if not reachable(env):
            report[env] = "unreachable" if not entry else "unreachable, kept the old token"
            continue
        try:
            authorize(env)
            report[env] = "authorized"
        except (RuntimeError, httpx.HTTPError, subprocess.TimeoutExpired, OSError, KeyError, ValueError) as error:
            logger.warning("t3 link to %s failed: %s", env, error)
            report[env] = f"failed: {error}"
    return report


_reach = {}
REACH_TTL_S = 60


def _probe(url):
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=1.5):
            return True
    except OSError:
        return False


def live_urls(urls):
    """Which T3 servers answer right now. An offline box would stall a claude spawn for 30s."""
    now = time.monotonic()
    stale = [url for url in urls if now - _reach.get(url, (0, False))[0] > REACH_TTL_S]
    if stale:
        with ThreadPoolExecutor(max_workers=len(stale)) as pool:
            for url, up in zip(stale, pool.map(_probe, stale)):
                _reach[url] = (now, up)
    return {url for url in urls if _reach[url][1]}


def mcp_servers():
    servers = {}
    now = time.time()
    data = load()
    live = live_urls([(data.get(env) or {}).get("url") for env in environments() if (data.get(env) or {}).get("url")])
    for env in environments():
        entry = data.get(env) or {}
        if not entry.get("token") or entry.get("expires_at", 0) <= now or entry.get("url") not in live:
            continue
        servers[f"t3-{env}"] = {
            "type": "http",
            "url": entry["url"],
            "headers": {"Authorization": f"Bearer {entry['token']}"},
        }
    return servers


def _rpc_body(response):
    text = response.text.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload.startswith("{"):
                return json.loads(payload)
    raise RuntimeError(f"no json-rpc body in: {text[:200]}")


def call_tool(env, name, arguments=None, timeout=60):
    entry = load().get(env) or {}
    if not entry.get("token"):
        raise RuntimeError(f"no t3 link to {env}")
    headers = {
        "Authorization": f"Bearer {entry['token']}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        init = client.post(entry["url"], headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "diablo-hub", "version": "2"}},
        })
        init.raise_for_status()
        session = init.headers.get("mcp-session-id")
        if session:
            headers["mcp-session-id"] = session
        client.post(entry["url"], headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = client.post(entry["url"], headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        response.raise_for_status()
        body = _rpc_body(response)
    if "error" in body:
        raise RuntimeError(str(body["error"].get("message") or body["error"])[:300])
    result = body.get("result") or {}
    text = "\n".join(part.get("text", "") for part in result.get("content") or [] if part.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text[:300] or "tool error")
    try:
        return json.loads(text)
    except ValueError:
        return text


def status():
    data = load()
    rows = []
    for env in environments():
        entry = data.get(env) or {}
        if not entry.get("token"):
            rows.append(f"{env}: not linked")
            continue
        days = (entry.get("expires_at", 0) - time.time()) / 86400
        rows.append(f"{env}: linked, {days:.0f} days left" if days > 0 else f"{env}: expired")
    return "\n".join(rows)


def is_up(env, fresh=False):
    """True when the env's T3 server answers on its port right now."""
    url = (load().get(env) or {}).get("url")
    if not url:
        return False
    if fresh:
        _reach.pop(url, None)
    return url in live_urls([url])


_where_cache = [0.0, ""]


def where(max_age=60):
    """Which T3 servers are up and which projects each one has, as compact text for the brain."""
    if _where_cache[1] and time.time() - _where_cache[0] <= max_age:
        return _where_cache[1]
    lines = []
    for env in environments():
        if not is_up(env):
            lines.append(f"{env}: down")
            continue
        try:
            projects = (call_tool(env, "t3_project_list", {"limit": 100}, 20) or {}).get("projects") or []
        except Exception as error:
            lines.append(f"{env}: up, project list failed ({str(error)[:80]})")
            continue
        names = []
        for project in projects:
            if project.get("deletedAt"):
                continue
            repo = ((project.get("repositoryIdentity") or {}).get("canonicalKey") or "").replace("github.com/", "")
            names.append(f"{project['title']} [{project['id']}]" + (f" ({repo})" if repo else ""))
        lines.append(f"{env}: up, {len(names)} projects: " + "; ".join(names))
    text = "\n".join(lines)
    _where_cache[:] = [time.time(), text]
    return text
