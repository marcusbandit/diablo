"""diablo-hands: Diablo's hands on the desktop, one command per process, one JSON object out.

    front                                   the focused window and its monitor's scale
    windows                                 every window, front to back
    ui [--window W] [--max N]               the window's controls and text, through AT-SPI
    find-text TEXT [--window W] [--all]     where visible text is: AT-SPI first, then OCR
    act [--window W] --steps JSON           a whole plan in one call

W is front (the default), a Hyprland address (0x...) or part of a class or title. Every box is
in global logical screen pixels, the same space hyprctl reports cursor positions in. A failure
prints {"error": ...} and exits 1; a click on something risky prints {"needs_confirm": ...} and
exits 3. AT-SPI comes from python-gobject, so the launcher runs this under the system Python;
without it the AT-SPI parts report a hint and OCR still works. docs/hands.md has the contract.
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import window

MAX_STEPS = 30
MAX_WAIT_S = 5.0
WALK_NODES = 3000
WALK_DEPTH = 60
WALK_SECONDS = 2.5
WALK_CHILDREN = 400
TEXT_CHARS = 4000
ACT_CONTROLS = 60
ACT_TEXT_CHARS = 800
OCR_MATCH = 0.8
EXIT_CONFIRM = 3

KINDS = ("focus", "click_text", "click", "click_at", "type", "key", "fill", "scroll", "open", "wait")
BUTTONS = ("left", "right", "middle")
DIRECTIONS = ("up", "down", "left", "right")
ADDRESS_KEYS = ("ctrl+l", "alt+d", "f6")
RETURN_KEYS = ("return", "enter", "kp_enter")
SETTLE = {"click_text": 0.3, "click": 0.3, "click_at": 0.3, "key": 0.2, "type": 0.1, "fill": 0.1,
          "scroll": 0.2, "open": 0.5, "focus": 0.15}

RISKY = re.compile(
    r"\b(delete|remove|pay|pay now|buy|buy now|purchase|send|submit order|place order|sign out|"
    r"log out|logout|uninstall|format|transfer|"
    r"slet|fjern|betal|køb|indsend|bestil|afgiv ordre|log ud|afinstaller|formater|overfør)\b",
    re.I,
)
URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://\S+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?)$", re.I)

CONTROL_ROLES = {
    "PUSH_BUTTON": "button", "PUSH_BUTTON_MENU": "button", "TOGGLE_BUTTON": "toggle", "CHECK_BOX": "checkbox",
    "RADIO_BUTTON": "radio", "COMBO_BOX": "combo", "ENTRY": "field", "PASSWORD_TEXT": "password",
    "SPIN_BUTTON": "spin", "SLIDER": "slider", "LINK": "link", "MENU_ITEM": "menuitem",
    "CHECK_MENU_ITEM": "menuitem", "RADIO_MENU_ITEM": "menuitem", "MENU": "menu", "PAGE_TAB": "tab",
    "LIST_ITEM": "item", "TREE_ITEM": "item", "SWITCH": "switch", "TERMINAL": "terminal",
}
TEXT_ROLES = ("LABEL", "HEADING", "PARAGRAPH", "STATIC", "TEXT", "CAPTION", "SECTION", "TABLE_CELL",
              "STATUS_BAR", "BLOCK_QUOTE", "ARTICLE", "DESCRIPTION_TERM", "DESCRIPTION_VALUE", "NOTIFICATION",
              "ALERT", "TOOL_TIP")
DOCUMENT_ROLES = ("DOCUMENT_WEB", "DOCUMENT_FRAME")
POPUP_ROLES = ("MENU", "POPUP_MENU", "TOOL_TIP", "WINDOW")
STATE_NAMES = (("FOCUSED", "focused"), ("CHECKED", "checked"), ("SELECTED", "selected"), ("EXPANDED", "expanded"),
               ("PRESSED", "pressed"), ("EDITABLE", "editable"), ("READ_ONLY", "readonly"), ("REQUIRED", "required"))
PRESS_ACTIONS = ("click", "press", "activate", "jump", "toggle", "open", "select", "switch", "check", "uncheck")
EDITABLE_ROLES = ("field", "password", "combo", "spin", "terminal")


class HandsError(Exception):
    pass


class NeedsConfirm(Exception):
    pass


def prepare_env(env=None):
    """Session sockets for ssh callers: Wayland, Hyprland and the session bus AT-SPI rides on."""
    env = os.environ if env is None else env
    window.fill_session(env)
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    bus = Path(runtime, "bus")
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and bus.exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


def _run(cmd, timeout=10.0, data=None, text=True):
    try:
        return subprocess.run(cmd, capture_output=True, text=text, timeout=timeout, input=data, env=os.environ)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HandsError(f"{cmd[0]} failed: {error}") from error


def hypr(*args):
    done = _run(["hyprctl", "-j", *args], timeout=5)
    if done.returncode != 0:
        raise HandsError(f"hyprctl {' '.join(args)} failed: {(done.stderr or done.stdout).strip()[:160]}")
    try:
        return json.loads(done.stdout or "null")
    except ValueError as error:
        raise HandsError(f"hyprctl {' '.join(args)} gave no json") from error


def logical(monitor):
    """(x, y, w, h) of a monitor in logical pixels; rotated outputs swap width and height."""
    scale = float(monitor.get("scale") or 1) or 1.0
    w, h = int(monitor.get("width") or 0), int(monitor.get("height") or 0)
    if int(monitor.get("transform") or 0) % 2:
        w, h = h, w
    return int(monitor.get("x") or 0), int(monitor.get("y") or 0), round(w / scale), round(h / scale)


def short(client, monitors=()):
    """The fields a caller needs from one hyprctl client."""
    if not client or not client.get("address"):
        return None
    (x, y), (w, h) = client.get("at") or (0, 0), client.get("size") or (0, 0)
    workspace = client.get("workspace") or {}
    monitor = next((m for m in monitors if m.get("id") == client.get("monitor")), None)
    shown = {(m.get("activeWorkspace") or {}).get("id") for m in monitors}
    shown |= {(m.get("specialWorkspace") or {}).get("id") for m in monitors if (m.get("specialWorkspace") or {}).get("id")}
    out = {
        "address": client.get("address"), "class": client.get("class") or "", "title": client.get("title") or "",
        "pid": client.get("pid"), "x": x, "y": y, "w": w, "h": h,
        "monitor": monitor.get("name") if monitor else client.get("monitor"),
        "scale": float(monitor.get("scale") or 1) if monitor else 1.0,
        "workspace": workspace.get("name") or workspace.get("id"),
    }
    if monitors:
        out["visible"] = workspace.get("id") in shown
    if client.get("floating"):
        out["floating"] = True
    if client.get("fullscreen"):
        out["fullscreen"] = True
    return out


def locked():
    """True or False for the screen lock, or None when hyprctl cannot say."""
    try:
        state = hypr("locked")
    except HandsError:
        return None
    value = state.get("locked") if isinstance(state, dict) else None
    return value if isinstance(value, bool) else None


def front():
    active = hypr("activewindow") or {}
    monitors = hypr("monitors") or []
    found = short(active, monitors)
    if found is None:
        focused = next((m for m in monitors if m.get("focused")), None)
        return {"window": None, "monitor": focused.get("name") if focused else None,
                "scale": float(focused.get("scale") or 1) if focused else 1.0}
    return found


def windows():
    rows = [c for c in hypr("clients") or [] if c.get("mapped")]
    monitors = hypr("monitors") or []
    rows.sort(key=lambda c: c["focusHistoryID"] if int(c.get("focusHistoryID", -1)) >= 0 else 1 << 30)
    out = []
    for c in rows:
        s = short(c, monitors)
        s["title"] = s["title"][:80]
        out.append(s)
    return out


def resolve(target):
    """(client, monitors) for front, an address, or part of a class or title."""
    words = " ".join(str(target or "").split())
    monitors = hypr("monitors") or []
    if words.lower() in ("", "front", "focused", *window.FOCUSED):
        active = hypr("activewindow") or {}
        if not active.get("address"):
            raise HandsError("no window has focus")
        return active, monitors
    rows = [c for c in hypr("clients") or [] if c.get("mapped")]
    if re.match(r"^0x[0-9a-f]+$", words, re.I):
        hit = next((c for c in rows if str(c.get("address")).lower() == words.lower()), None)
        if hit is None:
            raise HandsError(f"no window at {words}")
        return hit, monitors
    lowered, squashed = words.lower(), words.lower().replace(" ", "")
    for field in ("class", "initialClass", "title"):
        for c in sorted(rows, key=lambda c: c.get("focusHistoryID", 1 << 30)):
            value = str(c.get(field) or "").lower()
            if lowered in value or squashed in value.replace(" ", ""):
                return c, monitors
    raise HandsError(f"no window matching {words}; open: " + ", ".join(sorted({str(c.get('class') or '?') for c in rows})))


def frame_mapper(frame, win, scale=1.0):
    """A function from AT-SPI boxes to global logical boxes, and how the app reports them.

    frame is the app's own box for its top level window, in whatever space it uses: global (X11
    and some toolkits), window relative (most Wayland clients), or physical pixels. win is the
    window as Hyprland has it. The frame is fitted onto the window, so all three come out right;
    a frame larger than the window by a shadow margin is centred on it. A zero frame means the app
    gives no positions at all, and every box is then None."""
    fx, fy, fw, fh = frame
    wx, wy, ww, wh = win
    if fw <= 0 or fh <= 0 or ww <= 0 or wh <= 0:
        return (lambda box: None), "none"
    k = 1.0
    if scale and scale != 1 and abs(fw / scale - ww) <= max(4, ww * 0.03) and abs(fw - ww) > max(4, ww * 0.03):
        k = 1.0 / scale
    dx, dy = (fw * k - ww) / 2, (fh * k - wh) / 2
    if abs(dx) > ww * 0.25 or abs(dy) > wh * 0.25:
        dx = dy = 0.0
    if k != 1.0:
        mode = "physical"
    elif abs(fx - wx) <= 2 and abs(fy - wy) <= 2 and (wx, wy) != (0, 0):
        mode = "global"
    else:
        mode = "window"

    def mapped(box):
        x, y, w, h = box
        if w <= 0 or h <= 0 or (x, y, w, h) == (0, 0, 0, 0):
            return None
        gx, gy = wx + (x - fx) * k - dx, wy + (y - fy) * k - dy
        gw, gh = w * k, h * k
        left, top = max(gx, wx), max(gy, wy)
        right, bottom = min(gx + gw, wx + ww), min(gy + gh, wy + wh)
        if right - left < 1 or bottom - top < 1:
            return None
        return [round(left), round(top), round(right - left), round(bottom - top)]

    return mapped, mode


def clean(text):
    return " ".join(str(text or "").replace("\ufffc", " ").split())


def centre(box):
    return box[0] + box[2] // 2, box[1] + box[3] // 2


def norm(text):
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


def risky(text):
    return bool(RISKY.search(str(text or "")))


def _atspi():
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return None
    try:
        Atspi.set_timeout(1500, 5000)
    except Exception:
        pass
    return Atspi


def a11y_enabled():
    """org.a11y.Status IsEnabled and ScreenReaderEnabled, or None each when unreadable."""
    out = {}
    for name in ("IsEnabled", "ScreenReaderEnabled"):
        try:
            done = _run(["busctl", "--user", "get-property", "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", name], timeout=3)
            out[name] = done.stdout.strip().endswith("true") if done.returncode == 0 else None
        except HandsError:
            out[name] = None
    return out


NO_TREE_HINT = ("this window shows no controls through AT-SPI. Chromium and Electron apps expose them only when started with "
                "--force-renderer-accessibility or when org.a11y.Status ScreenReaderEnabled is on; find_on_screen still reads it by OCR")


class Node:
    __slots__ = ("acc", "role", "kind", "name", "box", "states", "value", "text", "id")

    def __init__(self, acc, role, kind, name, box, states):
        self.acc, self.role, self.kind, self.name, self.box, self.states = acc, role, kind, name, box, states
        self.value, self.text, self.id = None, None, None

    def control(self):
        out = {"id": self.id, "role": self.kind, "name": self.name[:120]}
        if self.value not in (None, ""):
            out["value"] = str(self.value)[:200]
        if self.states:
            out["states"] = self.states
        out["box"] = self.box
        return out


class Tree:
    """One walk of a window's accessibility tree, pruned to what is on screen."""

    def __init__(self, client, monitors, max_nodes=WALK_NODES, seconds=WALK_SECONDS):
        self.client, self.monitors = client, monitors
        self.win = short(client, monitors)
        self.controls, self.texts, self.items = [], [], []
        self.mode, self.app, self.hint, self.truncated = "none", None, None, False
        self.visited, self.max_nodes, self.deadline = 0, max_nodes, time.monotonic() + seconds
        self.Atspi = _atspi()
        if self.Atspi is None:
            self.hint = "AT-SPI is not available to this Python (python-gobject missing); OCR still works"
            return
        try:
            self._walk_window()
        except HandsError:
            raise
        except Exception as error:
            self.hint = f"AT-SPI failed: {str(error)[:160]}"
        if not self.controls and not self.texts and self.hint is None:
            self.hint = NO_TREE_HINT

    def _role(self, name):
        return getattr(self.Atspi.Role, name, None)

    def _frame(self):
        A, pid, title = self.Atspi, self.client.get("pid"), str(self.client.get("title") or "")
        desktop = A.get_desktop(0)
        frames = []
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            try:
                if app is None or app.get_process_id() != pid:
                    continue
                self.app = self.app or app
                for j in range(app.get_child_count()):
                    child = app.get_child_at_index(j)
                    if child is not None:
                        frames.append(child)
            except Exception:
                continue
        if not frames:
            return None
        named = [(f, f.get_name() or "") for f in frames]
        for test in (lambda n: n == title, lambda n: n and (n in title or title in n)):
            hit = next((f for f, n in named if test(n)), None)
            if hit is not None:
                return hit
        active = next((f for f in frames if f.get_state_set().contains(A.StateType.ACTIVE)), None)
        return active or frames[0]

    def _extents(self, acc, coord):
        e = acc.get_extents(coord)
        return e.x, e.y, e.width, e.height

    def _walk_window(self):
        A = self.Atspi
        frame = self._frame()
        if frame is None:
            self.hint = NO_TREE_HINT
            return
        coord = A.CoordType.SCREEN
        box = self._extents(frame, coord)
        if box[2] <= 0 or box[3] <= 0:
            coord = A.CoordType.WINDOW
            box = self._extents(frame, coord)
        self.coord = coord
        w = self.win
        self.map, self.mode = frame_mapper(box, (w["x"], w["y"], w["w"], w["h"]), w["scale"])
        self.embeds = []
        try:
            for relation in frame.get_relation_set():
                if relation.get_relation_type() == A.RelationType.EMBEDS:
                    self.embeds += [relation.get_target(i) for i in range(relation.get_n_targets())]
        except Exception:
            pass
        children = [frame.get_child_at_index(i) for i in range(min(frame.get_child_count(), WALK_CHILDREN))]
        self.showing_works = any(c is not None and c.get_state_set().contains(A.StateType.SHOWING) for c in children)
        self.control_roles = {r: k for n, k in CONTROL_ROLES.items() if (r := self._role(n)) is not None}
        self.text_roles = {r for n in TEXT_ROLES if (r := self._role(n)) is not None}
        self.document_roles = {r for n in DOCUMENT_ROLES if (r := self._role(n)) is not None}
        self.popup_roles = {r for n in POPUP_ROLES if (r := self._role(n)) is not None}
        self.text_role = self._role("TEXT")
        self.states = [(getattr(A.StateType, n), label) for n, label in STATE_NAMES]
        for child in children:
            if child is not None:
                self._visit(child, 1, top=True)

    def _visit(self, acc, depth, top=False, inside=False):
        A = self.Atspi
        if self.visited >= self.max_nodes or time.monotonic() > self.deadline or depth > WALK_DEPTH:
            self.truncated = True
            return
        self.visited += 1
        try:
            states = acc.get_state_set()
            if not states.contains(A.StateType.VISIBLE) or states.contains(A.StateType.DEFUNCT):
                return
            showing = states.contains(A.StateType.SHOWING)
            if self.showing_works and not showing:
                return
            role = acc.get_role()
            if top and role in self.popup_roles and not showing:
                return
            if role in self.document_roles and not inside:
                if self.embeds and not any(acc == e for e in self.embeds):
                    return
                inside = True
            raw = self._extents(acc, self.coord)
            if self.mode != "none" and (raw[2] <= 0 or raw[3] <= 0) and role in self.popup_roles:
                return
            box = self.map(raw)
            if self.mode != "none" and box is None and raw[2] > 0 and raw[3] > 0:
                return
            name = (acc.get_name() or "").strip()
            kind = self.control_roles.get(role)
            if kind is None and role == self.text_role and states.contains(A.StateType.EDITABLE):
                kind = "field"
            if kind is None and name and states.contains(A.StateType.FOCUSABLE) and role not in self.document_roles:
                kind = acc.get_role_name() or "control"
            if kind is not None:
                labels = [label for state, label in self.states if states.contains(state)]
                if not states.contains(A.StateType.ENABLED) and not states.contains(A.StateType.SENSITIVE):
                    labels.append("disabled")
                node = Node(acc, role, kind, name, box, labels)
                node.value = self._value(acc, kind)
                node.id = len(self.controls) + 1
                self.controls.append(node)
                if name or node.value:
                    self.items.append(node)
            elif role in self.text_roles:
                raw = "" if name else self._raw(acc)
                text = name or clean(raw)
                if text:
                    node = Node(acc, role, "text", text, box, [])
                    node.text = raw or None
                    self.items.append(node)
                    self.texts.append(text)
            count = acc.get_child_count()
        except Exception:
            return
        if count > WALK_CHILDREN:
            self.truncated = True
        for i in range(min(count, WALK_CHILDREN)):
            try:
                child = acc.get_child_at_index(i)
            except Exception:
                continue
            if child is not None:
                self._visit(child, depth + 1, inside=inside)

    def _raw(self, acc):
        try:
            if acc.get_text_iface() is None:
                return ""
            return self.Atspi.Text.get_text(acc, 0, min(self.Atspi.Text.get_character_count(acc), 2000)) or ""
        except Exception:
            return ""

    def _text(self, acc):
        return clean(self._raw(acc))

    def _value(self, acc, kind):
        try:
            if kind in ("field", "combo", "terminal") and acc.get_text_iface() is not None:
                return self._text(acc)[:200]
            if kind in ("slider", "spin") and acc.get_value_iface() is not None:
                value = acc.get_current_value()
                return int(value) if float(value).is_integer() else round(value, 2)
        except Exception:
            return None
        return None

    def text(self, limit=TEXT_CHARS):
        out, seen, total = [], set(), 0
        for piece in self.texts:
            if piece in seen:
                continue
            seen.add(piece)
            out.append(piece)
            total += len(piece) + 1
            if total >= limit:
                break
        return "\n".join(out)[:limit]

    def listing(self, max_controls, text_limit=TEXT_CHARS):
        out = {"window": self.win, "coords": self.mode, "controls": [n.control() for n in self.controls[:max_controls]],
               "text": self.text(text_limit)}
        if len(self.controls) > max_controls:
            out["more_controls"] = len(self.controls) - max_controls
        if self.truncated:
            out["truncated"] = True
        if self.hint:
            out["hint"] = self.hint
            if self.hint == NO_TREE_HINT:
                out["a11y"] = a11y_enabled()
        return out

    def control(self, control_id, name=None):
        """The control with this id from ui; with a name, the id must still carry it, else the control by that name."""
        node = self.controls[control_id - 1] if 0 < control_id <= len(self.controls) else None
        want = norm(name)
        if not want or (node is not None and want in norm(node.name)):
            return node
        return next((n for n in self.controls if norm(n.name) == want), None) or \
            next((n for n in self.controls if want in norm(n.name)), None)

    def at(self, x, y):
        hits = [n for n in self.controls if n.box and n.box[0] <= x < n.box[0] + n.box[2] and n.box[1] <= y < n.box[1] + n.box[3]]
        return min(hits, key=lambda n: n.box[2] * n.box[3]) if hits else None

    def range_box(self, node, start, end):
        try:
            e = node.acc.get_range_extents(start, end, self.coord)
            return self.map((e.x, e.y, e.width, e.height))
        except Exception:
            return None


