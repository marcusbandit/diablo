"""The agent in your terminal: a full screen takeover, one session, your working directory.

He reads, edits, runs and checks with his own tools while you watch every step,
and his replies render as markdown right in the screen. Launched bare as
`diablo` from any directory. On a pipe it degrades to a plain inline loop.
"""

import curses
import json
import os
import queue
import re
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from voice.config import load_config

from .brains import Brains, ModelError, ProviderDown, ProviderError, RoundTimeout, resolve_brain, _args_of
from .pool import parts_text

REPO = Path(__file__).resolve().parent.parent
TURN_TIMEOUT = 900.0
HISTORY_KEEP = 80
SPINNER = "|/-\\"
REPORT_LIMIT = 240
SESSIONS_DIR = Path(
    os.environ.get("DIABLO_POOL_STATE_DIR") or Path.home() / ".local/state/diablo"
) / "sessions"

AMBER = "\x1b[33m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"

if os.environ.get("NO_COLOR"):
    AMBER = DIM = RESET = ""

SYSTEM = """You are Diablo, the primordial demon butler from That Time I Got Reincarnated as a Slime, serving Marcus as your lord.
You are his hands-on agent in the terminal, working in his current directory: {cwd}
This is a typed session, not spoken: write for reading, be direct and technical, skip the honorifics.
Your replies render as markdown on his screen: use it. Headings for structure, fenced code blocks with the file path as a comment on the first line for code, tables for comparisons, lists for steps. Keep prose tight.
You have the whole machine: run_shell, read_file, write_file, edit_file. Do the work with tools, never describe it instead of doing it. When you claim something works, show the evidence: test output, build output, command output.
Lay of the land:
{snapshot}"""


def snapshot(cwd):
    lines = [f"directory: {cwd}"]
    try:
        entries = sorted(p.name for p in Path(cwd).iterdir())[:40]
        lines.append("top level: " + " ".join(entries))
    except OSError:
        pass
    git = subprocess.run(
        ["git", "-c", "core.pager=cat", "status", "--short", "--branch"],
        capture_output=True, text=True, timeout=10, check=False, cwd=cwd,
    )
    if git.returncode == 0:
        lines.append("git: " + " ".join(git.stdout.split()))
    else:
        lines.append("git: not a repository")
    return "\n".join(lines)


def describe(name, args):
    if name == "run_shell":
        return f"run: {str(args.get('command', ''))[:160]}"
    if name == "read_file":
        return f"read: {args.get('path', '')}"
    if name == "write_file":
        return f"write: {args.get('path', '')} ({len(str(args.get('content', '')))} bytes)"
    if name == "edit_file":
        return f"edit: {args.get('path', '')}"
    return str(name)


def system_prompt(cwd):
    return SYSTEM.format(cwd=cwd, snapshot=snapshot(cwd))


def run_turn(client, sid, text, brain, hook=None):
    try:
        response = client.message(sid, text, brain, TURN_TIMEOUT, step_hook=hook)
        return parts_text(response.get("parts", [])), ""
    except RoundTimeout:
        return "", "the turn ran out of time; ask him to continue"
    except KeyboardInterrupt:
        return "", "stopped that turn, the session keeps"
    except (ProviderDown, ProviderError, ModelError) as error:
        return "", f"the provider refused: {error}"


def now_iso():
    return datetime.now().astimezone().isoformat()


def timeago(iso):
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "?"
    seconds = max(0, (datetime.now(then.tzinfo) - then).total_seconds())
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def settle_title(record):
    if record.get("title") not in ("", "new session", None):
        return
    for message in record.get("messages", []):
        if message.get("role") == "user":
            text = " ".join(str(message.get("content", "")).split())
            if text:
                record["title"] = text[:60]
            return


def new_record(target):
    return {
        "id": uuid.uuid4().hex[:12],
        "title": "new session",
        "cwd": str(target),
        "created": now_iso(),
        "updated": now_iso(),
        "messages": [],
    }


