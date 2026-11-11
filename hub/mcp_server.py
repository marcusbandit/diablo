import json
import logging
import os
import sys
import time

import httpx

from . import agent, db, devices, lockguard, modules, questions, settings, t3link, tasks, tts
from .config import get_config

logger = logging.getLogger("diablo.mcp")

DB_PATH = os.environ.get("DIABLO_HUB_DB", os.path.expanduser("~/diablo-hub/diablo.db"))
SERVER_INFO = {"name": "diablo", "version": "2.0"}
SHARED = (
    "save_fact", "recall_facts", "emit_object", "workspace_todo", "hue_light",
    "phone_status", "fleet_power", "fleet_where", "brain_search", "brain_read", "brain_log", "update_task",
    "ask_user", "cancel_question",
)
EXTRA = [
    {
        "name": "notify",
        "description": "Send Marcus a Telegram message right now, outside the current reply. For reports he should see on his phone when the reply itself goes elsewhere, like the web app.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "send_voice",
        "description": "Send Marcus a Telegram voice note in your own voice, spoken from text. Use it whenever he asks you to say, read or speak something aloud, or to answer as a voice note. Write the text in the script of its language, Japanese in kana and kanji, never romaji.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "what to say"},
                "language": {"type": "string", "description": "two letter code of the text: en, ja or da. Defaults to en"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "pair_device",
        "description": "Make a one-time pairing code for a new phone or browser to reach the hub, when Marcus asks to pair, connect or log in a device. Give him the text it returns as is; the code lasts ten minutes and works once.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_devices",
        "description": "The phones and browsers paired with the hub, with their numbers and when each was last seen.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "revoke_device",
        "description": "Lock one paired device out of the hub by its number from list_devices, when Marcus asks to unpair or revoke it or says it is lost.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "t3_where",
        "description": "Where T3 Code work can run right now: which machines are up and which projects (title, id, GitHub repo) each one has. Call it before you launch project work, to pick the machine and the projectId.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wake_banditbox",
        "description": "Wake banditbox, his desktop, when project work needs a project that only exists there and t3_where says it is down. The hub sends wake-on-LAN, waits for its T3 to come up, then gives you a new turn with the order in then, where the t3-banditbox tools are loaded. Tell Marcus you are waking it and end your turn.",
        "inputSchema": {
            "type": "object",
            "properties": {"then": {"type": "string", "description": "the full order to carry on with once banditbox is up: project, what to do, which model"}},
            "required": ["then"],
        },
    },
    {
        "name": "restart_self",
        "description": "Restart the hub, which is your own body, when a change needs it and it is safe. The hub checks first: no turn running anywhere, Marcus neither speaking nor being listened to, no reply or approval waiting for him, no watched T3 thread waiting on an answer. If it is safe the restart follows once this reply is finished and the warm session resumes. If not it lists the reasons and the watched T3 threads that are running, so you can ask them with t3_thread_send whether a quick restart is fine, and try again later. dry_run only checks. After a restart you get a turn to tell Marcus in one line.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "why, in a few words; Marcus hears it afterwards. Needed unless dry_run"},
                "dry_run": {"type": "boolean", "description": "only check, never schedule"},
            },
            "required": [],
        },
    },
    {
        "name": "watch_t3_thread",
        "description": "Ask the hub to watch a T3 Code thread you launched or messaged. The hub keeps its task card live for Marcus from then on, with no turn from you. You get a new turn when it waits on an approval or a question, when it fails, and when partner work finishes; finished self-improvement work only updates its card. Call it right after t3_thread_launch or t3_thread_send for anything that runs longer than a minute.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "environment": {"type": "string", "description": "the T3 environment, like nemurenai or banditbox"},
                "thread_id": {"type": "string"},
                "title": {"type": "string", "description": "a few words naming the work, shown on its task card"},
                "kind": {"type": "string", "enum": list(tasks.KINDS), "description": "self_improvement for work on yourself (the diablo repo, or anything you started for yourself), partner for his other projects or anything he asked for. Leave it out to decide from the thread's project"},
            },
            "required": ["environment", "thread_id"],
        },
    },
    {
        "name": "thread_history",
        "description": "Look back through the whole conversation with Marcus. Each turn only carries the threads the hub judged relevant; when he points at something you were not given, search for it here by a few words. Returns the best matching thread with its latest turns, and matching messages.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "a few words naming what he means"}},
            "required": ["query"],
        },
    },
]