def atspi_matches(tree, phrase):
    """Matches of phrase among the tree's names and texts, best first."""
    want = norm(phrase)
    if not want:
        return []
    found = []
    for node in tree.items:
        for label in (node.name, node.value if isinstance(node.value, str) else None):
            have = norm(label)
            if not have:
                continue
            if have == want:
                score = 1.0
            elif re.search(r"(^| )" + re.escape(want) + r"( |$)", have):
                score = 0.85 if len(have) <= len(want) * 3 else 0.75
            elif len(have) <= len(want) + 6 and difflib.SequenceMatcher(None, have, want).ratio() >= 0.85:
                score = 0.8
            else:
                continue
            box = node.box
            if node.text and score < 1.0:
                at = node.text.lower().find(str(phrase).strip().lower())
                if at >= 0:
                    box = tree.range_box(node, at, at + len(str(phrase).strip())) or box
            if box is None:
                continue
            if node.kind != "text":
                score += 0.05
            entry = {"text": str(label)[:80], "x": box[0], "y": box[1], "w": box[2], "h": box[3], "source": "atspi"}
            if node.kind != "text":
                entry["control_id"] = node.id
            found.append((score, box[2] * box[3], entry))
            break
    found.sort(key=lambda f: (-f[0], f[1]))
    out, seen = [], set()
    for _, _, entry in found:
        key = (entry["x"], entry["y"], entry["w"], entry["h"])
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def parse_tsv(tsv):
    """Word rows of tesseract's TSV output: text and its box in image pixels."""
    words = []
    lines = str(tsv or "").splitlines()
    for line in lines[1:] if lines and lines[0].startswith("level") else lines:
        cells = line.split("\t")
        if len(cells) < 12 or cells[0] != "5":
            continue
        text = cells[11].strip()
        try:
            left, top, width, height = (int(c) for c in cells[6:10])
            conf = float(cells[10])
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        words.append({"text": text, "left": left, "top": top, "width": width, "height": height, "conf": conf})
    return words