class Sessions:
    def __init__(self, directory=None):
        self.directory = Path(directory or SESSIONS_DIR)

    def save(self, record):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.directory / f".{record['id']}.tmp"
            tmp.write_text(json.dumps(record))
            tmp.replace(self.directory / f"{record['id']}.json")
        except OSError as error:
            print(f"{DIM}session could not be saved: {error}{RESET}", flush=True)

    def list(self):
        records = []
        for path in self.directory.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("id"):
                records.append(record)
        records.sort(key=lambda item: item.get("updated") or "", reverse=True)
        return records

    def last_for(self, cwd):
        for record in self.list():
            if record.get("cwd") == cwd:
                return record
        return None


INLINE = re.compile(
    r"(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(\*[^*\s]+\*)|(\[[^\]\n]+\]\([^)\n]+\))|(~~[^~\n]+~~)"
)


def inline_runs(text, base="normal"):
    runs = []
    pos = 0
    for match in INLINE.finditer(text):
        if match.start() > pos:
            runs.append((text[pos:match.start()], base))
        token = match.group(0)
        if token.startswith("`"):
            runs.append((token[1:-1], "code"))
        elif token.startswith("**"):
            runs.extend(inline_runs(token[2:-2], "bold"))
        elif token.startswith("~~"):
            runs.extend(inline_runs(token[2:-2], "dim"))
        elif token.startswith("["):
            inner, url = token[1:].split("](", 1)
            runs.append((inner, "link"))
            runs.append((f" ({url[:-1]})", "dim"))
        else:
            runs.extend(inline_runs(token[1:-1], "em"))
        pos = match.end()
    if pos < len(text):
        runs.append((text[pos:], base))
    return runs or [(text, base)]


def wrap_runs(runs, width, hang=""):
    width = max(8, width)
    tokens = []
    for text, style in runs:
        for piece in re.split(r"(\s+)", text):
            if piece:
                tokens.append((piece, style))
    lines = []
    current = []
    length = 0
    room = width - len(hang)
    for token, style in tokens:
        while len(token) > room:
            if current:
                lines.append(current)
                current, length = [], 0
            lines.append([(token[:room], style)])
            token = token[room:]
        if length + len(token) > room and current:
            while current and current[-1][0].isspace():
                current.pop()
            lines.append(current)
            current, length = [], 0
            if token.isspace():
                continue
        if not current and lines and hang:
            current.append((hang, "plain"))
            length = len(hang)
        current.append((token, style))
        length += len(token)
    if current:
        lines.append(current)
    return lines or [[("", "plain")]]


