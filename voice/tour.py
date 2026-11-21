"""The tour: "show me what you can do", run here on the desktop from a fixed script.

No brain turn: the lines are written here, so it starts the moment he asks, and every picture
appears as the line about it starts playing. Asking only what he can do gets one offer; a yes
in the follow-up window starts the tour, a no gets a short "as you wish".
"""

import json
import logging
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import presence

log = logging.getLogger("diablo.tour")

REPO = Path(__file__).resolve().parent.parent
HANDS = REPO / "deploy" / "diablo-hands"
EXAMPLE = REPO / "hub" / "skills" / "explain-3d" / "example.html"
OFFER_SECONDS = 120

SHOW = re.compile(
    r"\b(?:show|demo|demonstrate|walk me through)\b[^.?!]{0,30}\b(?:what you can do|what you(?:'re| are) "
    r"(?:capable of|able to do)|your (?:tricks|abilities|skills|powers|features))\b"
    r"|\bgive me (?:a|the) (?:demo|tour|showcase)\b|\bshow (?:me )?(?:a|the) (?:demo|tour|showcase)\b"
    r"|\bshow off\b|\bvis mig hvad du kan\b",
    re.I,
)
ASK = re.compile(
    r"^\W*(?:diablo\W+)?(?:so\W+)?(?:what (?:can|could) you do|what (?:else )?are you (?:capable of|able to do|good at)"
    r"|what are your (?:abilities|skills|powers|features)|what do you do|hvad kan du)\b[^.?!]{0,20}[.?!]?\W*$",
    re.I,
)
YES = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|go on|go ahead|do it|show me|let'?s see|let'?s go|"
    r"go for it|absolutely|of course|ja|gerne)\b",
    re.I,
)
NO = re.compile(r"^\W*(?:no|nope|nah|not now|later|maybe later|nej)\b", re.I)

OFFER = "Easier shown than told, sir. Shall I give you the short tour?"
DECLINED = "As you wish, sir."
POINT_ROLES = ("tab", "button", "menuitem", "link", "toggle")
ROLE_WORDS = {"tab": "tab", "button": "button", "menuitem": "menu", "link": "link", "toggle": "switch", "window": "window"}
RISKY = re.compile(r"\b(delete|remove|pay|buy|send|sign out|log out|uninstall|format|transfer|close)\b", re.I)

_offered = {"at": 0.0}


def wanted(text):
    return bool(SHOW.search(" ".join(str(text or "").split())))


def asked(text):
    return bool(ASK.match(" ".join(str(text or "").split())))


def offer():
    _offered["at"] = time.monotonic()


def answer(text, now=None):
    """"yes" or "no" to an offer made in the last OFFER_SECONDS, else None. Taken once."""
    now = time.monotonic() if now is None else now
    at, _offered["at"] = _offered["at"], 0.0
    if not at or now - at > OFFER_SECONDS:
        return None
    words = " ".join(str(text or "").split())
    if len(words.split()) > 8:
        return None
    if YES.match(words):
        return "yes"
    if NO.match(words):
        return "no"
    return None