def rows_of(words):
    """Words grouped into lines by position, each line split where the gap is wider than a few letters."""
    rows = []
    for word in sorted(words, key=lambda w: (w["top"] + w["height"] / 2, w["left"])):
        middle = word["top"] + word["height"] / 2
        for row in rows:
            if abs(row["middle"] - middle) <= 0.6 * max(row["height"], word["height"]):
                row["words"].append(word)
                break
        else:
            rows.append({"middle": middle, "height": word["height"], "words": [word]})
    segments = []
    for row in rows:
        ordered = sorted(row["words"], key=lambda w: w["left"])
        current = [ordered[0]]
        for word in ordered[1:]:
            last = current[-1]
            gap = word["left"] - (last["left"] + last["width"])
            if gap > 2.5 * max(last["height"], word["height"], 8):
                segments.append(current)
                current = [word]
            else:
                current.append(word)
        segments.append(current)
    return segments


def phrase_matches(words, phrase, threshold=OCR_MATCH):
    """(score, words) for each place the phrase shows, best first, never two over one word."""
    want = norm(phrase)
    tokens = want.split()
    if not tokens:
        return []
    exact_only = len(want) <= 3
    candidates = []
    for segment in rows_of(words):
        cleaned = [norm(w["text"]) for w in segment]
        for size in sorted({max(1, len(tokens) - 1), len(tokens), len(tokens) + 1}):
            for start in range(0, len(segment) - size + 1):
                have = " ".join(c for c in cleaned[start:start + size] if c)
                if not have:
                    continue
                if have == want:
                    score = 1.0
                elif exact_only:
                    continue
                else:
                    score = difflib.SequenceMatcher(None, have, want).ratio()
                if score >= threshold:
                    candidates.append((score, -abs(size - len(tokens)), segment[start:start + size]))
    candidates.sort(key=lambda c: (-c[0], -c[1]))
    taken, out = set(), []
    for score, _, chosen in candidates:
        ids = {id(w) for w in chosen}
        if ids & taken:
            continue
        taken |= ids
        out.append((round(score, 3), chosen))
    return out


