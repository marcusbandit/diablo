"""Desktop commands the hub's Jev picked, run here at once, and the voice loop's words.

The hub never touches this computer for these: a voice line goes up with the open windows
and media players (desktop_state), Jev picks a plain command in the call it makes anyway,
and the reply carries it back as "act" for run() to do with hyprctl, playerctl and wpctl.
"""

import collections
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import apps, desktop

ADDRESS = re.compile(r"^0x[0-9a-fA-F]+$")
PLAYER = re.compile(r"^[\w.\-]+$")
WINDOWS_MAX = 15
VOLUME_STEP = "10%"

# "Diablo, speech control": he keeps listening without the name until it is stopped. The
# voice loop words from before mean the same mode.
MODE = r"(voice|command|speech|control|accessibility)\W*(loop|mode|control)"
LOOP_START = re.compile(
    r"\b(start|begin|enter|open|turn on|switch on|go into|activate)\b\W+(the |a |your )?" + MODE + r"\b"
    r"|^\W*(diablo\W*)?" + MODE + r"(\W+(on|mode))?\W*$"
    r"|^\W*(diablo\W*)?(speech|voice|accessibility)\W+(control|mode)\W*$",
    re.I,
)
LOOP_STOP = re.compile(
    r"\b(stop|end|exit|leave|close|quit|turn off|switch off)\b\W+(the |your )?(" + MODE + r"|loop|mode)\b"
    r"|\b" + MODE + r"\W+off\b"
    r"|^\W*(diablo\W*)?(that'?s all|that is all|end loop|exit loop|loop off|i'?m done|we'?re done)\W*$",
    re.I,
)
# "Type hello there": the words after it, typed as said. Whisper's closing full stop goes. Only
# "type": "write a script that ..." is a request for Claude, not text to paste.
TYPE = re.compile(r"^\W*(?:diablo\W+)?type(?:\s+in)?[\s:,]+(.+?)\.?\s*$", re.I | re.S)
# "Dictate", "take dictation", "start dictating": a long stretch of talk, typed once he ends it.
DICTATE = re.compile(
    r"^\W*(?:diablo\W+)?(?:(?:start|begin|take)\s+)?(?:dictate|dictation|dictating)(?:\s+(?:this|mode|now|that))?\W*$",
    re.I,
)
# Said at the end of the dictation, and cut from what gets typed.
DICTATION_DONE = re.compile(
    r"\s*\b(?:dictation|dictating)\W+(?:done|over|stop|stopped|end|ended|finished|complete)\W*$"
    r"|\s*\b(?:done|stop|end|finish|finished)\W+(?:dictation|dictating)\W*$",
    re.I,
)
# "Animate me": he records a take of Marcus and acts it out on video (voice/act.py).
ANIMATE = re.compile(
    r"^\W*(?:diablo\W+)?(?:(?:please|can\s+you|could\s+you)\W+)?"
    r"(?:animate\s+(?:me|this|my\s+voice)|act(?:\s+(?:this|me|it))?(?:\s+out)?|diablo\s+act"
    r"|make\s+(?:me\s+)?(?:an?\s+)?animation)(?:\s+please)?\W*$",
    re.I,
)
# Said at the end of a take, and cut from the audio.
ANIMATE_DONE = re.compile(
    r"\s*\b(?:(?:i'?m|i\s+am)\s+(?:done|finished)"
    r"|(?:dictation|dictating|recording|animation|take)\W+(?:done|over|finished|complete|ended)"
    r"|(?:done|stop|end)\s+(?:recording|dictation|the\s+take))\W*$",
    re.I,
)
# Keys and pointer actions for the acts route.KEY_SAID names: a combo for desktop.press_key,
# or ("scroll", clicks) or ("click", button, times). Window moves press his own Hyprland
# binds, so they follow whatever layout is on.
KEYS = {
    "focus_left": "super+left", "focus_right": "super+right", "focus_up": "super+up",
    "focus_down": "super+down", "fullscreen": "super+a", "new_window": "ctrl+n", "terminal": "super+enter",
    "enter": "enter", "escape": "esc", "tab": "tab", "backspace": "backspace",
    "delete_word": "ctrl+backspace", "copy": "ctrl+c", "paste": "ctrl+v", "cut": "ctrl+x",
    "undo": "ctrl+z", "redo": "ctrl+shift+z", "select_all": "ctrl+a", "save": "ctrl+s", "find": "ctrl+f",
    "new_tab": "ctrl+t", "close_tab": "ctrl+w", "next_tab": "ctrl+tab", "previous_tab": "ctrl+shift+tab",
    "reopen_tab": "ctrl+shift+t", "reload": "ctrl+r", "page_up": "pageup", "page_down": "pagedown",
    "top": "ctrl+home", "bottom": "ctrl+end",
    "scroll_up": ("scroll", -5), "scroll_down": ("scroll", 5),
    "click": ("click", "left", 1), "double_click": ("click", "left", 2), "right_click": ("click", "right", 1),
}


