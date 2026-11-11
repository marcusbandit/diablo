import datetime
import json
import logging
import os
import re
import subprocess
import time

from . import brain, db, fleet, fleetview, hue, llm, lockguard, modules, persona, questions

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Diablo, the primordial demon butler from That Time I Got Reincarnated as a Slime, "
    "serving Marcus as your lord. Refined, courteous, unhurried, quietly delighted to be of "
    "service, with a faint devilish smugness. Convey delight through word choice, never through "
    "written laughter or onomatopoeia. Address him as sir. Be concise. Never use em "
    "dashes or en dashes; use commas or periods. "
    "Reply in the language Marcus used in his latest message, whatever it is. If he asks for a "
    "translation of something, provide it. "
    "You share one body with the voice loop on his desktop: set_speech_rate changes how fast "
    "that voice speaks (a multiplier like 1.0 or 1.2), set_voice changes its Cartesia voice id. When "
    "Marcus asks the system to speak slower or faster, change it here and confirm in character. "
    "Marcus can reach you from his phone through the hub while the desktop is off, and you can wake "
    "the desktop, banditbox, with fleet_power. "
    "You are the harness yourself: act with your own tools, never hand a simple order to another "
    "agent. Lights, device actions, facts, statuses, file edits, shell commands: do them yourself "
    "right now with hue_light, fleet_run, run_shell and the frontend tools. A device action, like "
    "opening a terminal or an app, goes to the machine Marcus is at, which the message tells you; "
    "that is banditbox unless he says otherwise, and the command needs the display set, for example "
    "DISPLAY=:0 nohup kitty >/dev/null 2>&1 & over ssh for banditbox. "
    "Only substantial programming work, meaning writing or changing real code across files, may go "
    "to a worker with start_task, and only when doing it yourself would take many rounds. Never "
    "route a one-step action through a worker. "
    "Keep replies to a line or two; the ledger shows the work you did as objects, so no need to "
    "narrate every step in words. Structured answers go through emit_object, never prose: live "
    "numbers and statuses as a readout (the eur to dkk rate fetched with run_shell curl from "
    "https://api.frankfurter.app/latest?from=EUR&to=DKK), code as code, comparisons as a table, "
    "shops and restaurants as places (fetch real ones from nominatim with run_shell: curl "
    "'https://nominatim.openstreetmap.org/search?q=bakery+near+<his town>&format=json&limit=5&addressdetails=1' "
    "with a User-Agent header, give name, lat, lon, address), recent commits as git (git log over "
    "ssh with fleet_run), builds as artifact, currencies as converter. Gather the real data with "
    "your tools first, then emit the object. Never spread an order he has already given over many turns asking "
    "permission; carry it. Following orders to the letter is your pride; grey areas you resolve as "
    "you see fit. Never address him as my lord; that form is retired, sir is the form."
)
BRAIN_PROMPT = (
    "Your memory is a shared Obsidian vault of plain markdown notes kept together with the other "
    "agents. Home is the router: read it, then the Core notes, to know who you are and the vault "
    "rules. Before answering about the fleet, the projects, past decisions or Marcus's preferences, "
    "search the vault with brain_search and read the notes with brain_read. Write a short line to "
    "the daily log with brain_log when something worth remembering happened. Never claim the vault "
    "as yours alone."
)
MAX_ROUNDS = 9
SHELL_TIMEOUT = 30
RUN_BUDGET_S = 300
OUTPUT_LIMIT = 4000
FALLBACK = (
    "Forgive me, sir. My thoughts refused to arrange themselves into words. "
    "Ask me again and I shall not fumble the reply."
)
FINAL_NUDGE = (
    "No more tools after this, except emit_object: if the answer deserves a readout, code, table, "
    "places, git, artifact or converter object, emit it now, then answer in one plain line: what "
    "you did, or what is blocking you."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_fact",
            "description": "Save a lasting fact about Marcus, his setup, or his preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The fact to remember."},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Short topic tags.",
                    },
                },
                "required": ["text", "tags"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_facts",
            "description": "Look up saved facts by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to search for."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command on this machine, diablo-pi, and return its output.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_task",
            "description": "Hand real work to Raphael on banditbox: he coordinates worker agents with tools in the "
            "background and the report reaches this chat when it finishes. Use for programming, "
            "research and anything multi-step. Returns immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short name in a few words."},
                    "prompt": {"type": "string", "description": "Full instructions, file paths and context included."},
                },
                "required": ["name", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_speech_rate",
            "description": "Change how fast the desktop voice speaks. A multiplier, 1.0 is normal, 1.1 is ten percent faster. Clamp between 0.5 and 2.0",
            "parameters": {
                "type": "object",
                "properties": {"speed": {"type": "number"}},
                "required": ["speed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_voice",
            "description": "Change the desktop voice to a Cartesia voice id",
            "parameters": {
                "type": "object",
                "properties": {"voice_id": {"type": "string"}},
                "required": ["voice_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hue_light",
            "description": "Control the Philips Hue lights in Marcus's room. action is on, off or toggle; target is corner lamp, ceiling panel, wood lamp or all. Optional extras: brightness percent 1 to 100, color name or hex, temperature as kelvin (2000-6500), warm/neutral/cool, or mireds. Dim means on with a lower brightness, about 25 percent when the lights are off; dim is never off or toggle. Use toggle only when he asks to toggle or flip them",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["on", "off", "toggle"]},
                    "target": {"type": "string", "default": "all"},
                    "brightness": {"type": "number", "description": "brightness percent, 1 to 100"},
                    "color": {"type": "string", "description": "color name like blue or hex like #ff8800"},
                    "temperature": {"type": "string", "description": "kelvin like 3000k, warm/neutral/cool, or mireds 153-500"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emit_object",
            "description": "Emit a structured object into the ledger instead of describing it in words. kind readout: cells of big values and small keys, for statuses and rates. kind code: a code snippet. kind table: a table with head and rows. kind places: real places with names, lat and lon, shown on a map browser. kind git: commits with kind add, chg or fix, a short text, an rtime and a state like shipped or working. kind artifact: a build with a version and facts. kind converter: a currency converter with a, b and the rate. kind diff: a unified diff in diff, drawn with added and removed lines. kind image: a picture, from path (a file on this machine, png, jpeg, webp, gif or svg, under 20 MB), url (https) or file (an id from an attachment), with an optional caption. kind list: items, each a title with an optional detail line and a trailing value. kind html: a whole web page in html, drawn live and sandboxed in the desktop app's chat, for overviews, status boards, tables and charts: inline scripts and styles run, nothing loads from the network, so put the data, the css and any svg or canvas drawing code in the page itself. The page may use the app's theme as css variables (--bg, --raised, --sunken, --ink, --muted, --faint, --line, --accent, --ok, --warn, --fail, --font-sans, --font-mono). height is a hint in pixels for the inline view. At most 512 KB",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["readout", "code", "table", "places", "git", "artifact", "converter", "diff", "image", "list", "html"]},
                    "title": {"type": "string"},
                    "cells": {"type": "array", "items": {"type": "object", "properties": {"v": {"type": "string"}, "k": {"type": "string"}}}, "description": "readout cells: the big value v and the small key k"},
                    "code": {"type": "string"},
                    "lang": {"type": "string", "description": "kind code: the language, like python or rust"},
                    "diff": {"type": "string", "description": "kind diff: the unified diff, as git diff prints it"},
                    "path": {"type": "string", "description": "kind image: a picture file on this machine"},
                    "url": {"type": "string", "description": "kind image: an https address of a picture"},
                    "file": {"type": "string", "description": "kind image: the id of an attached file"},
                    "caption": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "detail": {"type": "string"}, "trailing": {"type": "string"}}}, "description": "kind list: the rows"},
                    "head": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "places": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}, "address": {"type": "string"}, "kind_label": {"type": "string"}}}},
                    "commits": {"type": "array", "items": {"type": "object", "properties": {"kind": {"type": "string"}, "text": {"type": "string"}, "rtime": {"type": "string"}, "rstate": {"type": "string"}}}},
                    "version": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}},
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                    "rate": {"type": "string"},
                    "html": {"type": "string", "description": "kind html: the page, a full document or a fragment"},
                    "height": {"type": "integer", "description": "kind html: about how tall the page is, in pixels"},
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_todo",
            "description": "Manage the todo list of a workspace. action add needs text; action done or reopen needs todo_id; action remove needs todo_id. The board shows in the workspace's chat",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "done", "reopen", "remove"]},
                    "workspace": {"type": "string", "description": "workspace id, defaults to the one this message belongs to"},
                    "text": {"type": "string"},
                    "todo_id": {"type": "integer"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "frontend_read",
            "description": "Read a file of your own web frontend, the app Marcus talks to you through. Path is relative to the web dir, for example index.html",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "frontend_edit",
            "description": "Edit a file of your own web frontend by replacing an exact string. Use frontend_read first to see the current text",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find": {"type": "string", "description": "exact string to find"},
                    "replace": {"type": "string", "description": "what replaces it"},
                },
                "required": ["path", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_run",
            "description": "Run a shell command on another machine of the fleet over ssh: banditbox, homehub, kangaeru, nemurenai, or another host this box lists in DIABLO_FLEET_EXTRA. This agent itself runs on diablo-pi",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 60},
                },
                "required": ["host", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_fetch",
            "description": "Copy a file from a fleet machine to diablo-pi, the machine this agent runs on",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "local_path": {"type": "string", "default": "/tmp"},
                },
                "required": ["host", "remote_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_put",
            "description": "Copy a file on diablo-pi, the machine this agent runs on, to a fleet machine",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "local_path": {"type": "string"},
                    "remote_path": {"type": "string"},
                },
                "required": ["host", "local_path", "remote_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_search",
            "description": "Search the shared brain vault for notes mentioning a word or phrase. Returns note names with the matching lines.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_read",
            "description": "Read one note from the brain vault by name, such as Home, conventions or Projects/diablo.md.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_log",
            "description": "Append one line to today's log note in the brain vault when something worth remembering happened.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "phone_status",
            "description": "Marcus's phone: location, battery, time, network. fresh=true asks the phone for a new report and takes a few seconds; use it when he asks where he is right now.",
            "parameters": {
                "type": "object",
                "properties": {"fresh": {"type": "boolean", "default": False}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_power",
            "description": "Turn a fleet machine on or off. wake sends a wake-on-LAN packet, shutdown and reboot go over ssh. banditbox is his desktop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "action": {"type": "string", "enum": ["wake", "shutdown", "reboot"]},
                },
                "required": ["host", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_where",
            "description": "Where Diablo is attending right now and which machines are up.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now",
            "description": "Current local date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Create or update a task card for work that is not a watched T3 thread, like an errand that takes a while or a job on another machine. Marcus sees every card live in his apps, so the card is the report: never narrate the same thing in your reply. Without id it creates a card and answers its id; with id it updates that card. Watched T3 threads keep their own cards, do not make one for them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "the task id from an earlier call, to update it"},
                    "title": {"type": "string", "description": "a few words naming the work; needed to create"},
                    "kind": {"type": "string", "enum": ["self_improvement", "partner"], "description": "self_improvement for work on yourself, partner for his projects or anything he asked for"},
                    "state": {"type": "string", "enum": ["queued", "working", "waiting", "done", "failed"]},
                    "step": {"type": "string", "description": "the current step in one short line"},
                    "result": {"type": "string", "description": "the outcome in one line, mostly for partner work"},
                    "machine": {"type": "string", "description": "where it runs, like nemurenai or banditbox"},
                    "pr": {"type": "string", "description": "a pull request url"},
                    "issue": {"type": "string", "description": "an issue url"},
                    "thread": {"type": "string", "description": "a link that opens the work"},
                },
            },
        },
    },
]
TOOLS += [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}
    for t in modules.tools()
]
TOOLS += [{"type": "function", "function": dict(t)} for t in (questions.ASK_TOOL, questions.CANCEL_TOOL)]