def union(words):
    left = min(w["left"] for w in words)
    top = min(w["top"] for w in words)
    right = max(w["left"] + w["width"] for w in words)
    bottom = max(w["top"] + w["height"] for w in words)
    return left, top, right - left, bottom - top


def to_global(box, region, image_size):
    """An image pixel box to global logical pixels, for a capture of region at image_size."""
    rx, ry, rw, rh = region
    iw, ih = image_size
    sx, sy = (iw / rw if rw else 1.0), (ih / rh if rh else 1.0)
    x, y, w, h = box
    return [round(rx + x / sx), round(ry + y / sy), max(1, round(w / sx)), max(1, round(h / sy))]


def ppm_size(data):
    """(width, height) from a binary PPM header."""
    fields, i = [], 2
    while len(fields) < 2 and i < len(data):
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while i < len(data) and data[i:i + 1] != b"\n":
                i += 1
            continue
        start = i
        while i < len(data) and not data[i:i + 1].isspace():
            i += 1
        fields.append(int(data[start:i]))
    return fields[0], fields[1]


def ocr_langs():
    done = _run(["tesseract", "--list-langs"], timeout=5)
    have = set((done.stdout or "").split())
    return "eng+dan" if {"eng", "dan"} <= have else "eng"


def ocr_region(client, monitors):
    """The region OCR reads: the window clipped to its monitor, or the focused monitor without a window."""
    if client:
        w = short(client, monitors)
        monitor = next((m for m in monitors if m.get("name") == w["monitor"]), None)
        mx, my, mw, mh = logical(monitor) if monitor else (w["x"], w["y"], w["w"], w["h"])
        left, top = max(w["x"], mx), max(w["y"], my)
        right, bottom = min(w["x"] + w["w"], mx + mw), min(w["y"] + w["h"], my + mh)
        if right - left < 4 or bottom - top < 4:
            raise HandsError("the window is off screen")
        return left, top, right - left, bottom - top
    focused = next((m for m in monitors if m.get("focused")), None) or (monitors[0] if monitors else None)
    if focused is None:
        raise HandsError("no monitor found")
    return logical(focused)