def tools():
    listed = []
    for entry in agent.TOOLS:
        function = entry.get("function") or {}
        if function.get("name") in SHARED or modules.owner(function.get("name") or "") is not None:
            listed.append({
                "name": function["name"],
                "description": function.get("description", ""),
                "inputSchema": function.get("parameters") or {"type": "object", "properties": {}},
            })
    return listed + EXTRA


def notify(text):
    text = str(text or "").strip()[:3900]
    if not text:
        return "nothing to send"
    token = settings.get("telegram_bot_token")
    chat_id = settings.get("telegram_chat_id")
    if not token or not chat_id:
        return "telegram is not set up, no chat to send to"
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": int(chat_id), "text": text},
            timeout=15,
        )
    except httpx.HTTPError as error:
        return f"telegram failed: {error}"
    if response.status_code != 200:
        return f"telegram refused: {response.text[:200]}"
    db.add_message("telegram", "assistant", text, {"pushed": True})
    return "sent"


def send_voice(text, language):
    text = str(text or "").strip()[:2000]
    language = str(language or "en").strip().lower()[:2] or "en"
    if not text:
        return "nothing to say"
    token = settings.get("telegram_bot_token")
    chat_id = settings.get("telegram_chat_id")
    if not token or not chat_id:
        return "telegram is not set up, no chat to send to"
    try:
        audio = tts.speak(text, True, language)
    except Exception as error:
        return f"speech failed: {error}"
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendVoice",
            data={"chat_id": int(chat_id)},
            files={"voice": ("voice.ogg", audio, "audio/ogg")},
            timeout=60,
        )
    except httpx.HTTPError as error:
        return f"telegram failed: {error}"
    if response.status_code != 200:
        return f"telegram refused: {response.text[:200]}"
    db.add_message("telegram", "assistant", text, {"voice_reply": True, "pushed": True})
    return "voice note sent"


class TaskRefused(RuntimeError):
    pass


def save_task(task_id, fields):
    """Writes a task through the hub, so the apps hear of it at once; straight to the database
    only when the hub is away. A refusal is raised, never written behind the hub's back."""
    token = str(get_config().get("hub_token") or "")
    try:
        response = httpx.post(f"{devices.public_url()}/v1/tasks", headers={"X-Diablo-Token": token},
                              json=dict(fields, id=task_id), timeout=10)
    except httpx.HTTPError as error:
        logger.info("hub not reachable for the task write: %s", error)
        return tasks.save(task_id, fields)
    if 400 <= response.status_code < 500:
        raise TaskRefused(f"the hub refused the task card ({response.status_code}): {response.text[:200]}")
    try:
        if response.status_code == 200:
            return response.json()["task"]
    except (ValueError, KeyError):
        pass
    logger.info("hub failed the task write (%s), writing it here", response.status_code)
    return tasks.save(task_id, fields)


def watch(arguments):
    env = str(arguments.get("environment") or "").strip()
    thread_id = str(arguments.get("thread_id") or "").strip()
    if not env or not thread_id:
        return "environment and thread_id are both needed"
    conv = os.environ.get("DIABLO_CONVERSATION") or "main"
    title = str(arguments.get("title") or "").strip()
    db.add_watch(thread_id, env, title, conv)
    kind = str(arguments.get("kind") or "").strip()
    source = "arg"
    known = db.get_task(thread_id)
    if kind not in tasks.KINDS and known and known.get("kind_source") in ("arg", "project"):
        kind, source = known["kind"], known["kind_source"]
    elif kind not in tasks.KINDS:
        kind, source = tasks.kind_at_watch(env, thread_id)
    fields = {"kind": kind, "kind_source": source, "machine": env, "state": "working", "step": "Starting",
              "source": f"t3:{thread_id}"}
    if title:
        fields["title"] = title
    try:
        task = save_task(thread_id, fields)
    except TaskRefused as error:
        return f"watching {thread_id} on {env}, but {error}"
    when = "when it waits on him or fails" if kind == "self_improvement" else "when it finishes, fails or waits on him"
    return f"watching {thread_id} on {env}; its task card ({task['kind']}) shows him the progress, you get a turn {when}"