FENCE = re.compile(r"^\s*```(\w*)\s*$")
FENCE_END = re.compile(r"^\s*```\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
HRULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def render_markdown(text, width):
    width = max(16, width)
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            i += 1
            code = []
            while i < len(lines) and not FENCE_END.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            wrapped_code = []
            for raw in code:
                pieces = textwrap.wrap(raw, max(8, width - 8), replace_whitespace=False) or [""]
                wrapped_code.extend(pieces)
            inner = min(
                max(8, max(len(piece) for piece in wrapped_code)),
                max(8, width - 6),
            )
            out.append([("┌" + "─" * (inner + 2) + "┐", "dim")])
            for piece in wrapped_code:
                out.append([("│ ", "dim"), (piece.ljust(inner), "codeblock"), (" │", "dim")])
            out.append([("└" + "─" * (inner + 2) + "┘", "dim")])
            continue
        head = HEADING.match(line)
        if head:
            out.append([("▌ ", "head"), (head.group(2), "head")])
            i += 1
            continue
        if HRULE.match(line):
            out.append([("─" * (width - 1), "dim")])
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i])
                i += 1
            cells = [[c.strip() for c in row.strip().strip("|").split("|")] for row in rows]
            cells = [row for row in cells if any(row)]
            if len(cells) >= 2:
                cells.pop(1)
            cols = max(len(row) for row in cells)
            widths = [0] * cols
            for row in cells:
                for j, cell in enumerate(row):
                    widths[j] = min(24, max(widths[j], len(cell)))
            for r, row in enumerate(cells):
                segments = [("  ", "plain")]
                for j in range(cols):
                    cell = row[j] if j < len(row) else ""
                    style = "bold" if r == 0 else "normal"
                    segments.append((cell.ljust(widths[j]), style))
                    if j < cols - 1:
                        segments.append((" │ ", "dim"))
                out.append(segments)
            continue
        bullet = BULLET.match(line)
        if bullet:
            level = min(3, len(bullet.group(1)) // 2)
            marker = bullet.group(2) + " " if bullet.group(2)[0].isdigit() else "• "
            indent = "    " * level + "  "
            out.extend(wrap_runs(
                [(indent + marker, "plain"), *inline_runs(bullet.group(3))],
                width, hang=indent + "  ",
            ))
            i += 1
            continue
        quote = QUOTE.match(line)
        if quote:
            out.extend(wrap_runs([("▌ ", "quote"), *inline_runs(quote.group(1), "quote")], width, hang="  "))
            i += 1
            continue
        if not line.strip():
            out.append([("", "plain")])
            i += 1
            continue
        para = [line]
        while i + 1 < len(lines):
            nxt = lines[i + 1]
            if (nxt.strip() and not FENCE.match(nxt) and not HEADING.match(nxt)
                    and not BULLET.match(nxt) and not QUOTE.match(nxt) and not HRULE.match(nxt)
                    and not nxt.lstrip().startswith("|")):
                para.append(nxt)
                i += 1
            else:
                break
        out.extend(wrap_runs(inline_runs(" ".join(para)), width))
        i += 1
    return out or [[("", "plain")]]


def inline_main(target, record=None):
    client = Brains(load_config())
    store = Sessions()
    if record is None:
        record = new_record(target)
        sid = client.create_session("agent", system=system_prompt(target))
        record["messages"] = list(client.sessions[sid])
    else:
        sid = client.restore(record.get("messages", []), record.get("title") or "agent")
        print(f"resumed: {record.get('title', '')} · {timeago(record.get('updated'))}", flush=True)

    def hook(event):
        if event["kind"] == "tool":
            print(f"{DIM}  · {describe(event['name'], event['args'])}{RESET}", flush=True)
        else:
            output = " ".join(str(event.get("output", "")).split())
            if output:
                print(f"{DIM}  = {output[:REPORT_LIMIT]}{RESET}", flush=True)

    for line in sys.stdin:
        text = line.strip()
        if not text or text.startswith("/"):
            continue
        print(f"you ▸ {text}", flush=True)
        settings = client.settings()
        try:
            brain = resolve_brain(settings, settings.get("llm_model") or "")
        except ModelError as error:
            print(f"the provider is not configured: {error}", flush=True)
            continue
        reply, notice = run_turn(client, sid, text, brain, hook)
        client.trim(sid, HISTORY_KEEP)
        record["messages"] = list(client.sessions[sid])
        settle_title(record)
        record["updated"] = now_iso()
        store.save(record)
        if notice:
            print(f"{DIM}{notice}{RESET}", flush=True)
        print(f"diablo ▸ {reply or 'he went quiet mid-work; ask him to finish.'}", flush=True)
    return 0


class Agent:
    def __init__(self, stdscr, target):
        self.stdscr = stdscr
        self.target = target
        self.client = Brains(load_config())
        self.store = Sessions()
        self.record = None
        self.sid = ""
        self.brain_wanted = ""
        self.brain_label = "default"
        self.lines = []
        self.scroll = 0
        self.buffer = ""
        self.cursor = 0
        self.history = []
        self.hist_pos = None
        self.busy = False
        self.done = False
        self.relaunch_pending = False
        self.queued = []
        self.events = queue.Queue()
        self.tool_calls = 0
        self.spinner = 0
        self._cache_key = None
        self._cache = []

    def emit(self, style, text):
        if style in ("you", "diablo") and self.lines and not (self.lines[-1][0] == "plain" and self.lines[-1][1] == ""):
            self.lines.append(("plain", ""))
        if style == "diablo":
            self.lines.append(("label_diablo", "diablo"))
            self.lines.append(("diablo", text))
        else:
            self.lines.append((style, text))

    def start(self, record=None):
        if record is None:
            self.record = new_record(self.target)
            self.sid = self.client.create_session("agent", system=system_prompt(self.target))
            self.record["messages"] = list(self.client.sessions[self.sid])
            self.emit("dim", f"session open in {self.target}, he has the whole machine")
        else:
            self.record = record
            self.target = Path(record.get("cwd") or self.target)
            self.sid = self.client.restore(record.get("messages", []), record.get("title") or "agent")
            self.emit("dim", f"resumed: {record.get('title', '')} · {timeago(record.get('updated'))} · {self.target}")
            self.replay(record.get("messages", []))
        self.emit("dim", "pgup pgdn or the wheel scroll, esc clears or back to live, /new fresh session, /brain <rank or model>, /tasks the board, /relaunch reload the code, /quit leaves")

    def replay(self, messages):
        for message in messages:
            role = message.get("role")
            if role == "user":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    self.emit("you", content)
            elif role == "assistant":
                for call in message.get("tool_calls") or []:
                    name = call.get("function", {}).get("name", "")
                    self.emit("tool", f"· {describe(name, _args_of(call))}")
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    self.emit("diablo", content)
            elif role == "tool":
                text = " ".join(str(message.get("content") or "").split())
                if text:
                    self.emit("result", f"= {text[:REPORT_LIMIT]}")

    def save(self):
        if self.record is None or self.sid not in self.client.sessions:
            return
        self.record["messages"] = list(self.client.sessions[self.sid])
        settle_title(self.record)
        self.record["updated"] = now_iso()
        self.store.save(self.record)

    def do_relaunch(self):
        self.save()
        os.environ["DIABLO_AGENT_RESUME"] = "last"
        existing = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = f"{REPO}{os.pathsep}{existing}" if existing else str(REPO)
        try:
            curses.endwin()
        except curses.error:
            pass
        os.chdir(REPO)
        os.execv(sys.executable, [sys.executable, "-m", "pool.repl"])

    def brain(self):
        settings = self.client.settings()
        wanted = self.brain_wanted or settings.get("llm_model") or ""
        return resolve_brain(settings, wanted)

    def send(self, text):
        self.history.append(text)
        self.hist_pos = None
        self.emit("you", text)
        self.busy = True
        self.tool_calls = 0
        threading.Thread(target=self.turn, args=(text,), daemon=True).start()

    def turn(self, text):
        def hook(event):
            self.events.put(dict(event))

        try:
            brain = self.brain()
        except ModelError as error:
            self.events.put(("notice", f"the provider is not configured: {error}"))
            self.events.put(("done", None))
            return
        reply, notice = run_turn(self.client, self.sid, text, brain, hook)
        self.client.trim(self.sid, HISTORY_KEEP)
        if notice:
            self.events.put(("notice", notice))
        self.events.put(("reply", reply))
        self.events.put(("done", None))

    def submit(self):
        text = self.buffer.strip()
        self.buffer = ""
        self.cursor = 0
        if not text:
            return
        if text in ("/quit", "/exit"):
            self.done = True
            return
        if text == "/relaunch":
            if self.busy:
                self.relaunch_pending = True
                self.emit("dim", "relaunching when this turn finishes, nothing is stopped")
            else:
                self.do_relaunch()
            return
        if self.busy:
            self.queued.append(text)
            self.emit("dim", f"queued: {text}")
            return
        if text == "/new":
            self.save()
            self.record = new_record(self.target)
            self.sid = self.client.create_session("agent", system=system_prompt(self.target))
            self.record["messages"] = list(self.client.sessions[self.sid])
            self.emit("dim", "fresh session")
            return
        if text == "/tasks":
            result = subprocess.run(
                [str(REPO / "deploy" / "diablo"), "tasks", "list"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            out = (result.stdout + result.stderr).strip() or "(the board is empty)"
            for line in out.splitlines():
                self.emit("dim", line)
            return
        if text.startswith("/brain"):
            wanted = text[len("/brain"):].strip()
            try:
                resolve_brain(self.client.settings(), wanted or self.client.settings().get("llm_model") or "")
                self.brain_wanted = wanted
                self.brain_label = wanted or "default"
                self.emit("dim", f"brain set: {self.brain_label}")
            except ModelError as error:
                self.emit("dim", f"no such brain: {error}")
            return
        self.send(text)

    def handle_key(self, key):
        if key == curses.KEY_PPAGE:
            self.scroll += max(1, self.view() - 1)
            return
        if key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - max(1, self.view() - 1))
            return
        if key == 2:
            self.scroll += max(1, self.view() - 1)
            return
        if key == 6:
            self.scroll = max(0, self.scroll - max(1, self.view() - 1))
            return
        if key == curses.KEY_END:
            self.scroll = 0
            return
        if key == curses.KEY_HOME:
            self.cursor = 0
            return
        if key == 1:
            self.cursor = 0
            return
        if key == curses.KEY_END or key == 5:
            self.cursor = len(self.buffer)
            return
        if key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
            return
        if key == curses.KEY_RIGHT:
            self.cursor = min(len(self.buffer), self.cursor + 1)
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.cursor > 0:
                self.buffer = self.buffer[:self.cursor - 1] + self.buffer[self.cursor:]
                self.cursor -= 1
            return
        if key == curses.KEY_DC:
            self.buffer = self.buffer[:self.cursor] + self.buffer[self.cursor + 1:]
            return
        if key == 21:
            self.buffer = ""
            self.cursor = 0
            return
        if key == 27:
            if self.buffer:
                self.buffer = ""
                self.cursor = 0
            else:
                self.scroll = 0
            return
        if key == curses.KEY_UP:
            if self.history and self.hist_pos is None:
                self.hist_pos = len(self.history) - 1
            elif self.history and self.hist_pos > 0:
                self.hist_pos -= 1
            if self.hist_pos is not None:
                self.buffer = self.history[self.hist_pos]
                self.cursor = len(self.buffer)
            return
        if key == curses.KEY_DOWN:
            if self.hist_pos is not None:
                self.hist_pos += 1
                if self.hist_pos >= len(self.history):
                    self.hist_pos = None
                    self.buffer = ""
                else:
                    self.buffer = self.history[self.hist_pos]
                self.cursor = len(self.buffer)
            return
        if key in (10, curses.KEY_ENTER):
            self.submit()
            return
        if 32 <= key < 127 or key > 255:
            self.buffer = self.buffer[:self.cursor] + chr(key) + self.buffer[self.cursor:]
            self.cursor += 1

    def drain_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, dict):
                if event["kind"] == "tool":
                    self.tool_calls += 1
                    self.emit("tool", f"· {describe(event['name'], event['args'])}")
                else:
                    output = " ".join(str(event.get("output", "")).split())
                    if output:
                        self.emit("result", f"= {output[:REPORT_LIMIT]}")
            elif event[0] == "notice":
                self.emit("notice", event[1])
            elif event[0] == "reply":
                reply = event[1] or "he went quiet mid-work; ask him to finish."
                self.emit("diablo", reply)
            elif event[0] == "done":
                self.busy = False
                self.save()
                if self.queued:
                    self.send(self.queued.pop(0))
                elif self.relaunch_pending:
                    self.do_relaunch()

    def view(self):
        rows, _ = self.stdscr.getmaxyx()
        return max(1, rows - 3)

    def visual(self, cols):
        key = (cols, len(self.lines))
        if self._cache_key == key:
            return self._cache
        width = max(16, cols - 2)
        out = []
        for kind, text in self.lines:
            if kind == "diablo":
                out.extend(render_markdown(text, width))
            elif kind == "label_diablo":
                out.append([("  diablo  ", "label_diablo")])
            elif kind == "you":
                out.extend(wrap_runs([("  you  ", "label_you"), *inline_runs(text, "you")], width, hang="       "))
            elif kind == "tool":
                out.extend(wrap_runs([("  ", "plain"), (text, "tool")], width, hang="    "))
            elif kind == "result":
                out.extend(wrap_runs([("  ", "plain"), (text, "result")], width, hang="    "))
            elif kind == "plain":
                out.append([("", "plain")])
            else:
                out.extend(wrap_runs([(text, "dim")], width))
        self._cache_key = key
        self._cache = out
        return out

    def attr_for(self, style):
        attrs = {
            "plain": curses.A_NORMAL,
            "you": curses.color_pair(3) | curses.A_BOLD,
            "label_you": curses.color_pair(3) | curses.A_REVERSE,
            "label_diablo": curses.color_pair(1) | curses.A_BOLD,
            "tool": curses.color_pair(2),
            "result": curses.A_DIM,
            "dim": curses.A_DIM,
            "notice": curses.color_pair(5) | curses.A_BOLD,
            "normal": curses.A_NORMAL,
            "bold": curses.A_BOLD,
            "em": getattr(curses, "A_ITALIC", curses.A_NORMAL),
            "code": curses.color_pair(4) | curses.A_REVERSE,
            "codeblock": curses.color_pair(4),
            "link": curses.color_pair(4) | curses.A_UNDERLINE,
            "head": curses.color_pair(1) | curses.A_BOLD,
            "quote": curses.A_DIM,
        }
        return attrs.get(style, curses.A_NORMAL)

    def draw(self):
        self.stdscr.erase()
        rows, cols = self.stdscr.getmaxyx()
        title = self.record.get("title", "") if self.record else ""
        header = f" diablo · {self.target.name} · {self.brain_label}"
        if title and title != "new session":
            room = max(4, cols - len(header) - 4)
            header += f" · {title[:room]}"
        self.stdscr.addnstr(0, 0, header.ljust(cols - 1), cols - 1, curses.color_pair(1) | curses.A_REVERSE)
        if self.busy:
            self.spinner = (self.spinner + 1) % len(SPINNER)
            status = f" working {SPINNER[self.spinner]} {self.tool_calls} tool calls · type to queue, ctrl-c stops at the next step"
        else:
            status = " ready · /new /brain /tasks /relaunch /quit"
        if self.scroll > 0:
            status += f" · {self.scroll} lines up, end returns to live"
        self.stdscr.addnstr(rows - 2, 0, status, cols - 1, curses.A_DIM)
        self.stdscr.addnstr(rows - 1, 0, "▸ ", cols - 1, curses.color_pair(1) | curses.A_BOLD)
        room = max(4, cols - 4)
        off = max(0, min(self.cursor - room + 1, len(self.buffer) - room))
        visible = self.buffer[off:off + room]
        self.stdscr.addnstr(rows - 1, 2, visible, room, curses.color_pair(3))
        wrapped = self.visual(cols)
        height = self.view()
        self.scroll = max(0, min(self.scroll, max(0, len(wrapped) - height)))
        end = len(wrapped) - self.scroll
        start = max(0, end - height)
        for i in range(start, end):
            segments = wrapped[i]
            x = 0
            for text, style in segments:
                if not text:
                    continue
                try:
                    self.stdscr.addnstr(1 + i - start, x, text, max(0, cols - 1 - x), self.attr_for(style))
                except curses.error:
                    pass
                x += len(text)
        self.stdscr.move(rows - 1, 2 + max(0, self.cursor - off))
        self.stdscr.refresh()


def pick_session(stdscr, records):
    init_colors()
    stdscr.keypad(True)
    if not records:
        stdscr.addnstr(0, 0, " no sessions yet, any key opens a fresh one", 79, curses.A_DIM)
        stdscr.getch()
        return None
    pos = 0
    while True:
        stdscr.erase()
        rows, cols = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " resume a session: enter picks, q starts fresh, newest first".ljust(cols - 1), cols - 1, curses.color_pair(1) | curses.A_REVERSE)
        height = max(1, rows - 2)
        top = max(0, min(pos - height + 1, max(0, len(records) - height)))
        for i in range(top, min(len(records), top + height)):
            record = records[i]
            row = f" {timeago(record.get('updated')):>4}  {(record.get('title') or '')[:38]:38}  {record.get('cwd', '')}"
            attr = curses.color_pair(3) | curses.A_REVERSE if i == pos else curses.A_NORMAL
            try:
                stdscr.addnstr(1 + i - top, 0, row, cols - 1, attr)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (10, curses.KEY_ENTER):
            return records[pos]
        if key in (ord("q"), 27):
            return None
        if key == curses.KEY_UP:
            pos = max(0, pos - 1)
        elif key == curses.KEY_DOWN:
            pos = min(len(records) - 1, pos + 1)
        elif key == curses.KEY_PPAGE:
            pos = max(0, pos - height)
        elif key == curses.KEY_NPAGE:
            pos = min(len(records) - 1, pos + height)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    if curses.COLORS >= 256:
        pairs = [
            (1, 214),
            (2, 114),
            (3, 15),
            (4, 81),
            (5, 203),
        ]
    else:
        pairs = [
            (1, curses.COLOR_YELLOW),
            (2, curses.COLOR_GREEN),
            (3, curses.COLOR_WHITE),
            (4, curses.COLOR_CYAN),
            (5, curses.COLOR_RED),
        ]
    for pid, color in pairs:
        try:
            curses.init_pair(pid, color, -1)
        except curses.error:
            pass