def typed_words(text):
    """The words of "type ..." to type, or None when the line is not that."""
    found = TYPE.match(str(text or ""))
    return found.group(1).strip() if found and found.group(1).strip() else None


def wants_animation(text):
    return bool(ANIMATE.match(str(text or "")))


def animation_end(text):
    """(ended, words): a piece of a take, with the closing phrase cut off."""
    text = str(text or "").strip()
    found = ANIMATE_DONE.search(text)
    if not found:
        return False, text
    return True, text[:found.start()].strip()


def wants_dictation(text):
    return bool(DICTATE.match(str(text or "")))


def dictation_end(text):
    """(ended, words): the words of a dictated piece, with the closing phrase cut off."""
    text = str(text or "").strip()
    found = DICTATION_DONE.search(text)
    if not found:
        return False, text
    return True, text[:found.start()].strip()


# Terminals paste with ctrl+shift+v: gvoice's own list, one regex per line.
TERMINALS = Path.home() / ".config" / "gvoice" / "terminal-classes"
TERMINAL_DEFAULT = r"kitty|alacritty|foot|wezterm|ghostty|konsole|terminal"


def _terminal(window_class):
    patterns = [TERMINAL_DEFAULT]
    try:
        patterns += [line.strip() for line in TERMINALS.read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        pass
    for pattern in patterns:
        try:
            if re.search(pattern, window_class or "", re.I):
                return True
        except re.error:
            continue
    return False


def _clipboard():
    """(mime, bytes) on the clipboard now, or None."""
    try:
        types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2).stdout.split()
        if not types:
            return None
        data = subprocess.run(["wl-paste", "--no-newline", "--type", types[0]], capture_output=True, timeout=3).stdout
        return types[0], data
    except (OSError, subprocess.SubprocessError):
        return None