def ocr(region):
    """Every word on screen in region, boxes in image pixels, plus the image size."""
    x, y, w, h = region
    grab = _run(["grim", "-g", f"{x},{y} {w}x{h}", "-t", "ppm", "-"], timeout=10, text=False)
    if grab.returncode != 0 or not grab.stdout:
        raise HandsError(f"grim failed: {(grab.stderr or b'').decode(errors='replace').strip()[:160]}")
    size = ppm_size(grab.stdout)
    read = _run(["tesseract", "stdin", "stdout", "--psm", "11", "-l", ocr_langs(), "tsv"], timeout=30,
                data=grab.stdout, text=False)
    if read.returncode != 0:
        raise HandsError(f"tesseract failed: {(read.stderr or b'').decode(errors='replace').strip()[-160:]}")
    return parse_tsv(read.stdout.decode(errors="replace")), size


def ocr_matches(phrase, client, monitors):
    region = ocr_region(client, monitors)
    words, size = ocr(region)
    out = []
    for score, chosen in phrase_matches(words, phrase):
        x, y, w, h = to_global(union(chosen), region, size)
        out.append({"text": " ".join(word["text"] for word in chosen), "x": x, "y": y, "w": w, "h": h, "source": "ocr",
                    "score": score})
    return out


def find_text(phrase, target="front", every=False, tree=None):
    """{"matches": [...]} for phrase in the target window: AT-SPI names first, OCR when they miss."""
    if not str(phrase or "").strip():
        raise HandsError("say what text to find")
    try:
        client, monitors = resolve(target)
    except HandsError:
        if str(target or "front") not in ("", "front"):
            raise
        client, monitors = None, hypr("monitors") or []
    out = {"matches": []}
    if client:
        tree = tree or Tree(client, monitors)
        out["matches"] += atspi_matches(tree, phrase)
        out["window"] = {"address": client.get("address"), "class": client.get("class")}
    if out["matches"] and not every:
        return out
    state = locked()
    if state is None:
        out["ocr"] = "skipped: could not read whether the screen is locked"
        return out
    if state:
        out["locked"] = True
        out["ocr"] = "skipped: the screen is locked, so a capture would only show the lock screen"
        return out
    if client and short(client, monitors).get("visible") is False:
        out["ocr"] = "skipped: the window is not on a visible workspace"
        return out
    out["matches"] += ocr_matches(phrase, client, monitors)
    return out


def nearest(matches, anchors):
    if not anchors:
        return matches[0]
    def distance(m):
        cx, cy = m["x"] + m["w"] / 2, m["y"] + m["h"] / 2
        return min((cx - a["x"] - a["w"] / 2) ** 2 + (cy - a["y"] - a["h"] / 2) ** 2 for a in anchors)
    return min(matches, key=distance)


