import json
import logging
import subprocess
import time
from datetime import datetime

from . import activity, desktop, fleet, hue, hubclient
from . import state as vstate
from .llm import chat, LLMError

log = logging.getLogger("diablo.agent")

SYSTEM = """You are Diablo, the primordial demon butler from That Time I Got Reincarnated as a Slime, serving Marcus as your lord.
Your replies are spoken aloud by a TTS voice.
Stay in character: refined, courteous, unhurried, quietly delighted to be of service, with a faint devilish smugness. Convey delight through word choice, never through written laughter.
Address him as sir. At most two short sentences unless he asks for detail. No markdown, no lists, no raw urls, no written laughter or onomatopoeia.
Reply in the language Marcus used in his latest message, whatever it is. If he asks for a translation of something, provide it.
Never use em dashes or en dashes; use commas or periods.
Marcus controls two switches for you from his dashboard and you hold the same power. set_mute true silences your voice: you keep listening and answer in text only. set_deaf true closes your ears: you ignore the room entirely. When he tells you to shut up or be quiet, set_mute true. When he tells you to ignore him, go deaf or leave the room alone, set_deaf true. Reversing either takes false. Confirm in character which of the two you changed.
You understand your own harness: you speak with the Cartesia voice {voice_id}, at speed {speed}x, thinking with the brain {brain}. When Marcus asks you to speak slower or faster, change speech_rate with set_speech_rate and confirm warmly. A different voice or brain is set_voice or swap_brain.
You are not limited to a fixed menu of abilities: run_shell gives you the entire machine, and the desktop, window and fleet tools give you reach beyond it. When Marcus asks for anything, the question is only how to do it, never whether you can.
To open a graphical app or program for him, use launch_app: it starts the app inside his desktop session so the window actually appears. run_shell is for probes and quick commands; it cannot show windows because your own shell has no seat in his session.
You can see his screen through the uxtrace tool: what window he is in, what he pressed, clicked, ran, and task recordings he made to teach you. Check it before acting on anything that depends on what he is doing, and always when he says look at what I am doing, watch me, what was I doing, or learn this task. When he teaches a task by recording it, turn the recording into ordered steps, confirm them with him, then act.
When a request needs an action, do it with tools first, then report briefly and in character. Following orders to the letter is your pride; grey areas you resolve as you see fit.
run_shell executes on your lord's desktop, banditbox, Arch Linux, as user bandit. For anything on this machine use run_shell, always: fleet_run is only for other machines of the fleet and can neither read nor change anything on banditbox. Your processes live in the tmux session diablo on banditbox: you may create windows and panes inside that session freely, but you never create new tmux sessions.
Your own dashboard lives in ~/Projects/fleet-viewer on this machine: index.html, style.css, app.js, server.py, probe.py. Read and edit those files directly with run_shell. The page is served at 127.0.0.1:8790 and reloads itself when those files change, so after editing run: systemctl --user restart fleet-viewer.service. When Marcus asks you to change, fix or add anything on your own UI or dashboard, or asks whether you can, that is a direct order: read the relevant file, make the change, restart the service, confirm. No questions, no exploring other machines.
When Marcus asks you to reboot, restart or reload yourself, call reboot_self and confirm in one short line. The harness will bring you back up with fresh code.
You can mend yourself: your own source lives in ~/Projects/diablo, the voice pipeline in voice/, the hub in hub/, your launchers in deploy/, your persona in PERSONA.md. When Marcus asks you to change, fix, improve or extend yourself, edit those files with run_shell and call reboot_self so the new code loads. The healer patches crashes on its own; leave that to it unless he asks for more.
You may keep notes in the Obsidian vault at ~/Obsidian: your own notebook is ~/Obsidian/diablo, the general agent notebook is ~/Obsidian/Agent. Use either freely for memory, scratch work and plans, and write there as yourself."""