def type_text(text):
    """(ok, why): puts text into the focused window as one paste, then gives the clipboard back.

    Typed key by key with wtype, a dictation lost the last letter and the full stop of every
    sentence in T3 (2026-11-11). A paste is one keystroke and carries every character, as
    gvoice does it. Terminals get ctrl+shift+v."""
    text = str(text or "")
    if not text:
        return True, ""
    ok, out = _run(["hyprctl", "-j", "activewindow"])
    try:
        window_class = str((json.loads(out) or {}).get("class") or "") if ok and out else ""
    except ValueError:
        window_class = ""
    shift = _terminal(window_class)
    held = _clipboard()
    try:
        subprocess.run(["wl-copy", "--", text], check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"could not use the clipboard: {error}"
    chords = ([["wtype", "-M", "ctrl", "-M", "shift", "-k", "v", "-m", "shift", "-m", "ctrl"],
               ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]] if shift else
              [["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]])
    pasted, why = False, ""
    for chord in chords:
        ok, why = _run(chord)
        if ok:
            pasted = True
            break
    # The app reads the clipboard after the keystroke lands; give it that moment first.
    time.sleep(0.35)
    try:
        if held is None:
            subprocess.run(["wl-copy", "--clear"], timeout=3)
        else:
            subprocess.run(["wl-copy", "--type", held[0]], input=held[1], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    return (True, "") if pasted else (False, f"pasting failed: {why[:120]}")


def _closed_info(address):
    """What a window is, read before it closes, so it can be started again."""
    ok, out = _run(["hyprctl", "-j", "clients"])
    try:
        clients = json.loads(out) if ok and out else []
    except ValueError:
        clients = []
    for c in clients:
        if c.get("address") == address:
            cmd = []
            try:
                cmd = [part for part in Path(f"/proc/{int(c.get('pid'))}/cmdline").read_bytes().decode().split("\0") if part]
            except (OSError, ValueError, TypeError):
                pass
            return {"class": c.get("class") or "", "initial": c.get("initialClass") or "",
                    "title": c.get("title") or "", "cmd": cmd}
    return None


def reopen(last=None):
    """(ok, words): starts an app closed by voice again (the newest one when last is None),
    by its desktop entry if it has one, else by the command line it ran with."""
    if last is None:
        entry = next((e for e in reversed(HISTORY) if e.get("closed") and not e.get("undone")), None)
        if entry is None:
            return False, "I have not closed anything yet"
        entry["undone"] = True
        last = entry["closed"]
    for name in (last["initial"], last["class"]):
        entry = apps.find_app(name.split(".")[-1]) if name else None
        if entry is not None:
            ok, why = apps._launch(["gtk-launch", entry["id"]])
            if ok:
                return True, f"reopened {entry['name']}"
    if last["cmd"]:
        try:
            subprocess.Popen(last["cmd"], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, cwd=os.path.expanduser("~"))
            return True, f"reopened {last['class'] or 'it'}"
        except OSError as error:
            return False, f"could not start {last['class']}: {error}"
    return False, f"I don't know how to start {last['class'] or 'that'} again"


SPOKEN_KEYS = {
    "control": "ctrl", "ctrl": "ctrl", "shift": "shift", "alt": "alt", "option": "alt", "super": "super",
    "windows": "super", "meta": "super", "command": "super", "enter": "enter", "return": "enter",
    "escape": "esc", "esc": "esc", "tab": "tab", "space": "space", "backspace": "backspace",
    "delete": "delete", "up": "up", "down": "down", "left": "left", "right": "right", "home": "home",
    "end": "end", "pageup": "pageup", "pagedown": "pagedown",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9",
}
PRESS = re.compile(r"^\W*(?:diablo\W+)?(?:press|hit|push|do)\s+(.+?)\W*$", re.I)


def pressed_combo(text):
    """"Press control 1", "hit ctrl shift t": the combo for desktop.press_key, or None."""
    found = PRESS.match(str(text or ""))
    if not found:
        return None
    words = re.findall(r"[a-z0-9]+", found.group(1).lower().replace("page up", "pageup").replace("page down", "pagedown"))
    words = [w for w in words if w not in ("plus", "and", "the", "key", "button")]
    parts = []
    for word in words:
        if word in SPOKEN_KEYS:
            parts.append(SPOKEN_KEYS[word])
        elif re.fullmatch(r"f(1[0-2]|[1-9])|[a-z]|[0-9]", word):
            parts.append(word)
        else:
            return None
    return "+".join(parts) if parts else None


# "Go to the YouTube tab": the browser's own tab search, so any open tab can be named.
TAB = re.compile(r"^\W*(?:diablo\W+)?(?:go|switch|jump|take me)(?:\s+back)?\s+to\s+(?:the\s+|my\s+)?(.+?)\s+tab\W*$", re.I)
BROWSERS = re.compile(r"zen|firefox|librewolf|floorp", re.I)


def tab_name(text):
    found = TAB.match(str(text or ""))
    if not found:
        return None
    name = found.group(1).strip()
    return None if name.lower() in ("next", "previous", "last", "new", "other") else name


def browser_tab(name):
    """(ok, words): finds a tab by name with the address bar's % tab search and opens it."""
    ok, out = _run(["hyprctl", "-j", "clients"])
    try:
        clients = json.loads(out) if ok and out else []
    except ValueError:
        clients = []
    browser = next((c for c in clients if BROWSERS.search(str(c.get("class") or ""))), None)
    if browser is None:
        return False, "no browser is open"
    before = _active()
    ok, why = _focus(browser["address"])
    if not ok:
        return False, f"could not bring the browser forward: {why}"
    for step in (lambda: desktop.press_key("ctrl+l"), lambda: desktop.type_text(f"% {name}")):
        out = step()
        if out != "ok":
            return False, out
    time.sleep(0.45)
    out = desktop.press_key("enter")
    if out != "ok":
        return False, out
    remember("browser_tab", f"went to the {name} tab", before)
    return True, f"went to the {name} tab"


# While the mode is on, a line that names it with any stop word in any order ends it ("the
# voice control could stop"), and so does a short line that is only a way of saying done.
MODE_WORDS = re.compile(r"\b(voice|speech|command|accessibility)\s*(control|mode|loop)|\bcontrol mode\b", re.I)
STOP_WORDS = re.compile(r"\b(stop|stopped|off|end|exit|quit|leave|close|done|finish|finished|enough|cancel|over)\b", re.I)
BARE_STOP = re.compile(
    r"^\W*(?:diablo\W+)?(?:stop|exit|quit|enough|done|i'?m done|i am done|we'?re done|that'?s it|that is it|"
    r"that'?s all|that is all|over and out|i'?m finished|finished|end it|i'?m paid)\W*$", re.I)


def loop_intent(text, active=False):
    """"start", "stop" or None for one heard line. active: the mode is on now, so stopping
    it is understood far more loosely."""
    text = str(text or "")
    if LOOP_STOP.search(text):
        return "stop"
    if active and (BARE_STOP.match(text) or (MODE_WORDS.search(text) and STOP_WORDS.search(text))):
        return "stop"
    if LOOP_START.search(text):
        return "start"
    return None


def _run(cmd, timeout=4.0):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)[:160]
    out = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, out.strip()


def desktop_state():
    """{"windows": [...], "players": [...]} for the hub: the focused window first."""
    windows, players = [], []
    ok, out = _run(["hyprctl", "-j", "activewindow"])
    try:
        focus = (json.loads(out) or {}).get("address") if ok and out else None
    except ValueError:
        focus = None
    ok, out = _run(["hyprctl", "-j", "clients"])
    try:
        clients = json.loads(out) if ok and out else []
    except ValueError:
        clients = []
    for c in clients if isinstance(clients, list) else []:
        if not c.get("mapped", True) or c.get("hidden") or not c.get("address"):
            continue
        windows.append({
            "address": c["address"],
            "class": str(c.get("class") or c.get("initialClass") or ""),
            "title": " ".join(str(c.get("title") or "").split())[:80],
            "workspace": (c.get("workspace") or {}).get("id"),
            "focused": c["address"] == focus,
        })
    windows.sort(key=lambda w: not w["focused"])
    ok, out = _run(["playerctl", "-l"], timeout=2.0)
    if ok:
        players = [line.strip() for line in out.splitlines() if PLAYER.match(line.strip())]
    return {"windows": windows[:WINDOWS_MAX], "players": players[:8]}


def _focus(address):
    ok, out = apps._dispatch(f'hl.dsp.focus({{ window = "address:{address}" }})')
    if not ok:
        return False, out
    ok, active = apps._query(["hyprctl", "-j", "activewindow"], as_json=True)
    if not ok or (active or {}).get("address") != address:
        return False, "that window would not come forward"
    return True, ""


# The last HISTORY_MAX things Diablo did on the desktop, newest last, each with what undoes
# it, so "go back" or "undo that" walks them back one by one. Kept across restarts.
HISTORY_MAX = 30
HISTORY_FILE = Path.home() / ".local" / "state" / "diablo" / "desktop-history.json"
HISTORY = collections.deque(maxlen=HISTORY_MAX)
# An act older than this is not what "undo" means; the app's own ctrl+z is.
UNDO_FRESH_S = 300
FLIP = {
    "next_tab": "previous_tab", "previous_tab": "next_tab", "new_tab": "close_tab", "close_tab": "reopen_tab",
    "reopen_tab": "close_tab", "play": "pause", "pause": "play", "next": "previous", "previous": "next",
    "volume_up": "volume_down", "volume_down": "volume_up", "mute": "unmute", "unmute": "mute",
    "fullscreen": "fullscreen", "scroll_up": "scroll_down", "scroll_down": "scroll_up",
    "page_up": "page_down", "page_down": "page_up",
}
# Acts that send or close something: never run off half a sentence (see ears.early_sure).
SURE_ONLY = ("close_window", "close_tab", "enter")
# Moves that change where he is: undone by putting the focus back.
MOVES = ("focus_window", "focus_left", "focus_right", "focus_up", "focus_down", "workspace", "open_thread",
         "terminal", "new_window", "browser_tab")
# Edits in the app: undone by the app's own undo.
EDITS = ("type", "dictation", "paste", "cut", "delete_word", "backspace")


def _load_history():
    try:
        rows = json.loads(HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            HISTORY.append(row)


def _save_history():
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(HISTORY)))
        tmp.replace(HISTORY_FILE)
    except OSError:
        pass


_load_history()


def _active():
    ok, out = _run(["hyprctl", "-j", "activewindow"])
    try:
        active = json.loads(out) if ok and out else {}
    except ValueError:
        active = {}
    return {"address": (active or {}).get("address") or "",
            "workspace": ((active or {}).get("workspace") or {}).get("id")}


def remember(action, did, before=None, **extra):
    """Puts one done thing in the history."""
    HISTORY.append({"action": action, "did": did, "at": time.time(), "before": before or {}, **extra})
    _save_history()


def recent(limit=5):
    """The newest things done, oldest first, as short lines: context for the hub."""
    return [e.get("did") or e.get("action") for e in list(HISTORY)[-limit:] if not e.get("undone")]


RECENT_S = 600


def recent_lines(limit=4, now=None):
    """What Diablo did by voice in the last ten minutes, oldest first, with how long ago, and
    repeats folded: "turned the sound down 3 times, last 4 s ago". Jev and Claude read it, so
    "down to 40" after three quieters is about the volume."""
    now = time.time() if now is None else now
    rows = [e for e in HISTORY if not e.get("undone") and now - float(e.get("at") or 0) <= RECENT_S]
    folded = []
    for e in rows:
        did = e.get("did") or e.get("action")
        if folded and folded[-1][0] == did:
            folded[-1][1] += 1
            folded[-1][2] = e
        else:
            folded.append([did, 1, e])
    out = []
    for did, times, last in folded[-limit:]:
        ago = int(now - float(last.get("at") or now))
        out.append(f"{did}{f' {times} times' if times > 1 else ''}, {'last ' if times > 1 else ''}{ago} s ago")
    return out


def last_action(within_s=120, now=None):
    now = time.time() if now is None else now
    entry = next((e for e in reversed(HISTORY) if not e.get("undone")), None)
    if entry is None or now - float(entry.get("at") or 0) > within_s:
        return None
    return entry.get("action")


AGAIN_FRESH_S = 120


def again():
    """(ok, words): does the newest thing once more: "again", "more", "a bit more". Only the
    very newest, and only within two minutes: "again" an hour after closing a tab must not press
    ctrl+w."""
    entry = next((e for e in reversed(HISTORY) if not e.get("undone")), None)
    if entry is None or time.time() - float(entry.get("at") or 0) > AGAIN_FRESH_S:
        return False, "there is nothing recent to do again"
    if not isinstance(entry.get("act"), dict):
        return False, f"I can't do {entry.get('did') or 'that'} again"
    return run(dict(entry["act"]))


TENS = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "a hundred": 100, "one hundred": 100,
        "zero": 0, "five": 5, "fifteen": 15, "twenty five": 25, "seventy five": 75, "half": 50}
VOLUME_TO = re.compile(
    r"^\W*(?:diablo\W+)?(?:(?:set|put|turn|bring|take)\s+)?(?:the\s+)?(?:volume|sound|music|it|that)?\s*"
    r"(?:(?:all\s+the\s+way\s+)?(?:down|up)\s+)?(?:to|at)\s+"
    r"(\d{1,3}|ten|twenty five|twenty|thirty|forty|fourty|fifty|sixty|seventy five|seventy|eighty|ninety|"
    r"a hundred|one hundred|hundred|zero|five|fifteen|half)\s*(?:%|percent|per cent)?\W*$", re.I)
SOUND_WORDS = re.compile(r"\b(volume|sound|music|loud|louder|quiet|quieter|audio)\b", re.I)


def volume_target(text):
    """The volume in percent a line sets, or None: "down to 40", "volume 30 percent", "set it
    to fifty". A bare "to 40" counts only right after a sound change, since the history is the
    only thing that says "it" is the sound and not the lights."""
    text = str(text or "")
    found = VOLUME_TO.match(text)
    if not found:
        found = re.match(r"^\W*(?:diablo\W+)?(?:volume|sound)\s+(\d{1,3})\s*(?:%|percent)?\W*$", text, re.I)
    if not found:
        return None
    word = found.group(1).lower()
    level = int(word) if word.isdigit() else TENS.get(word)
    if level is None or not 0 <= level <= 150:
        return None
    if SOUND_WORDS.search(text) or last_action() in ("volume_up", "volume_down", "mute", "unmute", "volume_set"):
        return level
    return None


def set_volume(level):
    """(ok, words): the default output to level percent, never above 100."""
    level = max(0, min(100, int(level)))
    before = _active()
    ok, out = _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{level}%"])
    if not ok:
        return False, f"the sound control failed: {out[:120]}"
    remember("volume_set", f"set the sound to {level} percent", before,
             act={"action": "volume_set", "level": level})
    return True, f"set the sound to {level} percent"


def undo_last():
    """(ok, words): walks the newest thing Diablo did back. With nothing recent to undo, the
    focused app's own ctrl+z runs instead."""
    entry = next((e for e in reversed(HISTORY) if not e.get("undone")), None)
    if entry is None or time.time() - float(entry.get("at") or 0) > UNDO_FRESH_S:
        out = desktop.press_key("ctrl+z")
        return (True, "undid it in the app") if out == "ok" else (False, out)
    entry["undone"] = True
    _save_history()
    action, before = entry.get("action"), entry.get("before") or {}
    if entry.get("closed"):
        return reopen(entry["closed"])
    if action == "move_to_workspace" and before.get("workspace") and ADDRESS.match(str(before.get("address") or "")):
        ok, out = apps._dispatch(
            f'hl.dsp.window.move({{ workspace = {int(before["workspace"])}, window = "address:{before["address"]}" }})')
        return (True, f"put it back on workspace {before['workspace']}") if ok else (False, out)
    if action in MOVES and ADDRESS.match(str(before.get("address") or "")):
        ok, why = _focus(before["address"])
        return (True, "went back") if ok else (False, f"could not go back: {why}")
    if action in FLIP:
        ok, why = _run_act({"action": FLIP[action]})
        return (True, f"undid: {entry.get('did') or action}") if ok else (False, why)
    if action in EDITS:
        out = desktop.press_key("ctrl+z")
        return (True, f"undid: {entry.get('did') or action}") if out == "ok" else (False, out)
    return False, f"I can't undo {entry.get('did') or action}"


def run(act):
    """(ok, words): runs one act from the hub and keeps it in the history. words say what
    happened, or what failed, in plain terms."""
    action = str((act or {}).get("action") or "")
    if action in ("undo", "go_back"):
        return undo_last()
    if action == "reopen_window":
        return reopen()
    if action == "again":
        return again()
    if action == "volume_set":
        return set_volume(act.get("level") or 50)
    before = _active()
    closed = _closed_info(str(act.get("window") or "")) if action == "close_window" else None
    ok, why = _run_act(act)
    if ok:
        remember(action, why or act.get("did") or action, before,
                 act={k: v for k, v in act.items() if k != "did"}, **({"closed": closed} if closed else {}))
    return ok, why


def _run_act(act):
    action = str((act or {}).get("action") or "")
    if action in ("close_window", "focus_window"):
        address = str(act.get("window") or "")
        if not ADDRESS.match(address):
            return False, "I did not get a window to act on"
        name = act.get("name") or "the window"
        ok, why = _focus(address)
        if not ok:
            return False, f"I could not find {name}: {why}"
        if action == "focus_window":
            return True, ""
        ok, out = apps._dispatch("hl.dsp.window.close()")
        return (True, "") if ok else (False, f"closing {name} failed: {out}")
    if action == "open_thread":
        link = str(act.get("link") or "")
        if not link.startswith("t3code://threads/"):
            return False, "I did not get a thread to open"
        ok, why = apps._launch(["xdg-open", link])
        return (True, f"opened {act.get('name') or 'the thread'}") if ok else (False, why)
    if action in ("workspace", "move_to_workspace"):
        call = apps.switch_workspace if action == "workspace" else apps.move_to_workspace
        ok, out = call({"number": act.get("workspace")}, None)
        return (True, "") if ok else (False, out)
    if action in ("play", "pause", "next", "previous"):
        player = str(act.get("player") or "")
        if player and not PLAYER.match(player):
            player = ""
        if action == "pause" and not player:
            cmd = ["playerctl", "-a", "pause"]
        else:
            cmd = ["playerctl"] + (["-p", player] if player else []) + [action]
        ok, out = _run(cmd)
        if "No players found" in out:
            return False, "nothing is playing anywhere"
        return (True, "") if ok else (False, f"the player said: {out[:120]}")
    if action in KEYS:
        key = KEYS[action]
        if isinstance(key, tuple) and key[0] == "scroll":
            out = desktop.mouse_scroll(key[1])
        elif isinstance(key, tuple):
            out = desktop.mouse_click(key[1], key[2])
        else:
            out = desktop.press_key(key)
        return (True, "") if out == "ok" else (False, out)
    sink = "@DEFAULT_AUDIO_SINK@"
    sound = {
        "volume_up": ["wpctl", "set-volume", "-l", "1.0", sink, VOLUME_STEP + "+"],
        "volume_down": ["wpctl", "set-volume", sink, VOLUME_STEP + "-"],
        "mute": ["wpctl", "set-mute", sink, "1"],
        "unmute": ["wpctl", "set-mute", sink, "0"],
    }
    if action in sound:
        ok, out = _run(sound[action])
        return (True, "") if ok else (False, f"the sound control failed: {out[:120]}")
    return False, f"I don't know how to {action or 'do that'} here"
