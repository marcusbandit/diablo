import json
import logging
import os
import subprocess

log = logging.getLogger("diablo.desktop")

KEYCODES = {
    "super": 125, "ctrl": 29, "leftctrl": 29, "rightctrl": 97,
    "alt": 56, "leftalt": 56, "rightalt": 100, "altgr": 100,
    "shift": 42, "leftshift": 42, "rightshift": 54,
    "enter": 28, "return": 28, "esc": 1, "escape": 1, "space": 57, "tab": 15,
    "backspace": 14, "delete": 119, "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
}

BUTTONS = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}


def _socket():
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.environ.get("YDOTOOL_SOCKET") or f"{runtime}/.ydotool_socket"


def _run(args, timeout=15):
    env = {**os.environ, "YDOTOOL_SOCKET": _socket()}
    try:
        done = subprocess.run(
            ["ydotool", *args], env=env, capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return f"ydotool failed: {error}"
    if done.returncode != 0:
        return f"ydotool failed: {done.stderr.strip()[:200]}"
    return "ok"


def mouse_move(x, y):
    return _run(["mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))])


def mouse_click(button="left", times=1):
    code = BUTTONS.get(button or "left")
    if code is None:
        return f"unknown button {button}"
    return _run(["click", *([code] * max(1, min(int(times), 3)))])


def mouse_scroll(clicks):
    clicks = max(-15, min(15, int(clicks)))
    return _run(["mousemove", "--wheel", "-x", "0", "-y", str(clicks)])


def type_text(text):
    text = str(text)
    if not text:
        return "nothing to type"
    if len(text) > 2000:
        return "too long for one keystroke burst"
    return _run(["type", "-D", "10", text])


def press_key(combo):
    parts = [p.strip().lower() for p in str(combo).split("+") if p.strip()]
    if not parts:
        return "empty combo"
    codes = []
    for part in parts:
        code = KEYCODES.get(part)
        if code is None and part.isdigit():
            code = int(part)
        if code is None:
            return f"unknown key {part}"
        codes.append(code)
    sequence = []
    for code in codes[:-1]:
        sequence.extend([f"{code}:1"])
    sequence.append(f"{codes[-1]}:1")
    sequence.append(f"{codes[-1]}:0")
    for code in reversed(codes[:-1]):
        sequence.append(f"{code}:0")
    return _run(["key", "-d", "60", *sequence])


def list_windows():
    done = subprocess.run(
        ["hyprctl", "-j", "clients"], capture_output=True, text=True, timeout=10
    )
    if done.returncode != 0:
        return f"hyprctl failed: {done.stderr[:200]}"
    lines = []
    for client in json.loads(done.stdout or "[]"):
        if client.get("mapped"):
            lines.append(
                f"{client.get('class', '?')} | {client.get('title', '?')[:60]}"
                f" | ws {client.get('workspace', {}).get('id', '?')}"
            )
    return "\n".join(lines) or "(no mapped windows)"


def focus_window(title):
    # Hyprland 0.56 only takes the Lua form; the old focuswindow syntax is rejected.
    done = subprocess.run(
        ["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "title:{title}" }})'],
        capture_output=True, text=True, timeout=10,
    )
    if done.returncode != 0 or done.stdout.strip() != "ok":
        return f"no window matching title:{title}"
    return "ok"
