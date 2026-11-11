#!/usr/bin/env python3
"""diablo talk: the terminal door.

Type a line, the fast tongue answers here and out loud. Worker starts and
reports scroll by as the pool runs them. /tasks shows the board, /quit leaves.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

STATE = Path(os.environ.get("DIABLO_PANEL_STATE_DIR") or Path.home() / ".local/state/diablo")
FAST_IN = STATE / "fast.in"
FAST_OUT = STATE / "fast.out"
TASKS = STATE / "tasks"
REPO = Path(__file__).resolve().parent.parent

AMBER = "\x1b[33m"
DIM = "\x1b[2m"
GREEN = "\x1b[92m"
RESET = "\x1b[0m"

if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    AMBER = DIM = GREEN = RESET = ""

POLL_OUT = 0.3
POLL_TASKS = 1.0
REPORT_LIMIT = 400


def stamp(text):
    print(f"{DIM}{time.strftime('%H:%M:%S')}{RESET} {text}", flush=True)


def say_diablo(text):
    print(f"{AMBER}diablo{RESET} {text}", flush=True)


def say_worker(text):
    print(f"{GREEN}worker{RESET} {text}", flush=True)


def watch_out():
    try:
        known = FAST_OUT.stat().st_mtime_ns
    except OSError:
        known = 0
    while True:
        time.sleep(POLL_OUT)
        try:
            now = FAST_OUT.stat().st_mtime_ns
            if now == known:
                continue
            known = now
            text = FAST_OUT.read_text().strip()
            if text:
                say_diablo(text)
        except OSError:
            continue


def watch_tasks():
    known = {}
    if TASKS.exists():
        for path in TASKS.glob("*.status"):
            try:
                known[path.stem] = path.read_text().strip()
            except OSError:
                pass
    while True:
        time.sleep(POLL_TASKS)
        if not TASKS.exists():
            continue
        for path in TASKS.glob("*.status"):
            try:
                value = path.read_text().strip()
            except OSError:
                continue
            tid = path.stem
            if known.get(tid) == value:
                continue
            known[tid] = value
            try:
                name = (TASKS / f"{tid}.name").read_text().strip() or tid
            except OSError:
                name = tid
            if value == "running":
                say_worker(f"started: {name}")
            elif value == "done":
                try:
                    report = (TASKS / f"{tid}.clean").read_text().strip()
                except OSError:
                    report = ""
                say_worker(f"{name}: {report[:REPORT_LIMIT]}" if report else f"finished: {name}")
            elif value == "canceled":
                say_worker(f"canceled: {name}")
            elif value == "interrupted":
                say_worker(f"interrupted: {name}")


def send(text):
    try:
        fd = os.open(FAST_IN, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as error:
        reason = error.strerror or str(error)
        stamp(f"the panel is not listening ({reason}); try: systemctl --user start diablo-fast")
        return
    try:
        os.write(fd, (text + "\n").encode())
    finally:
        os.close(fd)
    print(f"{DIM}you{RESET} {text}", flush=True)


def board():
    try:
        result = subprocess.run(
            [str(REPO / "deploy" / "diablo"), "tasks", "list"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        stamp(f"the board would not show: {error}")
        return
    out = (result.stdout + result.stderr).strip()
    print(out or "(the board is empty)", flush=True)


def main():
    print(f"{AMBER}Diablo, in your terminal.{RESET} Lines go to the fast tongue, answers land here and are spoken too.")
    print(f"{DIM}Worker starts and reports scroll by. /tasks shows the board, /quit leaves.{RESET}", flush=True)
    try:
        last = FAST_OUT.read_text().strip()
        if last:
            stamp(f"his last words: {last}")
    except OSError:
        pass
    threading.Thread(target=watch_out, daemon=True).start()
    threading.Thread(target=watch_tasks, daemon=True).start()
    while True:
        try:
            line = input().strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line == "/tasks":
            board()
            continue
        send(line)


if __name__ == "__main__":
    sys.exit(main())
