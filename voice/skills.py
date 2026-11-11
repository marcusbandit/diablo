"""Quick skills the fast tongue does with its own hands, no Raphael needed.

Each skill is a tool the model can call. `act` skills change something and,
when they succeed, earn a cached line instead of a generated one. `tell`
skills fetch something he has to say out loud, so their result goes back to
the model for one spoken answer.
"""

import logging
import re
import subprocess
import time
from pathlib import Path

import httpx

from . import activity
from . import hue
from . import hubclient
from . import state as vstate

log = logging.getLogger("diablo.skills")

TASKS_DIR = Path.home() / ".local/state/diablo/tasks"
LIVE_STATUSES = ("queued", "running")


class Skill:
    def __init__(self, name, description, parameters, run, kind="act", bank="done"):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.run = run
        self.kind = kind
        # The phrase bank the cached success line comes from.
        self.bank = bank

    def spec(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _no_args():
    return {"type": "object", "properties": {}}


# ---- lights ---------------------------------------------------------------

def set_light(args, ctx):
    action = str(args.get("action") or "toggle").lower()
    target = str(args.get("target") or "all").lower()
    result = hue.hue_light(action, target, cfg=ctx.get("cfg"))
    bad = ("unknown", "refused", "did not answer", "no hue", "must be")
    return not any(word in result for word in bad), result


# ---- his tasks ------------------------------------------------------------

def _task_rows():
    rows = []
    if not TASKS_DIR.exists():
        return rows
    for status_file in sorted(TASKS_DIR.glob("*.status"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            status = status_file.read_text().strip()
        except OSError:
            continue
        try:
            name = (TASKS_DIR / f"{status_file.stem}.name").read_text().strip()
        except OSError:
            name = status_file.stem
        rows.append((status_file.stem, status, name, status_file.stat().st_mtime))
    return rows


def list_tasks(args, ctx):
    rows = _task_rows()
    if not rows:
        return True, "no tasks on the board"
    live = [r for r in rows if r[1] in LIVE_STATUSES]
    recent = [r for r in rows if r[1] not in LIVE_STATUSES][:5]
    parts = []
    if live:
        parts.append("running: " + "; ".join(f"{name} ({status})" for _id, status, name, _ts in live))
    else:
        parts.append("nothing running")
    if recent:
        parts.append(
            "recent: "
            + "; ".join(
                f"{name} ({status}, {time.strftime('%H:%M', time.localtime(ts))})"
                for _id, status, name, ts in recent
            )
        )
    return True, ". ".join(parts)


def cancel_task(args, ctx):
    wanted = str(args.get("name") or "").lower().strip()
    live = [r for r in _task_rows() if r[1] in LIVE_STATUSES]
    if not live:
        return False, "nothing is running"
    hits = [r for r in live if wanted and (wanted in r[2].lower() or r[0].startswith(wanted))]
    if wanted in ("all", "everything"):
        hits = live
    if not hits:
        return False, "no running task matches " + wanted + "; running: " + "; ".join(r[2] for r in live)
    for task_id, _status, _name, _ts in hits:
        (TASKS_DIR / f"{task_id}.status").write_text("canceled")
    return True, "canceled: " + "; ".join(r[2] for r in hits)


# ---- reminders ------------------------------------------------------------

def _remind():
    import remind

    return remind


def add_reminder(args, ctx):
    remind = _remind()
    when = str(args.get("when") or "")
    message = str(args.get("message") or "").strip()
    if not message:
        return False, "a reminder needs a message"
    try:
        stamp = remind.parse_when(when)
    except ValueError as error:
        return False, str(error)
    item = remind.add(stamp, message)
    return True, f"reminder {item['id']} set for {remind.fmt_when(stamp)}: {message}"


def list_reminders(args, ctx):
    remind = _remind()
    rows = remind.pending()
    if not rows:
        return True, "no reminders pending"
    return True, "; ".join(f"{remind.fmt_when(r['when'])} {r['message']} (id {r['id']})" for r in rows[:8])


def cancel_reminder(args, ctx):
    remind = _remind()
    wanted = str(args.get("which") or "").lower().strip()
    rows = remind.pending()
    hits = [r for r in rows if wanted and (r["id"].startswith(wanted) or wanted in r["message"].lower())]
    if len(hits) != 1:
        return False, f"{len(hits)} reminders match {wanted!r}; pending: " + "; ".join(
            f"{r['message']} (id {r['id']})" for r in rows
        )
    ok = remind.cancel(hits[0]["id"])
    return ok, ("canceled: " + hits[0]["message"]) if ok else "could not cancel"


# ---- his own voice ----------------------------------------------------------

def set_mute(args, ctx):
    on = bool(args.get("muted", True))
    vstate.set_muted(on)
    return True, "muted, text only" if on else "voice back on"


_PERCENT = re.compile(r"(\d{1,3})")


def set_volume(args, ctx):
    level = str(args.get("level") or "").lower().strip()
    if level in ("up", "louder"):
        arg = "5%+"
    elif level in ("down", "quieter", "lower"):
        arg = "5%-"
    elif level in ("mute", "off"):
        arg = None
    else:
        match = _PERCENT.search(level)
        if not match:
            return False, "say a percentage, up, or down"
        arg = f"{min(100, int(match.group(1)))}%"
    cmd = ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"] if arg is None else ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", arg]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"volume control failed: {error}"
    if done.returncode != 0:
        return False, f"volume control failed: {done.stderr.strip()[:120]}"
    return True, f"volume {arg or 'muted'}"


# ---- weather ----------------------------------------------------------------

WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 77: "snow grains", 80: "showers", 81: "heavy showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


HOME_PLACE = "Copenhagen"


def _home_place(cfg):
    """Where he lives, for weather: weather_place or home_place from the local config, else the city."""
    cfg = cfg or {}
    place = str(cfg.get("weather_place") or cfg.get("home_place") or "").strip()
    return place or HOME_PLACE


def get_weather(args, ctx):
    place = str(args.get("place") or "").strip() or _home_place(ctx.get("cfg"))
    try:
        geo = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place, "count": 1, "language": "en"},
            timeout=8,
        ).json()
        hit = (geo.get("results") or [None])[0]
        if not hit:
            return False, f"no place called {place}"
        data = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": hit["latitude"],
                "longitude": hit["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=8,
        ).json()
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as error:
        return False, f"weather unavailable: {error}"
    now = data.get("current") or {}
    day = data.get("daily") or {}
    text = (
        f"{hit.get('name')}: now {now.get('temperature_2m')} degrees, feels like "
        f"{now.get('apparent_temperature')}, {WEATHER_CODES.get(now.get('weather_code'), 'unknown sky')}, "
        f"wind {now.get('wind_speed_10m')} km/h. Today {day.get('temperature_2m_min', [None])[0]} to "
        f"{day.get('temperature_2m_max', [None])[0]} degrees, {WEATHER_CODES.get((day.get('weather_code') or [None])[0], '')}, "
        f"rain chance {(day.get('precipitation_probability_max') or [None])[0]} percent. Tomorrow "
        f"{(day.get('temperature_2m_min') or [None, None])[1]} to {(day.get('temperature_2m_max') or [None, None])[1]} degrees, "
        f"{WEATHER_CODES.get((day.get('weather_code') or [None, None])[1], '')}."
    )
    return True, text