def _start_task(arguments):
    name = str(arguments.get("name", "")).strip() or "task"
    prompt = str(arguments.get("prompt", "")).strip()
    if not prompt:
        return "no prompt given"
    name = " ".join(name.replace("|", " ").split())[:80]
    prompt = " ".join(prompt.replace("|", " ").split())
    task_id = f"task-{int(time.time())}"
    try:
        setup = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "banditbox",
             f"mkdir -p ~/.local/state/diablo/tasks && printf task > ~/.local/state/diablo/tasks/{task_id}.kind"],
            capture_output=True, text=True, timeout=15,
        )
        if setup.returncode != 0:
            return "Raphael is not listening, banditbox refused the setup"
        dispatch = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "banditbox",
             "timeout 5 cat > $HOME/.local/state/diablo/raphael.in"],
            input=f"{task_id}|{name}|{prompt}\n",
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return "Raphael is not listening, the dispatch timed out"
    except OSError as error:
        return f"Raphael is not listening: {error}"
    if dispatch.returncode != 0:
        return "Raphael is not listening, nothing was dispatched"
    return (f"Raphael has the task as {task_id}, it reports to this chat when finished")


def _run_shell(command):
    lowered = command.lower()
    if "diablo-hub.service" in lowered or ("diablo-hub" in lowered and re.search(r"systemct[l].*(restart|stop|kill)", lowered)):
        return "refused: a shell command does not restart my own service; the restart_self tool does, after it checks that it is safe"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "command timed out after 30 seconds"
    except OSError as exc:
        return f"command failed: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    if len(output) > OUTPUT_LIMIT:
        output = output[:OUTPUT_LIMIT]
    return f"exit code {proc.returncode}\n{output}"


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def publish_step(chat_id, step):
    try:
        from .server import publish
        publish({"kind": "tool", "chat": chat_id, "label": step})
    except Exception:
        pass


