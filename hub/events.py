import asyncio
import logging

logger = logging.getLogger(__name__)

subscribers: set = set()
_loop = None


def bind(loop):
    global _loop
    _loop = loop


def _deliver(event):
    for queue in list(subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def publish(event):
    try:
        asyncio.get_running_loop()
        on_loop = True
    except RuntimeError:
        on_loop = False
    if on_loop or _loop is None:
        _deliver(event)
    else:
        _loop.call_soon_threadsafe(_deliver, event)


def message_added(message_id, source, meta, main_sources):
    section = (meta or {}).get("section") if isinstance(meta, dict) else None
    if section is None and source in main_sources:
        section = "diablo"
    if section:
        publish({"kind": "message", "section": section, "id": message_id})


PLAIN = {
    "hue_light": "lights", "lights": "lights", "notify": "Telegram", "phone_status": "your phone",
    "fleet_power": "the fleet", "fleet_where": "the fleet", "fleet_run": "the fleet", "fleet_fetch": "the fleet",
    "fleet_put": "the fleet", "wake_banditbox": "banditbox", "list_devices": "devices", "pair_device": "devices",
    "revoke_device": "devices", "save_fact": "memory", "recall_facts": "memory", "brain_log": "the vault",
    "brain_read": "the vault", "brain_search": "the vault", "add_reminder": "reminders",
    "list_reminders": "reminders", "cancel_reminder": "reminders", "workout_log": "the workout log",
    "workout_stats": "the workout log", "emit_object": "a card", "watch_t3_thread": "T3", "update_task": "a task card",
    "WebSearch": "the web", "WebFetch": "the web", "Read": "files", "Write": "files", "Edit": "files",
    "Glob": "files", "Grep": "files", "Task": "a helper", "Agent": "a helper", "ToolSearch": "his tools",
    "run_shell": "the shell", "Bash": "the shell", "send_voice": "his voice", "now": "the clock",
}


SSH_ARGS = {"-o", "-i", "-p", "-l", "-F", "-J", "-L", "-R", "-D", "-E", "-b", "-c", "-m", "-S", "-W", "-w"}


def plain_step(label):
    """A tool step's label in a word or two Marcus would use: lights, mail, T3, ssh nemurenai."""
    name, _, detail = str(label or "").partition(": ")
    name, _, host = name.partition(" on ")
    name = name.strip()
    if name.startswith("t3_"):
        return f"T3 on {host}" if host else "T3"
    if "mail" in name or "jorbis" in name:
        return "mail"
    if name in ("Bash", "run_shell"):
        words = detail.split()
        if words and words[0] == "ssh":
            target, skip = "", False
            for word in words[1:]:
                if skip:
                    skip = False
                elif word.startswith("-"):
                    skip = word in SSH_ARGS
                else:
                    target = word
                    break
            return f"ssh {target.split('@')[-1]}".strip()
    return PLAIN.get(name) or name.replace("_", " ") or "a tool"