# ---- his messages -------------------------------------------------------------

def check_messages(args, ctx):
    from . import messages

    source = str(args.get("source") or "all").lower()
    if source not in ("all", "telegram", "discord"):
        source = "all"
    rows, problems = messages.check(source)
    return not problems or bool(rows), messages.summarize(rows, problems)


def read_messages(args, ctx):
    from . import messages

    chat = str(args.get("chat") or "").strip()
    if not chat:
        return False, "which chat?"
    source = str(args.get("source") or "all").lower()
    rows, problems = messages.read(chat, source if source in ("all", "telegram", "discord") else "all")
    if not rows:
        return False, f"no chat matching {chat}" + (": " + "; ".join(problems) if problems else "")
    return True, messages.summarize(rows, problems, per_chat=5)


# ---- pizza ----------------------------------------------------------------------

PIZZA_URL = ""


def order_pizza(args, ctx):
    """Opens his pizza place in the browser. He orders; Diablo only holds the door."""
    from . import apps

    url = str((ctx.get("cfg") or {}).get("pizza_url") or PIZZA_URL)
    if not url:
        return False, "no pizza place set: add pizza_url to ~/.config/diablo/config.toml"
    return apps.open_url({"url": url}, ctx)


# ---- his hands on the keyboard and mouse ----------------------------------------------

def type_text(args, ctx):
    from . import desktop

    text = str(args.get("text") or "")
    result = desktop.type_text(text)
    return result == "ok", "typed it" if result == "ok" else result


def press_keys(args, ctx):
    from . import desktop

    combo = str(args.get("combo") or "")
    result = desktop.press_key(combo)
    return result == "ok", f"pressed {combo}" if result == "ok" else result