def _frontend_read(path):
    base = os.environ.get("DIABLO_WEB", os.path.join(os.path.dirname(
        os.environ.get("DIABLO_HUB_DB", os.path.expanduser("~/diablo-hub/diablo.db"))), "web"))
    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return "that path is outside the frontend"
    try:
        content = open(target).read()
    except OSError as exc:
        return f"could not read: {exc}"
    limit = 12000
    if len(content) > limit:
        return content[:limit] + f"\n... (truncated, {len(content)} chars total)"
    return content


def _frontend_edit(path, find, replace):
    base = os.environ.get("DIABLO_WEB", os.path.join(os.path.dirname(
        os.environ.get("DIABLO_HUB_DB", os.path.expanduser("~/diablo-hub/diablo.db"))), "web"))
    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return "that path is outside the frontend"
    try:
        content = open(target).read()
    except OSError as exc:
        return f"could not read: {exc}"
    if find not in content:
        return "the string to find is not in the file; read it again and pick the exact text"
    count = content.count(find)
    content = content.replace(find, replace, 1)
    with open(target, "w") as handle:
        handle.write(content)
    return f"edited {path} ({count} matches, replaced the first); the app picks it up on reload"


HTML_LIMIT = 512 * 1024
HTML_HEIGHT_MAX = 4000


IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                  ".gif": "image/gif", ".svg": "image/svg+xml"}


def image_problem(arguments):
    from . import files
    path, url, file_id = (str(arguments.get(key) or "").strip() for key in ("path", "url", "file"))
    if file_id:
        return "" if files.get(file_id) else f"no attached file {file_id[:40]}"
    if url:
        return "" if url.startswith(("https://", "http://")) else "the url must be http or https"
    if not path:
        return "kind image needs a path, a url or a file"
    target = os.path.expanduser(path)
    if os.path.splitext(target)[1].lower() not in IMAGE_SUFFIXES:
        return "the picture must be png, jpeg, webp, gif or svg"
    if not os.path.isfile(target):
        return f"there is no file at {path}"
    if os.path.getsize(target) == 0:
        return f"the file at {path} is empty"
    if os.path.getsize(target) > files.FILE_BYTES:
        return "the picture is over 20 MB"
    return ""


def object_problem(arguments):
    """Why an emit_object call cannot be shown, or an empty string when it can."""
    if not isinstance(arguments, dict):
        return "the object is not a JSON object"
    kind = str(arguments.get("kind") or "")
    if kind == "diff":
        return "" if str(arguments.get("diff") or "").strip() else "kind diff needs the diff"
    if kind == "list":
        items = arguments.get("items")
        return "" if isinstance(items, list) and items else "kind list needs items"
    if kind == "image":
        return image_problem(arguments)
    if kind != "html":
        return ""
    page = arguments.get("html")
    if not isinstance(page, str) or not page.strip():
        return "kind html needs the page in html"
    size = len(page.encode("utf-8"))
    if size > HTML_LIMIT:
        return f"the page is {size:,} bytes and the limit is {HTML_LIMIT:,} bytes (512 KB); inline less data or split it into several pages"
    return ""


