"""Replay real turns through the thread context offline and count tokens before and after.

    python deploy/thread-replay.py migrate COPY.db          file a copy of the hub database into threads (Haiku, once)
    python deploy/thread-replay.py count COPY.db TURNS.json  tokens per turn, measured before and assembled after

COPY.db is a copy of diablo.db (VACUUM INTO), never the live one. TURNS.json comes from the
claude session transcripts: one entry per user turn with its text and each model call's
usage (input, cache read, cache write, output). Before is what the session really carried.
After is the measured fixed prefix, plus the lane's own growth since it was seeded, plus the
assembled thread context, with the same within-turn tool growth. Text is counted at 3.5
characters a token, the rate the transcript shows for these turns.
"""

import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import context, db, settings, threads

CHARS_PER_TOKEN = 3.5
PREFIX = 51593


def tokens(text):
    return round(len(text or "") / CHARS_PER_TOKEN)


def migrate(path):
    db.init(path)
    settings.set_setting("threads.migrated", "")
    asyncio.run(threads.migrate(pause=0))
    print(json.dumps(threads.migration()))
    for thread in db.list_threads(limit=40):
        print(f"{thread['id']}  {thread['title']}: {thread['descriptor']}")


def find(text, rows):
    tail = " ".join(str(text).split())[-160:]
    for row in rows:
        body = " ".join(str(row["text"]).split())
        if row["role"] == "user" and body and (body[-160:] in tail or tail.endswith(body[-120:])):
            return row
    return None


def count(path, turns_path, cap=80000):
    db.init(path)
    turns = [t for t in json.load(open(turns_path)) if t["calls"]]
    rows = db.main_messages(limit=100000, main_sources=db.MAIN_SOURCES, after=0)
    rows = list(reversed(db.main_messages(limit=100000, main_sources=db.MAIN_SOURCES)))
    lane, lane_tokens = None, 0
    out = []
    for i, turn in enumerate(turns):
        first = sum(turn["calls"][0][:3])
        growth = [sum(c[:3]) - first for c in turn["calls"]]
        message = find(turn["text"], rows)
        if message is None:
            continue
        ids = list((message["meta"] or {}).get("threads") or [])
        pick = threads.Pick(relevant=ids[:3], attach=ids[:3], source="replay")
        now = message["ts"] + 1
        assembled = threads.assemble(pick, said=message["text"], now=now) or ""
        new = tokens(message["text"]) + 60
        next_first = sum(turns[i + 1]["calls"][0][:3]) if i + 1 < len(turns) else first
        grew = max(0, next_first - first)
        seeded = not ids or ids[0] != lane or lane_tokens >= cap
        if seeded:
            lane, lane_tokens = (ids[0] if ids else None), PREFIX
            after_first = PREFIX + tokens(assembled) + new
        else:
            after_first = lane_tokens + tokens(assembled) + new
        lane_tokens = after_first + max(0, min(grew, 20000))
        before_total = sum(sum(c[:3]) for c in turn["calls"])
        after_total = sum(after_first + g for g in growth)
        out.append({"ts": turn["ts"], "text": message["text"][:70], "threads": ids, "seeded": seeded,
                    "before": first, "after": after_first, "before_turn": before_total, "after_turn": after_total,
                    "carried_before": first - PREFIX, "context": tokens(assembled), "new": new})
    json.dump(out, sys.stdout, indent=1)
    sys.stderr.write(
        f"turns {len(out)}, first call median before {statistics.median(r['before'] for r in out):.0f} after {statistics.median(r['after'] for r in out):.0f}; "
        f"per turn sum before {sum(r['before_turn'] for r in out)} after {sum(r['after_turn'] for r in out)}\n")


if __name__ == "__main__":
    if sys.argv[1] == "migrate":
        migrate(sys.argv[2])
    else:
        count(sys.argv[2], sys.argv[3])