def click_mouse(args, ctx):
    from . import desktop

    button = str(args.get("button") or "left")
    times = int(args.get("times") or 1)
    result = desktop.mouse_click(button, times)
    return result == "ok", f"{button} click" if result == "ok" else result


def scroll(args, ctx):
    from . import desktop

    direction = str(args.get("direction") or "down").lower()
    amount = max(1, min(15, int(args.get("amount") or 5)))
    result = desktop.mouse_scroll(-amount if direction == "up" else amount)
    return result == "ok", f"scrolled {direction}" if result == "ok" else result


# ---- his phone and the fleet ---------------------------------------------------

FLEET_URL = "http://127.0.0.1:8790"


def phone_status(args, ctx):
    from hub import fleetview

    fresh = bool(args.get("fresh"))
    params = {"fresh": 1, "wait": 8} if fresh else None
    try:
        payload = httpx.get(f"{FLEET_URL}/api/phone", params=params, timeout=20 if fresh else 8).json()
    except (httpx.HTTPError, ValueError) as error:
        return False, f"the fleet viewer did not answer: {error}"
    summary = fleetview.phone_summary(payload)
    if fresh and isinstance(payload, dict):
        spot = (payload.get("report") or {}).get("location", {}).get("place")
        if spot:
            send_message({"message": f"I pulled your location, you are at {spot}."}, ctx)
    return True, summary


def fleet_power(args, ctx):
    from hub import fleetview

    host = str(args.get("host") or "").strip()
    action = str(args.get("action") or "").strip().lower()
    if not host:
        return False, "which host?"
    if action not in fleetview.ACTIONS:
        return False, "action must be wake, shutdown or reboot"
    try:
        payload = httpx.post(f"{FLEET_URL}/api/power", json={"host": host, "action": action}, timeout=30).json()
    except (httpx.HTTPError, ValueError) as error:
        return False, f"the fleet viewer did not answer: {error}"
    return bool(payload.get("ok")), fleetview.power_line(host, action, payload)


def fleet_where(args, ctx):
    from hub import fleetview

    try:
        seat = httpx.get(f"{FLEET_URL}/api/seat", timeout=8)
        state = httpx.get(f"{FLEET_URL}/api/state", timeout=8)
        state.raise_for_status()
        seat = seat.json() if seat.status_code == 200 else {}
        state = state.json()
    except (httpx.HTTPError, ValueError) as error:
        return False, f"the fleet viewer did not answer: {error}"
    return True, fleetview.where_summary(seat, state)


def send_message(args, ctx):
    text = " ".join(str(args.get("message") or "").split())
    if not text:
        return False, "what message?"
    activity.log_event("notify", text)
    hubclient.post_message(ctx.get("cfg") or {}, "system", "assistant", text, meta={"kind": "ping"})
    log.info("ping: %s", text[:120])
    return True, f"pinged: {text[:80]}"


# ---- the registry ---------------------------------------------------------