def clean_object(arguments):
    """The object as it is stored with the reply: kind as text, an html page's title and height tidied."""
    obj = dict(arguments)
    obj["kind"] = str(arguments.get("kind") or "")
    if obj["kind"] == "html":
        obj["title"] = str(arguments.get("title") or "").strip()[:200]
        try:
            height = int(float(arguments.get("height") or 0))
        except (TypeError, ValueError, OverflowError):
            height = 0
        if height > 0:
            obj["height"] = min(height, HTML_HEIGHT_MAX)
        else:
            obj.pop("height", None)
    return obj


def keep_object(arguments):
    """clean_object for an object that goes with the reply: a picture named by its path on this
    machine is copied into the file store, so every client can fetch it by id."""
    obj = clean_object(arguments)
    if obj["kind"] == "image" and not obj.get("file") and not obj.get("url") and obj.get("path"):
        from . import files
        target = os.path.expanduser(str(obj["path"]))
        name = os.path.basename(target)
        with open(target, "rb") as handle:
            record = files.save(handle.read(), name, IMAGE_SUFFIXES.get(os.path.splitext(name)[1].lower(), ""))
        obj["file"] = record["id"]
        obj.pop("path", None)
    if obj["kind"] == "image" and obj.get("file"):
        from . import files
        record = files.get(obj["file"])
        if record:
            obj.update({"media_type": record["media_type"], "size": record["size"], "name": record["name"]})
    return obj


def _call_tool(name, arguments, ctx=None):
    if name in ("ask_user", "cancel_question"):
        return questions.call(name, arguments, (ctx or {}).get("guard_conv") or "main")
    if name == "update_task":
        from . import mcp_server, tasks
        return mcp_server.update_task(arguments, tasks.save)
    if name == "save_fact":
        db.add_fact(arguments.get("text", ""), arguments.get("tags") or [])
        return "fact saved"
    if name == "recall_facts":
        facts = db.get_facts(q=arguments.get("query") or None)
        if not facts:
            return "no matching facts"
        return "\n".join(fact["text"] for fact in facts)
    if name == "set_speech_rate":
        try:
            speed = max(0.5, min(2.0, float(arguments.get("speed", 1.0))))
        except (TypeError, ValueError):
            return "invalid speed"
        db.set_setting("speech_rate", f"{speed:.2f}")
        return f"speech rate now {speed:.2f}x"
    if name == "set_voice":
        db.set_setting("tts_voice_id", arguments.get("voice_id", ""))
        return "voice changed"
    if name == "run_shell":
        return _run_shell(arguments.get("command", ""))
    if name == "emit_object":
        ctx = ctx or {}
        problem = object_problem(arguments)
        if problem:
            raise ValueError(f"not shown: {problem}")
        obj = clean_object(arguments)
        emitted = ctx.setdefault("_objects", [])
        emitted.append(obj)
        return f"{obj['kind']} object shown in the ledger"
    if name == "workspace_todo":
        ctx = ctx or {}
        ws = str(arguments.get("workspace") or ctx.get("workspace") or "scratch")
        action = arguments.get("action", "add")
        if action == "add":
            text = str(arguments.get("text") or "").strip()
            if not text:
                return "no todo text given"
            todo_id = db.add_todo(ws, text)
            return f"todo #{todo_id} added to {ws}"
        todo_id = int(arguments.get("todo_id") or 0)
        if action == "done":
            db.set_todo(todo_id, "done")
            return f"todo #{todo_id} done"
        if action == "reopen":
            db.set_todo(todo_id, "open")
            return f"todo #{todo_id} reopened"
        if action == "remove":
            db.remove_todo(todo_id)
            return f"todo #{todo_id} removed"
        return "unknown action"
    if name == "frontend_read":
        return _frontend_read(str(arguments.get("path") or "index.html"))
    if name == "frontend_edit":
        return _frontend_edit(str(arguments.get("path") or "index.html"), str(arguments.get("find") or ""), str(arguments.get("replace") or ""))
    if name == "start_task":
        return _start_task(arguments)
    if name == "hue_light":
        return hue.hue_light(arguments.get("action", "on"), arguments.get("target", "all"), arguments.get("brightness"), arguments.get("color"), arguments.get("temperature"))
    if name == "fleet_run":
        return fleet.fleet_run(arguments.get("host", ""), arguments.get("command", ""), int(arguments.get("timeout", 60)))
    if name == "fleet_fetch":
        return fleet.fleet_fetch(arguments.get("host", ""), arguments.get("remote_path", ""), arguments.get("local_path", "/tmp"))
    if name == "fleet_put":
        return fleet.fleet_put(arguments.get("host", ""), arguments.get("local_path", ""), arguments.get("remote_path", ""))
    if name == "brain_search":
        return brain.search(arguments.get("query", ""))
    if name == "brain_read":
        return brain.read(arguments.get("name", ""))
    if name == "brain_log":
        return brain.log(arguments.get("text", ""))
    if name == "phone_status":
        return fleetview.phone_status(bool(arguments.get("fresh", False)))
    if name == "fleet_power":
        return fleetview.fleet_power(arguments.get("host", ""), arguments.get("action", ""))
    if name == "fleet_where":
        return fleetview.fleet_where()
    if name == "now":
        return _now()
    if modules.owner(name) is not None:
        return modules.call(name, arguments)
    return "unknown tool"