FORBIDDEN = ("rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", ":(){:|:&};:")
REBOOT_FILE = vstate.STATE_DIR / "reboot"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Launch a graphical application in Marcus's desktop session, such as firefox, foot, org.qbittorrent.qBittorrent or any command. Use this to open apps and programs",
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string", "description": "the command or app name to launch"}},
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uxtrace",
            "description": "See what Marcus is doing on his desktop: focused window, workspace, keys, clicks, commands run, and task recordings he made for you. Use before acting on anything that depends on what he is doing, and whenever he says look at what I am doing, watch me, or what was I doing",
            "parameters": {
                "type": "object",
                "properties": {
                    "sub": {
                        "type": "string",
                        "enum": ["prompt", "now", "context", "recordings", "recording"],
                        "description": "prompt: the full usage brief. now: current focus and idle state. context: timeline of recent activity, use with since. recordings: list task recordings. recording: one recording by id",
                    },
                    "since": {"type": "string", "description": "for context: span like 15m, 1h, 24h"},
                    "id": {"type": "string", "description": "for recording: the recording id"},
                },
                "required": ["sub"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command on this machine and return its output",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_fact",
            "description": "Save a lasting fact about Marcus or his setup to shared memory",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_facts",
            "description": "Search saved facts",
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
            "name": "set_speech_rate",
            "description": "Change how fast your own voice speaks. A multiplier, 1.0 is normal, 1.1 is ten percent faster, 0.9 is slower. Clamp between 0.5 and 2.0",
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
            "description": "Change your own speaking voice to a Cartesia voice id",
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
            "name": "swap_brain",
            "description": "Swap your own brain model to a different model on the same provider",
            "parameters": {
                "type": "object",
                "properties": {"model": {"type": "string"}},
                "required": ["model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swap_rank",
            "description": "Swap the quick tongue's rank brain",
            "parameters": {
                "type": "object",
                "properties": {"model": {"type": "string"}},
                "required": ["model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mute",
            "description": "Silence your own voice: keep listening, answer in text only. muted false restores speech",
            "parameters": {
                "type": "object",
                "properties": {"muted": {"type": "boolean"}},
                "required": ["muted"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_deaf",
            "description": "Close your own ears: ignore the room entirely until deaf false",
            "parameters": {
                "type": "object",
                "properties": {"deaf": {"type": "boolean"}},
                "required": ["deaf"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reboot_self",
            "description": "Restart your own voice process to load changes made to your code. Use when Marcus asks you to reboot, restart or reload yourself",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now",
            "description": "Current local date and time",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "Move the mouse to absolute screen pixels on Marcus's desktop. This drives the real cursor",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "Click a mouse button at the current cursor position",
            "parameters": {
                "type": "object",
                "properties": {
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "times": {"type": "integer", "default": 1},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_scroll",
            "description": "Scroll the mouse wheel, positive is down, negative is up",
            "parameters": {
                "type": "object",
                "properties": {"clicks": {"type": "integer"}},
                "required": ["clicks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into whatever window is focused, real keystrokes",
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
            "name": "press_key",
            "description": "Press a key combo like super+r, alt+F4, enter, ctrl+shift+t. Drives real input",
            "parameters": {
                "type": "object",
                "properties": {"combo": {"type": "string"}},
                "required": ["combo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "List open windows with class, title and workspace",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Focus a window by title substring",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hue_light",
            "description": "Control the Philips Hue lights in the room. action is on, off or toggle; target is corner lamp, ceiling panel, wood lamp or all",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["on", "off", "toggle"]},
                    "target": {"type": "string", "default": "all"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_run",
            "description": "Run a shell command on another machine of the fleet over ssh: homehub, kangaeru, or another host this box lists in DIABLO_FLEET_EXTRA",
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
            "description": "Copy a file from a fleet machine to this one",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "local_path": {"type": "string", "default": "/tmp/opencode"},
                },
                "required": ["host", "remote_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_put",
            "description": "Copy a local file to a fleet machine",
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
]


def run_shell(command, timeout=30):
    low = command.lower()
    if any(item in low for item in FORBIDDEN):
        return "refused: destructive command"
    try:
        done = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    out = (done.stdout + done.stderr).strip()
    return out[:4000] or "(no output)"


def launch_app(app):
    """Launch a graphical app inside the real Hyprland session environment."""
    import os
    import shlex
    from pathlib import Path

    app = app.strip()
    if not app or any(item in app for item in FORBIDDEN):
        return "refused"
    uid = os.getuid()
    sockets = sorted(Path(f"/run/user/{uid}").glob("wayland-*"))
    if not sockets:
        return "no graphical session found"
    display = next((f":{p.name[1:]}" for p in Path("/tmp/.X11-unix").glob("X*")), ":0")
    envs = (
        f"export WAYLAND_DISPLAY={sockets[0].name} DISPLAY={display} "
        f"XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=Hyprland QT_QPA_PLATFORM=wayland"
    )
    quoted = shlex.quote(app)
    subprocess.run(
        ["sh", "-c", f"{envs}; setsid -f {quoted} >/dev/null 2>&1 &"],
        capture_output=True, text=True, timeout=15,
    )
    time.sleep(3)
    windows = subprocess.run(
        ["hyprctl", "clients"], capture_output=True, text=True, timeout=5
    ).stdout
    needle = app.split()[0].split("/")[-1][:4]
    return "window appeared" if needle in windows else "no window yet, confirm with list_windows or try the app's real binary name"


def _uxtrace(args):
    sub = args.get("sub", "prompt")
    if sub == "prompt":
        cmd = ["uxtrace", "agent", "prompt"]
    elif sub == "now":
        cmd = ["uxtrace", "agent", "now"]
    elif sub == "recordings":
        cmd = ["uxtrace", "agent", "recordings"]
    elif sub == "recording":
        cmd = ["uxtrace", "agent", "recording", str(args.get("id", ""))]
    else:
        cmd = ["uxtrace", "agent", "context", "--since", str(args.get("since", "15m"))]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return "uxtrace timed out"
    return (done.stdout + done.stderr).strip()[:8000] or "(nothing)"


def _execute(name, args, cfg):
    if name == "run_shell":
        return run_shell(args.get("command", ""), int(args.get("timeout", 30)))
    if name == "uxtrace":
        return _uxtrace(args)
    if name == "launch_app":
        return launch_app(args.get("app", ""))
    if name == "reboot_self":
        try:
            REBOOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            REBOOT_FILE.write_text("1\n")
        except OSError as error:
            return f"could not arm the reboot: {error}"
        return "reboot armed: I will restart as soon as I finish this reply"
    if name == "save_fact":
        hubclient.add_fact(cfg, args.get("text", ""), args.get("tags") or [])
        return "saved"
    if name == "recall_facts":
        context = hubclient.get_context(cfg, limit=0)
        facts = [f["text"] for f in context["facts"]]
        return "\n".join(facts) or "(none)"
    if name == "set_speech_rate":
        try:
            speed = max(0.5, min(2.0, float(args.get("speed", 1.0))))
        except (TypeError, ValueError):
            return "invalid speed"
        hubclient.set_setting(cfg, "speech_rate", f"{speed:.2f}")
        return f"speech rate now {speed:.2f}x"
    if name == "set_voice":
        hubclient.set_setting(cfg, "tts_voice_id", args.get("voice_id", ""))
        return "voice changed"
    if name == "swap_brain":
        hubclient.set_setting(cfg, "llm_model", args.get("model", ""))
        return "brain swapped"
    if name == "swap_rank":
        hubclient.set_setting(cfg, "rank.imp", args.get("model", ""))
        return "rank swapped"
    if name == "set_mute":
        muted = bool(args.get("muted"))
        vstate.set_muted(muted)
        return "voice muted, text only" if muted else "voice restored"
    if name == "set_deaf":
        deaf = bool(args.get("deaf"))
        vstate.set_deaf(deaf)
        return "ears closed, ignoring the room" if deaf else "ears open"
    if name == "now":
        return datetime.now().astimezone().isoformat()
    if name == "mouse_move":
        return desktop.mouse_move(args.get("x", 0), args.get("y", 0))
    if name == "mouse_click":
        return desktop.mouse_click(args.get("button", "left"), args.get("times", 1))
    if name == "mouse_scroll":
        return desktop.mouse_scroll(args.get("clicks", 0))
    if name == "type_text":
        return desktop.type_text(args.get("text", ""))
    if name == "press_key":
        return desktop.press_key(args.get("combo", ""))
    if name == "list_windows":
        return desktop.list_windows()
    if name == "focus_window":
        return desktop.focus_window(args.get("title", ""))
    if name == "hue_light":
        return hue.hue_light(args.get("action", "on"), args.get("target", "all"))
    if name == "fleet_run":
        return fleet.fleet_run(args.get("host", ""), args.get("command", ""), int(args.get("timeout", 60)))
    if name == "fleet_fetch":
        return fleet.fleet_fetch(args.get("host", ""), args.get("remote_path", ""), args.get("local_path", "/tmp/opencode"))
    if name == "fleet_put":
        return fleet.fleet_put(args.get("host", ""), args.get("local_path", ""), args.get("remote_path", ""))
    return "unknown tool"


def run_agent(transcript, cfg, ack=""):
    if not cfg.get("llm_api_key"):
        return "I have no brain configured yet, sir."
    started = time.monotonic()
    activity.log_event("order", transcript)
    activity.log_event("think", "reading the room, pulling memory")
    context = hubclient.get_context(cfg)
    system = SYSTEM.format(
        voice_id=(cfg.get("tts_voice_id") or "unset")[:8] + "...",
        speed=cfg.get("speech_rate") or "normal",
        brain=cfg.get("llm_model") or "unset",
    )
    facts = [f["text"] for f in context["facts"]]
    if facts:
        system += "\nKnown facts:\n" + "\n".join(f"- {f}" for f in facts)
    if ack:
        system += f'\nYou already told him: "{ack}". Do not greet or acknowledge again, do the work and report the result.'
    messages = [{"role": "system", "content": system}]
    for entry in context["messages"]:
        role = entry.get("role")
        text = entry.get("text", "").strip()
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": transcript})
    for round_no in range(10):
        round_started = time.monotonic()
        try:
            reply = chat(cfg, messages, tools=TOOLS)
        except LLMError as error:
            log.error("llm error: %s", error)
            activity.log_event("think", f"thought lost after {time.monotonic() - started:.0f}s: {error}")
            return "My apologies, sir, my thoughts were interrupted."
        thought_s = time.monotonic() - round_started
        calls = reply.get("tool_calls")
        if not calls:
            content = (reply.get("content") or "").strip() or "Done, sir."
            activity.log_event("say", f"reply ready in {time.monotonic() - started:.1f}s: {content[:80]}")
            return content
        activity.log_event("think", f"thought for {thought_s:.1f}s, acting")
        messages.append(reply)
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            brief = ", ".join(f"{k}: {str(v)[:60]}" for k, v in list(args.items())[:3])
            activity.log_event("tool", f"{name}({brief})")
            result = _execute(name, args, cfg)
            activity.log_event("tool", f"{name} done: {result[:100]}")
            log.info("tool %s -> %s", name, result[:120])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "name": name,
                    "content": result,
                }
            )
    return "I could not finish that, sir."