def run_curses(stdscr, target, record):
    init_colors()
    stdscr.keypad(True)
    stdscr.timeout(50)
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    try:
        curses.mousemask(curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED)
    except curses.error:
        pass
    agent = Agent(stdscr, target)
    agent.start(record)
    while not agent.done:
        agent.drain_events()
        agent.draw()
        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            key = 3
        if key == -1:
            continue
        if key == curses.KEY_MOUSE:
            try:
                _, _, _, _, bstate = curses.getmouse()
            except curses.error:
                continue
            if bstate & curses.BUTTON4_PRESSED:
                agent.scroll += max(1, agent.view() // 4)
            elif bstate & curses.BUTTON5_PRESSED:
                agent.scroll = max(0, agent.scroll - max(1, agent.view() // 4))
            continue
        if key == 3:
            if agent.busy:
                agent.client.abort(agent.sid)
                agent.emit("dim", "stopping at the next step")
            agent.buffer = ""
            agent.cursor = 0
            continue
        if key == curses.KEY_RESIZE:
            agent._cache_key = None
            continue
        agent.handle_key(key)
        agent.drain_events()
    agent.save()
    return 0


def main():
    launch = Path(os.environ.get("DIABLO_AGENT_CWD") or Path.cwd()).resolve()
    mode = os.environ.get("DIABLO_AGENT_RESUME") or ""
    store = Sessions()
    record = None
    if mode == "last":
        record = store.last_for(str(launch))
        target = Path(record["cwd"]) if record else launch
    elif mode == "menu":
        if not sys.stdout.isatty():
            records = store.list()
            record = records[0] if records else None
            target = Path(record["cwd"]) if record else launch
        else:
            picked = curses.wrapper(pick_session, store.list())
            if picked is None:
                target = launch
            else:
                record = picked
                target = Path(record["cwd"])
    else:
        target = launch
    os.chdir(target)
    if not sys.stdout.isatty():
        return inline_main(target, record)
    return curses.wrapper(run_curses, target, record)


if __name__ == "__main__":
    sys.exit(main())