def validate(steps):
    """The plan, checked and normalised. Raises HandsError naming the first bad step."""
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError as error:
            raise HandsError(f"steps is not json: {error}") from error
    if isinstance(steps, dict):
        steps = [steps]
    if not isinstance(steps, list) or not steps:
        raise HandsError("steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise HandsError(f"at most {MAX_STEPS} steps in one plan")
    out = []
    for n, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            raise HandsError(f"step {n}: each step is an object")
        kind = str(raw.get("do") or "")
        if kind not in KINDS:
            raise HandsError(f"step {n}: do must be one of {', '.join(KINDS)}")
        step = {"do": kind}
        if raw.get("confirmed") is True:
            step["confirmed"] = True
        try:
            if kind == "click_text":
                step["text"] = _need_text(raw, "text")
                if raw.get("near"):
                    step["near"] = str(raw["near"])
                step["button"] = _button(raw)
                step["double"] = bool(raw.get("double"))
            elif kind == "click":
                step["id"] = int(raw["id"])
                if step["id"] < 1:
                    raise ValueError("id starts at 1")
                if raw.get("name"):
                    step["name"] = str(raw["name"])
                step["button"] = _button(raw)
                step["double"] = bool(raw.get("double"))
            elif kind == "click_at":
                step["x"], step["y"] = int(round(float(raw["x"]))), int(round(float(raw["y"])))
                step["button"] = _button(raw)
                step["double"] = bool(raw.get("double"))
            elif kind == "type":
                step["text"] = _need_text(raw, "text", strip=False)
            elif kind == "key":
                step["key"] = _need_text(raw, "key")
            elif kind == "fill":
                if not isinstance(raw.get("text"), str):
                    raise ValueError("text is needed")
                step["text"] = raw["text"]
                if raw.get("field"):
                    step["field"] = str(raw["field"])
            elif kind == "scroll":
                direction = str(raw.get("direction") or "down").lower()
                if direction not in DIRECTIONS:
                    raise ValueError(f"direction is one of {', '.join(DIRECTIONS)}")
                step["direction"] = direction
                step["amount"] = max(1, min(15, int(raw.get("amount") or 5)))
                if raw.get("x") is not None and raw.get("y") is not None:
                    step["x"], step["y"] = int(round(float(raw["x"]))), int(round(float(raw["y"])))
            elif kind == "open":
                step["target"] = _need_text(raw, "target")
            elif kind == "wait":
                seconds = float(raw.get("seconds", 1))
                if not 0 <= seconds <= MAX_WAIT_S:
                    raise ValueError(f"seconds is 0 to {MAX_WAIT_S:g}")
                step["seconds"] = seconds
        except (KeyError, TypeError, ValueError) as error:
            raise HandsError(f"step {n} ({kind}): {_why(error)}") from error
        out.append(step)
    return out


def _why(error):
    return f"{error.args[0]} is needed" if isinstance(error, KeyError) else str(error)


def _need_text(raw, key, strip=True):
    value = raw.get(key)
    if not isinstance(value, str) or not (value.strip() if strip else value):
        raise ValueError(f"{key} is needed")
    if len(value) > 5000:
        raise ValueError(f"{key} is too long")
    return value.strip() if strip else value


def _button(raw):
    button = str(raw.get("button") or "left").lower()
    if button not in BUTTONS:
        raise ValueError(f"button is one of {', '.join(BUTTONS)}")
    return button


def looks_like_url(text):
    return bool(URL.match(str(text or "").strip())) and " " not in str(text).strip()


def rewrite(steps):
    """(steps, note): a plan that only types an address into the address bar and presses Return
    becomes one open of that address, which is faster and lands in the default browser."""
    core = [s for s in steps if s["do"] not in ("focus", "wait")]
    if core and core[0]["do"] == "key" and core[0]["key"].lower().replace(" ", "") in ADDRESS_KEYS:
        core = core[1:]
    if core and core[0]["do"] == "click_text" and norm(core[0]["text"]) in ("address bar", "search or enter address",
                                                                          "search or type a url", "search with google or enter address"):
        core = core[1:]
    url = None
    if len(core) == 2 and core[0]["do"] in ("type", "fill") and core[1]["do"] == "key" and \
            core[1]["key"].lower() in RETURN_KEYS:
        url = core[0]["text"].strip()
    elif len(core) == 1 and core[0]["do"] == "type" and core[0]["text"].endswith("\n"):
        url = core[0]["text"].strip()
    if url and looks_like_url(url):
        return [{"do": "open", "target": url}], f"typing {url} into the address bar became an open of it"
    return steps, None


def _dispatch(lua, legacy):
    done = _run(["hyprctl", "dispatch", lua], timeout=5)
    if done.returncode == 0 and done.stdout.strip() == "ok":
        return True
    done = _run(["hyprctl", "dispatch", *legacy], timeout=5)
    return done.returncode == 0 and done.stdout.strip() == "ok"


def cursor():
    try:
        at = hypr("cursorpos") or {}
        return int(at["x"]), int(at["y"])
    except (HandsError, KeyError, TypeError, ValueError):
        return None


def move(x, y):
    if not _dispatch(f"hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }})", ["movecursor", str(int(x)), str(int(y))]):
        raise HandsError("the pointer would not move")


def click_point(x, y, button="left", double=False):
    from . import desktop

    move(x, y)
    time.sleep(0.04)
    said = desktop.mouse_click(button, 2 if double else 1)
    if said != "ok":
        raise HandsError(said)


def type_text(text):
    done = _run(["wtype", "--", text], timeout=30)
    if done.returncode == 0:
        return "typed"
    from . import commands

    ok, why = commands.type_text(text)
    if not ok:
        raise HandsError(why or "typing failed")
    return "pasted, wtype failed"


WTYPE_MODS = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "super": "logo", "win": "logo",
              "logo": "logo", "meta": "logo", "altgr": "altgr"}
WTYPE_KEYS = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "space",
              "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "up": "Up", "down": "Down", "left": "Left",
              "right": "Right", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next", "insert": "Insert"}


def press(key):
    """Presses a combo like ctrl+l or Return, or one of the voice loop's key names like select_all."""
    from . import desktop

    combo = str(key).strip()
    if "+" not in combo and combo.lower() not in desktop.KEYCODES and combo.lower() not in WTYPE_KEYS:
        from . import commands

        named = commands.KEYS.get(combo.lower())
        if isinstance(named, str):
            combo = named
    if desktop.press_key(combo) == "ok":
        return combo
    parts = [p.strip() for p in combo.split("+") if p.strip()] or ([combo] if combo == "+" else [])
    mods = [WTYPE_MODS.get(p.lower()) for p in parts[:-1]]
    if not parts or None in mods:
        raise HandsError(f"unknown key {combo}")
    last = parts[-1]
    if not mods and len(last) == 1:
        done = _run(["wtype", "--", last], timeout=5)
        if done.returncode != 0:
            raise HandsError(f"could not press {combo}")
        return combo
    last = WTYPE_KEYS.get(last.lower(), last if len(last) > 1 else last.lower())
    argv = ["wtype"]
    for mod in mods:
        argv += ["-M", mod]
    argv += ["-k", last]
    for mod in reversed(mods):
        argv += ["-m", mod]
    done = _run(argv, timeout=5)
    if done.returncode != 0:
        raise HandsError(f"could not press {combo}: {(done.stderr or '').strip()[:120]}")
    return combo