def update_task(arguments, writer=None):
    writer = writer or save_task
    task_id = str(arguments.get("id") or "").strip()
    if not task_id and not str(arguments.get("title") or "").strip():
        return "a new task needs a title"
    if task_id and db.get_task(task_id) is None and not str(arguments.get("title") or "").strip():
        return f"no task {task_id}; give a title to create one"
    if arguments.get("kind") and arguments["kind"] not in tasks.KINDS:
        return f"kind is one of {', '.join(tasks.KINDS)}"
    if arguments.get("state") and arguments["state"] not in tasks.STATES:
        return f"state is one of {', '.join(tasks.STATES)}"
    fields = {key: arguments[key] for key in ("title", "kind", "state", "step", "result", "machine") if arguments.get(key) is not None}
    links = {key: arguments[key] for key in ("pr", "issue", "thread") if arguments.get(key)}
    if links:
        fields["links"] = links
    if not task_id:
        fields.setdefault("kind", "partner")
        fields.setdefault("source", "tool")
    task = writer(task_id or tasks.new_id(), fields)
    return f"task {task['id']} saved: {task['state']}, {task['kind']}. Its card shows it; no need to tell him"


def ask_through_hub(name, arguments, conv, post=None):
    """ask_user and cancel_question go through the hub, so the apps hear of the question at once; straight to
    the database only when the hub cannot be reached at all, never after it may have stored the question."""
    post = post or httpx.post
    token = str(get_config().get("hub_token") or "")
    if name == "ask_user":
        url, body = f"{devices.public_url()}/v1/questions", dict(arguments, conv=conv)
    else:
        url, body = f"{devices.public_url()}/v1/questions/cancel", {"conv": conv, "id": arguments.get("id")}
    try:
        response = post(url, headers={"X-Diablo-Token": token}, json=body, timeout=10)
    except (httpx.ConnectError, httpx.ConnectTimeout) as error:
        logger.info("hub not reachable for %s: %s", name, error)
        return questions.call(name, arguments, conv)
    except httpx.HTTPError as error:
        logger.info("hub did not confirm %s: %s", name, error)
        return (f"the hub did not confirm it ({type(error).__name__}); it may have the question already. "
                "Do not ask again this turn; end it with one short line.")
    if response.status_code == 400 and name == "ask_user":
        try:
            return f"not asked: {response.json().get('detail')}"
        except ValueError:
            return "not asked: the hub refused it"
    if response.status_code != 200:
        logger.info("hub refused %s (%s)", name, response.status_code)
        return f"not done: the hub answered {response.status_code}"
    found = (response.json() or {}).get("question")
    if name == "ask_user":
        return questions.tool_text(found)
    return f"question {found['id']} withdrawn" if found else "no pending question to withdraw"


def restart_text(verdict):
    lines = []
    if verdict.get("restarting"):
        lines.append("Restart scheduled. It happens once this reply is finished and nothing else is going on; the session resumes and you get a turn afterwards.")
    elif verdict.get("safe"):
        lines.append("Safe to restart now (dry run, nothing scheduled).")
    else:
        lines.append("Not restarting. Reasons: " + "; ".join(verdict.get("reasons") or ["unknown"]) + ". Try again later.")
    running = [t for t in verdict.get("threads") or [] if t.get("status") == "running"]
    if running:
        lines.append("Watched T3 threads running, ask them before a restart: " + "; ".join(
            f"{t['title']} ({t['thread_id']} on {t['environment']})" for t in running))
    return "\n".join(lines)