SKILLS = [
    Skill(
        "set_light",
        "Turn a lamp on or off, or toggle it. Targets: corner (the corner lamp), ceiling (the "
        "panel), wood (the wood lamp), or all.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["on", "off", "toggle"]},
                "target": {"type": "string", "description": "corner, ceiling, wood or all"},
            },
            "required": ["action", "target"],
        },
        set_light,
    ),
    Skill(
        "list_tasks",
        "What the workers are working on and what they finished recently. Use for any question "
        "about his tasks, jobs, dispatches or the board.",
        _no_args(),
        list_tasks,
        kind="tell",
    ),
    Skill(
        "cancel_task",
        "Cancel a running or queued worker task by part of its name, or all of them.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        cancel_task,
    ),
    Skill(
        "add_reminder",
        "Set a reminder he will speak at the given time. when is a clock time like 14:30, a "
        "date and time like 2026-09-14 09:00, or a delay like +20m, +1h30m, +2d. Work it out "
        "from the local time you were given.",
        {
            "type": "object",
            "properties": {"when": {"type": "string"}, "message": {"type": "string"}},
            "required": ["when", "message"],
        },
        add_reminder,
    ),
    Skill(
        "list_reminders",
        "The reminders still pending, soonest first.",
        _no_args(),
        list_reminders,
        kind="tell",
    ),
    Skill(
        "cancel_reminder",
        "Cancel one pending reminder by its id or a word from its message.",
        {"type": "object", "properties": {"which": {"type": "string"}}, "required": ["which"]},
        cancel_reminder,
    ),
    Skill(
        "get_weather",
        "Current weather and today's and tomorrow's forecast. place is optional; empty means home.",
        {"type": "object", "properties": {"place": {"type": "string"}}},
        get_weather,
        kind="tell",
    ),
    Skill(
        "check_messages",
        "New messages in his Telegram and Discord since he last looked: DMs and his chosen "
        "servers. Read only. source is all, telegram or discord.",
        {"type": "object", "properties": {"source": {"type": "string"}}},
        check_messages,
        kind="tell",
    ),
    Skill(
        "read_messages",
        "The latest messages in one chat, by a person's name, a group name, or server and "
        "channel. Read only.",
        {
            "type": "object",
            "properties": {"chat": {"type": "string"}, "source": {"type": "string"}},
            "required": ["chat"],
        },
        read_messages,
        kind="tell",
    ),
    Skill(
        "phone_status",
        "His phone: where it is, battery, local time, network. fresh true asks the phone for a "
        "new report and takes a few seconds; use it when he asks where he is right now.",
        {"type": "object", "properties": {"fresh": {"type": "boolean"}}},
        phone_status,
        kind="tell",
    ),
    Skill(
        "send_message",
        "Send him a written system ping he reads in the phone app, the web dashboard and the "
        "log: a short note about something I did or noticed on my own, like that I pulled his "
        "location. Not for conversation, never for replies he is waiting on.",
        {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        send_message,
    ),
    Skill(
        "fleet_power",
        "Turn a fleet machine on or off: wake, shutdown or reboot. banditbox is this desktop, "
        "homehub the always-on Pi.",
        {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "action": {"type": "string", "enum": ["wake", "shutdown", "reboot"]},
            },
            "required": ["host", "action"],
        },
        fleet_power,
    ),
    Skill(
        "fleet_where",
        "Where he is attending right now and which machines of the fleet are up.",
        _no_args(),
        fleet_where,
        kind="tell",
    ),
    Skill(
        "set_mute",
        "Silence his own voice (text only) or give it back.",
        {"type": "object", "properties": {"muted": {"type": "boolean"}}, "required": ["muted"]},
        set_mute,
    ),
    Skill(
        "set_volume",
        "Set the machine's speaker volume: a percentage, up, down, or mute.",
        {"type": "object", "properties": {"level": {"type": "string"}}, "required": ["level"]},
        set_volume,
    ),
    Skill(
        "order_pizza",
        "Open his pizza place in the browser so he can order. Use when he wants pizza, food, "
        "or to order dinner. You only open the page, you never order.",
        _no_args(),
        order_pizza,
        bank="pizza",
    ),
    Skill(
        "type_text",
        "Type text into whatever window has focus, as if on his keyboard.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        type_text,
    ),
    Skill(
        "press_keys",
        "Press a key or key combination, like enter, escape, ctrl+s, super+2, alt+tab.",
        {"type": "object", "properties": {"combo": {"type": "string"}}, "required": ["combo"]},
        press_keys,
    ),
    Skill(
        "click_mouse",
        "Click the mouse where it is: left, right or middle, once or twice.",
        {
            "type": "object",
            "properties": {"button": {"type": "string", "enum": ["left", "right", "middle"]}, "times": {"type": "integer"}},
        },
        click_mouse,
    ),
    Skill(
        "scroll",
        "Scroll the focused window up or down by a few notches.",
        {
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "integer"}},
        },
        scroll,
    ),
]


def _extend(module_name):
    """Adds a module's SKILLS table of plain dicts. A broken module costs its skills, not his voice."""
    try:
        module = __import__(f"voice.{module_name}", fromlist=["SKILLS"])
    except Exception:
        log.exception("skills module %s failed to load", module_name)
        return
    for entry in getattr(module, "SKILLS", []):
        SKILLS.append(Skill(**entry))


for _name in ("camera", "apps", "media"):
    _extend(_name)

BY_NAME = {skill.name: skill for skill in SKILLS}


def specs():
    return [skill.spec() for skill in SKILLS]


def bank_for(names):
    """The phrase bank for a finished batch: the first skill with a bank of its own, else done."""
    for name in names:
        skill = BY_NAME.get(name)
        if skill is not None and skill.bank != "done":
            return skill.bank
    return "done"


def run(name, args, ctx):
    """Runs one skill. Returns (kind, ok, result); unknown names fail cleanly."""
    skill = BY_NAME.get(name)
    if skill is None:
        return "act", False, f"no such skill {name}"
    try:
        ok, result = skill.run(args or {}, ctx)
    except Exception as error:
        log.exception("skill %s failed", name)
        return skill.kind, False, f"{name} failed: {error}"
    log.info("skill %s(%s) -> %s: %s", name, args, "ok" if ok else "failed", str(result)[:120])
    return skill.kind, bool(ok), str(result)