def scroll(direction, amount):
    from . import desktop

    if direction in ("up", "down"):
        said = desktop.mouse_scroll(amount if direction == "down" else -amount)
    else:
        said = desktop._run(["mousemove", "--wheel", "-x", str(amount if direction == "right" else -amount), "-y", "0"])
    if said != "ok":
        raise HandsError(said)


def open_target(target):
    from . import apps

    target = str(target).strip()
    if looks_like_url(target):
        url = target if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I) else "https://" + target
        if url.split(":", 1)[0].lower() not in ("http", "https", "file", "mailto"):
            raise HandsError(f"will not open {url.split(':', 1)[0]} links")
        ok, why = apps._launch(["xdg-open", url])
        return ok, why, f"opened {url}"
    path = Path(target).expanduser()
    if path.exists():
        ok, why = apps._launch(["xdg-open", str(path)])
        return ok, why, f"opened {path.name}"
    app = apps.find_app(target)
    if app is None:
        raise HandsError(f"no app, file or address called {target}")
    ok, why = apps._launch(["gtk-launch", app["id"]])
    return ok, why, f"opened {app['name']}"


class Hands:
    """One act call: the target window, a lazily walked tree, and the pointer to put back."""

    def __init__(self, target):
        self.target = target
        try:
            self.client, self.monitors = resolve(target)
        except HandsError:
            if " ".join(str(target or "").split()).lower() not in ("", "front"):
                raise
            self.client, self.monitors = None, hypr("monitors") or []
        self._tree = None
        self.pointer = None

    @property
    def address(self):
        return self.client.get("address") if self.client else "front"

    def refresh(self):
        self._tree = None
        try:
            self.client, self.monitors = resolve(self.address)
        except HandsError:
            self.client = None

    def tree(self):
        if self.client is None:
            raise HandsError("no window to act on")
        if self._tree is None:
            self._tree = Tree(self.client, self.monitors)
        return self._tree

    def remember_pointer(self):
        if self.pointer is None:
            self.pointer = cursor() or False

    def restore_pointer(self):
        if self.pointer:
            try:
                move(*self.pointer)
            except HandsError:
                pass

    def find(self, text, near=None, retry=True):
        for attempt in range(2 if retry else 1):
            tree = self.tree() if self.client else None
            found = find_text(text, self.address, tree=tree)["matches"]
            if found:
                if near:
                    return nearest(found, find_text(near, self.address, tree=tree)["matches"])
                return found[0]
            if attempt == 0 and retry:
                time.sleep(0.6)
                self.refresh()
        return None

    def check(self, label, step):
        if risky(label) and not step.get("confirmed"):
            raise NeedsConfirm(f'clicking "{str(label)[:80]}" may delete, pay, send or sign out; '
                               "ask Marcus, then run the step again with confirmed true")

    def precheck(self, steps):
        for step in steps:
            if step["do"] == "click_text":
                self.check(step["text"], step)
            elif step["do"] == "click":
                node = self.tree().control(step["id"], step.get("name"))
                if node is not None:
                    self.check(node.name, step)
            elif step["do"] == "click_at":
                node = self.tree().at(step["x"], step["y"])
                if node is not None:
                    self.check(node.name, step)

    def run(self, step):
        kind = step["do"]
        if kind == "focus":
            if self.client is None:
                raise HandsError("no window to focus")
            ok, why = window.bring(self.client["address"])
            if not ok:
                raise HandsError(why)
            return f"focused {self.client.get('class')}"
        if kind == "click_text":
            hit = self.find(step["text"], step.get("near"))
            if hit is None:
                raise HandsError(f'no "{step["text"]}" on screen')
            self.check(hit["text"], step)
            self.remember_pointer()
            x, y = hit["x"] + hit["w"] // 2, hit["y"] + hit["h"] // 2
            click_point(x, y, step["button"], step["double"])
            return f'clicked "{hit["text"]}" at {x},{y} ({hit["source"]})'
        if kind == "click":
            node = self.tree().control(step["id"], step.get("name"))
            if node is None:
                raise HandsError(f"no control {step['id']}" + (f" named {step['name']}" if step.get("name") else ""))
            self.check(node.name, step)
            if step["button"] == "left" and not step["double"]:
                done = self._activate(node)
                if done:
                    return done
            if not node.box:
                raise HandsError(f"control {node.id} has no position and no action")
            self.remember_pointer()
            x, y = centre(node.box)
            click_point(x, y, step["button"], step["double"])
            return f'clicked {node.kind} "{node.name[:60]}" at {x},{y}'
        if kind == "click_at":
            self.remember_pointer()
            click_point(step["x"], step["y"], step["button"], step["double"])
            return f"clicked at {step['x']},{step['y']}"
        if kind == "type":
            return type_text(step["text"])
        if kind == "key":
            return f"pressed {press(step['key'])}"
        if kind == "fill":
            return self._fill(step)
        if kind == "scroll":
            self.remember_pointer()
            if "x" in step:
                move(step["x"], step["y"])
            elif self.client is not None:
                w = short(self.client, self.monitors)
                move(w["x"] + w["w"] // 2, w["y"] + w["h"] // 2)
            scroll(step["direction"], step["amount"])
            return f"scrolled {step['direction']} {step['amount']}"
        if kind == "open":
            ok, why, said = open_target(step["target"])
            if not ok:
                raise HandsError(why)
            return said
        if kind == "wait":
            time.sleep(step["seconds"])
            return f"waited {step['seconds']:g}s"
        raise HandsError(f"unknown step {kind}")

    def _activate(self, node):
        acc = node.acc
        try:
            if node.kind in EDITABLE_ROLES and acc.get_component_iface() is not None and acc.grab_focus():
                return f'focused {node.kind} "{node.name[:60]}"'
            if acc.get_action_iface() is not None:
                count = acc.get_n_actions()
                names = [str(acc.get_action_name(i) or "").lower() for i in range(count)]
                index = next((i for i, n in enumerate(names) if n in PRESS_ACTIONS), 0 if count == 1 else None)
                if index is not None and acc.do_action(index):
                    return f'{names[index] or "activated"} {node.kind} "{node.name[:60]}"'
        except Exception:
            return None
        return None

    def _fill(self, step):
        tree = self.tree()
        node = None
        if step.get("field"):
            fields = [n for n in tree.controls if n.kind in EDITABLE_ROLES]
            want = norm(step["field"])
            node = next((n for n in fields if norm(n.name) == want), None) or \
                next((n for n in fields if want and want in norm(n.name)), None)
            if node is None:
                hit = self.find(step["field"], retry=False)
                if hit is None:
                    raise HandsError(f'no field "{step["field"]}"')
                self.remember_pointer()
                if hit.get("control_id"):
                    node = tree.control(hit["control_id"])
                if node is None or not self._activate(node):
                    click_point(hit["x"] + hit["w"] // 2, hit["y"] + hit["h"] // 2)
            elif not self._activate(node):
                if not node.box:
                    raise HandsError(f'field "{node.name}" has no position')
                self.remember_pointer()
                click_point(*centre(node.box))
            time.sleep(0.1)
        else:
            node = next((n for n in tree.controls if "focused" in n.states and n.kind in EDITABLE_ROLES), None)
        press("ctrl+a")
        if step["text"]:
            how = type_text(step["text"])
        else:
            press("BackSpace")
            how = "cleared"
        if node is None or node.kind == "password":
            return f"{how}, not read back"
        time.sleep(0.15)
        got = tree._text(node.acc)
        if norm(step["text"]) and norm(step["text"]) not in norm(got):
            raise HandsError(f'the field reads "{got[:80]}" after typing')
        return f'{how} into "{node.name[:60]}", read back'


def act(target, steps):
    """Runs a plan; the result names each step and ends with the front window and its controls."""
    started = time.monotonic()
    plan = validate(steps)
    plan, note = rewrite(plan)
    result = {"ok": False, "steps": []}
    if note:
        result["rewritten"] = note
    state = locked()
    if state is None:
        raise HandsError("could not read whether the screen is locked, so nothing is clicked or typed; try again in a moment")
    if state:
        raise HandsError("the screen is locked; nothing on it can be clicked or typed into until Marcus unlocks it")
    hands = Hands(target)
    needs_focus = hands.client is not None and hands.address != (hypr("activewindow") or {}).get("address")
    if needs_focus and any(s["do"] in ("type", "key", "fill") for s in plan) and plan[0]["do"] != "focus":
        plan = [{"do": "focus"}] + plan
    code = 0
    try:
        try:
            hands.precheck(plan)
        except NeedsConfirm as ask:
            result["needs_confirm"] = str(ask)
            code = EXIT_CONFIRM
            return result, code
        for n, step in enumerate(plan, 1):
            entry = {"do": step["do"], "ok": False}
            result["steps"].append(entry)
            try:
                entry["detail"] = hands.run(step)
                entry["ok"] = True
            except NeedsConfirm as ask:
                entry["detail"] = "needs confirmation"
                result["needs_confirm"], result["failed_step"] = str(ask), n
                code = EXIT_CONFIRM
                break
            except HandsError as error:
                entry["detail"] = str(error)
                result["failed_step"], result["error"] = n, f"step {n} ({step['do']}): {error}"
                code = 1
                break
            if step["do"] not in ("wait",):
                time.sleep(SETTLE.get(step["do"], 0.1))
                hands._tree = None
        result["ok"] = code == 0
    finally:
        hands.restore_pointer()
    try:
        after = front()
        result["front"] = after
        if after.get("address"):
            client, monitors = resolve(after["address"])
            listing = Tree(client, monitors, seconds=1.5).listing(ACT_CONTROLS, ACT_TEXT_CHARS)
            result["controls"] = listing["controls"]
            result["text"] = listing["text"]
            if listing.get("hint"):
                result["hint"] = listing["hint"]
    except HandsError as error:
        result["after"] = f"could not read the screen after: {error}"
    result["ms"] = round((time.monotonic() - started) * 1000)
    return result, code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise HandsError(message)


def parser():
    top = Parser(prog="diablo-hands", description="Diablo's hands on the desktop; prints one JSON object")
    sub = top.add_subparsers(dest="command", parser_class=Parser)
    sub.add_parser("front", help="the focused window")
    sub.add_parser("windows", help="every window, front to back")
    ui = sub.add_parser("ui", help="a window's controls and text through AT-SPI")
    ui.add_argument("--window", default="front")
    ui.add_argument("--max", type=int, default=300)
    find = sub.add_parser("find-text", help="where visible text is, in global screen pixels")
    find.add_argument("text", nargs="+")
    find.add_argument("--window", default="front")
    find.add_argument("--all", action="store_true")
    acting = sub.add_parser("act", help="run a plan of steps")
    acting.add_argument("--window", default="front")
    acting.add_argument("--steps", required=True)
    return top


def run(argv):
    """(object, exit code) for one command line."""
    started = time.monotonic()
    args = parser().parse_args(argv)
    if args.command is None:
        raise HandsError("say a command: front, windows, ui, find-text or act")
    prepare_env()
    if args.command == "act":
        return act(args.window, args.steps)
    if args.command == "front":
        out = front()
        out["locked"] = locked()
    elif args.command == "windows":
        out = {"windows": windows()}
    elif args.command == "ui":
        client, monitors = resolve(args.window)
        out = Tree(client, monitors).listing(max(1, min(int(args.max), 2000)))
    else:
        out = find_text(" ".join(args.text), args.window, args.all)
    out["ms"] = round((time.monotonic() - started) * 1000)
    return out, 0


def emit(obj, stream=None):
    stream = stream or sys.stdout
    indent = 2 if stream.isatty() else None
    stream.write(json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")
    stream.flush()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help", "help"):
        parser().print_help()
        return 0
    try:
        out, code = run(argv)
    except HandsError as error:
        out, code = {"error": str(error)}, 1
    except Exception as error:
        out, code = {"error": f"{type(error).__name__}: {str(error)[:200]}"}, 1
    emit(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