def restart(arguments):
    token = str(get_config().get("hub_token") or "")
    body = {
        "reason": str(arguments.get("reason") or ""),
        "conv": os.environ.get("DIABLO_CONVERSATION") or "main",
        "dry_run": bool(arguments.get("dry_run")),
    }
    try:
        response = httpx.post(f"{devices.public_url()}/v1/restart", headers={"X-Diablo-Token": token}, json=body, timeout=20)
    except httpx.HTTPError as error:
        return f"could not reach the hub to ask: {error}. Not restarting."
    if response.status_code != 200:
        return f"the hub refused the restart check ({response.status_code}). Not restarting."
    return restart_text(response.json())


WAKE_KEY = "wake.pending"


def send_wake():
    import subprocess

    import ipaddress

    from . import localenv

    mac = str(settings.get("fleet.banditbox.mac") or localenv.get("DIABLO_WOL_MAC"))
    if not mac:
        return False
    sent = []
    attempts = [["wakeonlan", mac]]
    subnet = str(settings.get("network.home_subnet") or "")
    try:
        attempts.append(["wakeonlan", "-i", str(ipaddress.ip_network(subnet, strict=False).broadcast_address), mac])
    except ValueError:
        pass
    for argv in attempts:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            sent.append(done.returncode == 0)
        except (OSError, subprocess.TimeoutExpired):
            sent.append(False)
    return any(sent)


def wake(arguments):
    then = str(arguments.get("then") or "").strip()
    if not then:
        return "say what to carry on with once banditbox is up"
    if t3link.is_up("banditbox", fresh=True):
        return "banditbox is already up; t3-banditbox tools load on your next turn if they are missing now"
    if not send_wake():
        return "could not send the wake-on-LAN packet from the Pi"
    conv = os.environ.get("DIABLO_CONVERSATION") or "main"
    pending = json.loads(settings.get(WAKE_KEY) or "[]")
    pending.append({"then": then[:2000], "conv": conv, "created": time.time()})
    settings.set_setting(WAKE_KEY, json.dumps(pending[-5:]))
    return "wake packet sent; you get a new turn when banditbox's T3 is up (usually 1 to 2 minutes). Tell Marcus and end your turn."


def call(name, arguments):
    refused = lockguard.refusal(name, os.environ.get("DIABLO_CONVERSATION") or "main", "mcp", arguments)
    if refused:
        return refused
    if name == "t3_where":
        return t3link.where()
    if name == "pair_device":
        return devices.pair_text(devices.mint())
    if name == "list_devices":
        return devices.devices_text()
    if name == "revoke_device":
        device_id = int(arguments.get("id") or 0)
        done = devices.revoke(device_id)
        return devices.revoked_text(done) if done else f"no device {device_id}"
    if name == "wake_banditbox":
        return wake(arguments)
    if name == "notify":
        return notify(arguments.get("text"))
    if name == "send_voice":
        return send_voice(arguments.get("text"), arguments.get("language"))
    if name == "watch_t3_thread":
        return watch(arguments)
    if name == "update_task":
        return update_task(arguments)
    if name == "thread_history":
        from . import threads
        return threads.history(arguments.get("query"))
    if name == "restart_self":
        return restart(arguments)
    if name in ("ask_user", "cancel_question"):
        return ask_through_hub(name, arguments, os.environ.get("DIABLO_CONVERSATION") or "main")
    if modules.owner(name) is not None:
        return modules.call(name, arguments)
    if name in SHARED:
        return agent._call_tool(name, arguments, {"workspace": os.environ.get("DIABLO_WORKSPACE") or "scratch"})
    return f"unknown tool {name}"


def handle(request):
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    if method == "tools/list":
        return {"tools": tools()}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        started = time.monotonic()
        try:
            text = str(call(name, arguments))
            failed = False
        except Exception as error:
            logger.exception("tool %s failed", name)
            text = f"tool error: {error}"
            failed = True
        logger.info("%s %.1fs -> %s", name, time.monotonic() - started, text[:160])
        return {"content": [{"type": "text", "text": text}], "isError": failed}
    if method == "ping":
        return {}
    raise LookupError(method)


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": handle(request)}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": f"no method {request.get('method')}"}}
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    db.init(DB_PATH)
    serve()


if __name__ == "__main__":
    main()