def pick_target(controls):
    """One control worth pointing at: a tab, a button, a menu or a link with a short, clear name
    and a box on screen; tabs and buttons first. None when the window has none."""
    best = None
    for control in controls or []:
        role, name, box = control.get("role"), " ".join(str(control.get("name") or "").split()), control.get("box")
        if role not in POINT_ROLES or not box or len(box) != 4:
            continue
        if not 2 <= len(name) <= 24 or not re.search(r"[A-Za-z]{2}", name) or RISKY.search(name):
            continue
        if box[2] < 8 or box[3] < 8:
            continue
        rank = POINT_ROLES.index(role)
        if best is None or rank < best[0]:
            best = (rank, {"name": name, "role": role, "box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]}})
    return best[1] if best else None


def app_name(client):
    """A name to say for a window's app: "com.t3tools.T3Code" is "T3 Code", "zen" is "Zen"."""
    raw = str(client.get("class") or client.get("initialClass") or "").split(".")[-1]
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw.replace("-", " ").replace("_", " ")).strip()
    return raw[:1].upper() + raw[1:] if raw else ""


def front_window():
    """The focused window as a target to point at, from Hyprland, or None."""
    try:
        done = subprocess.run(["hyprctl", "-j", "activewindow"], capture_output=True, text=True, timeout=2)
        client = json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    at, size, name = client.get("at") or [], client.get("size") or [], app_name(client)
    if len(at) != 2 or len(size) != 2 or not name or size[0] < 40 or size[1] < 40:
        return None
    return {"name": name, "role": "window", "box": {"x": at[0], "y": at[1], "w": size[0], "h": size[1]}}


def heading_target():
    """The biggest line of text in the focused window, read by OCR: for apps with no controls to
    read (Electron, Chromium), the clearest thing on screen to point at. None when there is none."""
    from . import hands

    try:
        client, monitors = hands.resolve("front")
        region = hands.ocr_region(client, monitors)
        words, size = hands.ocr(region)
    except Exception:
        return None
    best = None
    for segment in hands.rows_of(words):
        clean = [w for w in segment if w["conf"] >= 80 and re.search(r"[A-Za-z]{2}", w["text"])]
        text = " ".join(w["text"] for w in segment)
        if len(clean) < 2 or len(clean) < len(segment) - 1 or not 6 <= len(text) <= 60:
            continue
        height = sorted(w["height"] for w in segment)[len(segment) // 2]
        top = min(w["top"] for w in segment)
        if best is None or (height, -top) > (best[0], -best[1]):
            best = (height, top, segment, text)
    if best is None:
        return None
    x, y, w, h = hands.to_global(hands.union(best[2]), region, size)
    name = " ".join(best[3].split()[:5])
    return {"name": name, "role": "heading", "box": {"x": x, "y": y, "w": w, "h": h}}


def find_target():
    """A named control in the focused window, else its biggest line of text, else the window itself."""
    return pick_target(read_front()) or heading_target() or front_window()


def read_front(timeout=4.0):
    """The focused window's controls through diablo-hands, or []."""
    try:
        done = subprocess.run([str(HANDS), "ui", "--window", "front", "--max", "120"], capture_output=True,
                              text=True, timeout=timeout)
        body = json.loads((done.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return []
    return body.get("controls") or [] if isinstance(body, dict) else []


class Screen:
    """What the tour puts on screen, through the hub like the brain's own cards and marks."""

    def __init__(self, cfg):
        self.base = str(cfg.get("hub_url") or "").rstrip("/")
        self.headers = {"X-Diablo-Token": str(cfg.get("hub_token") or "")}
        self.show_id = "tour-" + uuid.uuid4().hex[:8]
        self.card = None
        self.big = False

    def _post(self, path, body):
        if not self.base:
            return
        try:
            httpx.post(self.base + path, headers=self.headers, json=body, timeout=5)
        except httpx.HTTPError as error:
            log.warning("tour could not reach the hub: %s", error)

    def later(self, path, body):
        threading.Thread(target=self._post, args=(path, body), daemon=True, name="tour-screen").start()

    def marks(self, marks, n=0, count=1):
        self.later("/v1/screen/marks", {"show": self.show_id, "n": n, "count": count, "say": "", "marks": marks})

    def clear_marks(self):
        self.later("/v1/screen/marks", {"clear": True})

    def picture(self, title, html):
        self.big = True
        self.later("/v1/screen", {"title": title, "html": html, "size": "large"})

    def close_picture(self):
        if not self.big:
            return
        self.big = False
        from . import panel

        panel.apply("close")

    def say(self, words):
        self.card = presence.show_reply(words, card_id=self.card)


def lines(cfg, target=None, spoken=None):
    """The tour as (line, cue) pairs: cue shows what the line is about as it starts playing.
    target is the control to point at, or a function that returns it once it is read."""
    screen = Screen(cfg)
    said = []

    def step(text, then=None):
        def cue():
            said.append(text)
            if spoken is not None:
                spoken(text)
            screen.say(" ".join(said))
            if then is not None:
                then()
        return text, cue

    yield step("Here is what I can do, sir, in under a minute.")
    try:
        html = EXAMPLE.read_text()
    except OSError:
        html = ""
    if html:
        yield step("Ask me how something works and I draw it, like this atom you can turn.",
                   lambda: screen.picture("Explained in 3D", html))
    else:
        yield step("Ask me how something works and I draw it for you.")
    found = target() if callable(target) else target
    if found and found["role"] == "heading":
        yield step(f"I can point at anything on your screen, like this heading: {found['name']}.",
                   lambda: (screen.close_picture(), screen.marks([{"kind": "ring", "box": found["box"]},
                                                                   {"kind": "underline", "box": found["box"]}], 0, 1)))
    elif found:
        what = f"{found['name']} {ROLE_WORDS.get(found['role'], 'button')}"
        first = "spotlight" if found["role"] == "window" else "ring"
        yield step(f"I can point at anything on your screen, like your {what}.",
                   lambda: (screen.close_picture(), screen.marks([{"kind": first, "box": found["box"]},
                                                                   {"kind": "label", "box": found["box"],
                                                                    "text": found["name"]}], 0, 1)))
    else:
        yield step("I can point at anything on your screen, and walk you through a task step by step.",
                   screen.close_picture)
    yield step("And I can work your windows for you: click, type and press keys, a whole task in one go.",
               screen.clear_marks if found else None)
    yield step("Your lights, reminders, mail and phone run through me as well, and I hand coding work to T3.")
    yield step("That's the tour, sir. What would you like to try?")


def run(cfg, speaker, cancel=None, on_line=None):
    """The whole tour on this speaker, start to end, or until cancel is set. For a terminal test."""
    controls = {}
    reading = threading.Thread(target=lambda: controls.update(found=find_target()), daemon=True)
    reading.start()

    def target():
        reading.join(timeout=8.0)
        return controls.get("found")

    return speaker.say_stream(lines(cfg, target, on_line), cfg, cancel=cancel)
