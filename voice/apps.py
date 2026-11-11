"""Quick desktop skills: apps and links, windows and workspaces, notifications, clipboard, lock and suspend.

Every run function takes (args, ctx), returns (ok, text) and never raises. Programs start detached
through one helper so tests can mock it, and quick queries go through subprocess.run with a short
timeout. Window work goes through hyprctl in its Lua dispatch form, the only form Hyprland 0.56 accepts.
"""

import configparser
import difflib
import json
import re
import subprocess
import time
import urllib.parse
from functools import partial
from pathlib import Path

APP_DIRS = [Path(p).expanduser() for p in ("/usr/share/applications", "~/.local/share/applications",
            "/var/lib/flatpak/exports/share/applications", "~/.local/share/flatpak/exports/share/applications")]
_CACHE = {"stamp": None, "apps": []}


def _launch(cmd, stdin_text=None):
    null, pipe = subprocess.DEVNULL, subprocess.PIPE
    try:
        proc = subprocess.Popen(cmd, start_new_session=True, stdin=null if stdin_text is None else pipe, stdout=null, stderr=null)
        if stdin_text is not None:
            proc.communicate(stdin_text.encode(), timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"{cmd[0]} failed: {error}"
    return True, ""


def _query(cmd, as_json=False):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if done.returncode != 0:
            return False, f"{cmd[0]} failed: {(done.stderr or done.stdout).strip()[:120]}"
        return True, json.loads(done.stdout or "null") if as_json else done.stdout
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return False, f"{cmd[0]} failed: {error}"


def _parse(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
        entry = {key: value.strip() for key, value in parser["Desktop Entry"].items()}
    except (OSError, configparser.Error, KeyError):
        return None
    return {
        "id": path.stem, "name": entry.get("name", ""), "generic": entry.get("genericname", ""), "exec": entry.get("exec", ""),
        "terminal": entry.get("terminal", "").lower() == "true",
        "keywords": [k.strip() for k in entry.get("keywords", "").split(";") if k.strip()],
        "categories": [c.strip() for c in entry.get("categories", "").split(";") if c.strip()],
        "hidden": "true" in (entry.get("nodisplay", "").lower(), entry.get("hidden", "").lower()) or entry.get("type") != "Application",
    }


def apps_index():
    stamp = tuple(folder.stat().st_mtime if folder.is_dir() else None for folder in APP_DIRS)
    if _CACHE["stamp"] != stamp:
        found = {}
        for folder in APP_DIRS:
            for path in sorted(folder.glob("*.desktop")):
                entry = _parse(path)
                if entry and entry["name"]:
                    found[entry["id"]] = entry
        _CACHE.update(stamp=stamp, apps=[e for e in found.values() if not e["hidden"]])
    return _CACHE["apps"]


def _alias(query):
    """The default browser or the preferred terminal, looked up before the index."""
    apps = apps_index()
    if query in ("browser", "web browser"):
        ok, out = _query(["xdg-settings", "get", "default-web-browser"])
        app_id = out.strip().removesuffix(".desktop") if ok else ""
        files = [f / f"{app_id}.desktop" for f in APP_DIRS if app_id and (f / f"{app_id}.desktop").is_file()]
        return next((e for e in apps if e["id"] == app_id), _parse(files[0]) if files else None)
    if query == "terminal":
        for wanted in ("ghostty", "kitty"):
            for entry in apps:
                if wanted in (entry["name"].lower(), entry["id"].lower().split(".")[-1]):
                    return entry
        return next((e for e in apps if "TerminalEmulator" in e["categories"]), None)
    return None


def _rank(entry, query):
    name = entry["name"].lower()
    tests = (name == query, name.startswith(query), any(w.startswith(query) for w in name.split()),
             query in " ".join([name, entry["generic"], *entry["keywords"]]).lower())
    return next((i for i, hit in enumerate(tests) if hit), None)


def find_app(query):
    """The best desktop entry for a spoken name, or None."""
    query = re.sub(r"^(the|a|an) ", "", " ".join(str(query or "").lower().split()))
    hit = _alias(query)
    if hit or not query:
        return hit
    apps = apps_index()
    ranked = sorted(((r, e) for e in apps if (r := _rank(e, query)) is not None), key=lambda p: (p[0], len(p[1]["name"])))
    if ranked:
        return ranked[0][1]
    names = {e["name"].lower(): e for e in apps}
    close = difflib.get_close_matches(query, list(names), n=1, cutoff=0.6)
    return names[close[0]] if close else None


def open_app(args, ctx):
    query = str(args.get("name") or "").strip()
    app = find_app(query)
    if app is None:
        names = {e["name"].lower(): e["name"] for e in apps_index()}
        close = [names[k] for k in difflib.get_close_matches(query.lower(), list(names), n=3, cutoff=0.3)]
        return False, f"no app called {query}" + ("; closest: " + ", ".join(close) if close else "")
    ok, why = _launch(["gtk-launch", app["id"]])
    return ok, f"opened {app['name']}" if ok else why


def open_url(args, ctx):
    url = str(args.get("url") or "").strip()
    if not url:
        return False, "open which address?"
    url = url if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) else "https://" + url
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return False, f"I only open http and https links, not {scheme}"
    ok, why = _launch(["xdg-open", url])
    return ok, f"opened {url}" if ok else why


def web_search(args, ctx):
    query = str(args.get("query") or "").strip()
    if not query:
        return False, "search for what?"
    template = (ctx.get("cfg") or {}).get("search_url") or "https://duckduckgo.com/?q={q}"
    ok, why = _launch(["xdg-open", template.replace("{q}", urllib.parse.quote_plus(query))])
    return ok, f"searching for {query}" if ok else why


def _dispatch(lua):
    """Runs one Hyprland dispatcher. Anything but a bare ok is a failure, warnings included."""
    ok, out = _query(["hyprctl", "dispatch", lua])
    if ok and out.strip() != "ok":
        return False, (out.strip().splitlines() or ["hyprctl said nothing"])[0][:120]
    return ok, out


def _clients():
    ok, rows = _query(["hyprctl", "-j", "clients"], as_json=True)
    if not ok or not isinstance(rows, list):
        return None, rows if not ok else "hyprctl gave no window list"
    return [c for c in rows if c.get("mapped")], ""


def _focus(target):
    clients, error = _clients()
    if clients is None:
        return None, error
    hits = [c for field in ("class", "title") for c in clients if target.lower() in str(c.get(field) or "").lower()]
    if not hits:
        return None, f"no window matching {target}; open: " + ", ".join(sorted({str(c.get("class") or "?") for c in clients}))
    ok, out = _dispatch(f'hl.dsp.focus({{ window = "address:{hits[0]["address"]}" }})')
    return (hits[0], f"focused {hits[0].get('class')}") if ok else (None, out)


def list_windows(args, ctx):
    clients, error = _clients()
    if clients is None:
        return False, error
    lines = [f"{c.get('class', '?')} | {str(c.get('title', '?'))[:60]} (ws {(c.get('workspace') or {}).get('id', '?')})" for c in clients[:15]]
    return True, "; ".join(lines) or "no windows open"


def focus_window(args, ctx):
    target = str(args.get("target") or "").strip()
    client, text = _focus(target) if target else (None, "focus which window?")
    return client is not None, text


def close_window(args, ctx):
    """Closes the active window, or focuses the target and closes it once it really is active."""
    target = str(args.get("target") or "").strip()
    client, text = _focus(target) if target else ({}, "")
    if client is None:
        return False, text
    if target:
        ok, active = _query(["hyprctl", "-j", "activewindow"], as_json=True)
        if not ok or (active or {}).get("address") != client["address"]:
            return False, f"could not bring {client.get('class')} forward, so I left it open"
    ok, out = _dispatch("hl.dsp.window.close()")
    return ok, f"closed {client.get('class') or 'the active window'}" if ok else out


def _workspace_call(args, ctx, lua, said):
    number = int(args.get("number")) if str(args.get("number")).strip().isdigit() else 0
    if not 1 <= number <= 10:
        return False, "workspaces run from 1 to 10"
    ok, out = _dispatch(lua.format(number))
    return ok, said.format(number) if ok else out


switch_workspace = partial(_workspace_call, lua="hl.dsp.focus({{ workspace = {} }})", said="workspace {}")
move_to_workspace = partial(_workspace_call, lua="hl.dsp.window.move({{ workspace = {} }})", said="moved it to workspace {}")


def notify(args, ctx):
    title = str(args.get("title") or "Diablo").strip() or "Diablo"
    ok, out = _query(["notify-send", title, str(args.get("body") or "").strip()])
    return ok, "notified" if ok else out


def read_clipboard(args, ctx):
    ok, types = _query(["wl-paste", "--list-types"])
    kinds = types.split() if ok else []
    if kinds and all(k.startswith("image/") for k in kinds):
        return True, "the clipboard holds an image"
    ok, out = _query(["wl-paste", "--no-newline"])
    text = " ".join(out.split()) if ok else ""
    return True, text[:500] if text else "the clipboard is empty"


def write_clipboard(args, ctx):
    text = str(args.get("text") or "")
    if not text:
        return False, "copy what?"
    ok, why = _launch(["wl-copy"], stdin_text=text)
    return ok, "copied" if ok else why


def power(args, ctx):
    action = str(args.get("action") or "").strip().lower().replace(" ", "_")
    if action not in ("lock", "suspend", "lock_and_suspend"):
        return False, "I only lock or suspend from here"
    ok, why = _launch(["hyprlock"]) if action != "suspend" else (True, "")
    if ok and action == "lock_and_suspend":
        time.sleep(1)
    if ok and action != "lock":
        ok, why = _launch(["systemctl", "suspend"])
    return ok, {"lock": "locked", "suspend": "suspending", "lock_and_suspend": "locked and suspending"}[action] if ok else why


def _params(required=(), **props):
    schema = {"type": "object", "properties": {k: v if isinstance(v, dict) else {"type": v} for k, v in props.items()}}
    return {**schema, "required": list(required)} if required else schema


SKILLS = [
    {"name": name, "description": text, "parameters": params, "run": run, "kind": kind, "bank": bank}
    for name, text, params, run, kind, bank in [
        ("open_app", "Open an application by name, like Firefox, Spotify, Discord, Obsidian, the terminal or the browser.",
         _params(("name",), name="string"), open_app, "act", "open"),
        ("open_url", "Open a web link or site in the browser. Give the address, like youtube.com or https://example.org.",
         _params(("url",), url="string"), open_url, "act", "open"),
        ("web_search", "Search the web for something and show the results in the browser.", _params(("query",), query="string"), web_search, "act", "open"),
        ("list_windows", "Which windows are open right now, with their workspace numbers.", _params(), list_windows, "tell", "done"),
        ("focus_window", "Bring an open window to the front by its app name or part of its title, like Spotify or the Discord window.",
         _params(("target",), target="string"), focus_window, "act", "done"),
        ("close_window", "Close a window by its app name or part of its title. With no target, close the active window.",
         _params(target="string"), close_window, "act", "done"),
        ("switch_workspace", "Go to a workspace by number, 1 to 10.", _params(("number",), number="integer"), switch_workspace, "act", "done"),
        ("move_to_workspace", "Move the active window to a workspace by number, 1 to 10.",
         _params(("number",), number="integer"), move_to_workspace, "act", "done"),
        ("notify", "Show a desktop notification with a title and a body.", _params(("body",), title="string", body="string"), notify, "act", "done"),
        ("read_clipboard", "Read out what is on the clipboard right now.", _params(), read_clipboard, "tell", "done"),
        ("write_clipboard", "Put text on the clipboard so it can be pasted.", _params(("text",), text="string"), write_clipboard, "act", "done"),
        ("power", "Lock the screen, suspend the machine, or lock and then suspend. Nothing else: no shutdown, no reboot.",
         _params(("action",), action={"type": "string", "enum": ["lock", "suspend", "lock_and_suspend"]}), power, "act", "done"),
    ]
]