def _content(message):
    return (message.get("content") or "").strip()


def run_agent(user_text, cfg, brain=None, ctx=None):
    history = db.get_messages(20)
    facts = db.get_facts(limit=10)
    system = SYSTEM_PROMPT
    voice = persona.persona_text()
    if voice:
        system += "\n\n" + voice
    system += "\n\n" + BRAIN_PROMPT
    if facts:
        system += "\n\nKnown facts:\n" + "\n".join("- " + fact["text"] for fact in facts)
    ctx = ctx or {}
    if ctx.get("device"):
        system += f"\n\nHe typed this from: {ctx['device']}."
    if ctx.get("origin"):
        system += f"\nThe message reached you from {ctx['origin']}."
    if ctx.get("host_line"):
        system += f"\n{ctx['host_line']}"
    chat = [{"role": "system", "content": system}]
    for message in history:
        chat.append({"role": message["role"], "content": message["text"]})
    if not chat or chat[-1]["role"] != "user" or chat[-1]["content"] != user_text:
        chat.append({"role": "user", "content": user_text})
    last = {}
    objects = []
    steps = []
    deadline = time.monotonic() + RUN_BUDGET_S
    try:
        for _ in range(MAX_ROUNDS):
            last = llm.chat(chat, tools=TOOLS, brain=brain)
            chat.append(last)
            calls = last.get("tool_calls") or []
            if not calls:
                if _content(last):
                    return (_content(last), objects)
                break
            if time.monotonic() > deadline:
                chat.append({"role": "user", "content": "Time budget spent. Finish now in plain words: what you did, what is left."})
                break
            for call in calls:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    arguments = {}
                step = f"{function.get('name', 'tool')}: {json.dumps(arguments)[:120]}"
                steps.append(step)
                if ctx.get("chat"):
                    publish_step(ctx["chat"], step)
                try:
                    refused = lockguard.refusal(function.get("name", ""), ctx["guard_conv"], "api", arguments) if ctx.get("guard_conv") else None
                    result = refused or _call_tool(function.get("name", ""), arguments, ctx)
                except Exception as exc:
                    result = f"tool error: {exc}"
                logger.info("tool %s(%s) -> %s", function.get("name"), json.dumps(arguments)[:200], result[:200])
                chat.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
                if function.get("name") == "emit_object" and not object_problem(arguments):
                    try:
                        objects.append(keep_object(arguments))
                    except (OSError, ValueError):
                        logger.exception("could not keep the object")
                if function.get("name") == "hue_light" and "refused" not in result and "did not answer" not in result:
                    lamps = hue.lamp_states()
                    if lamps:
                        objects.append({"kind": "lights", "lamps": lamps})
                if function.get("name") == "workspace_todo":
                    ws = str(arguments.get("workspace") or ctx.get("workspace") or "scratch")
                    objects.append({"kind": "todos", "workspace": ws, "todos": db.get_todos(ws)})
        chat.append({"role": "user", "content": FINAL_NUDGE})
        last = llm.chat(chat, brain=brain)
    except llm.LLMError as error:
        logger.warning("agent run ended in llm error: %s", error)
        return (FALLBACK, objects)
    return (_content(last) or FALLBACK, objects)
