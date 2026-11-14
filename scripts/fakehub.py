#!/usr/bin/env python3
"""A stand-in for the hub's GET /v1/history, for screenshots of the History page.

    fakehub.py PORT

Every line is invented: a few days of a made-up household talking to him on every channel.
It answers the same shape as hub/server.py, pages, search and channel filter included, and only
to the token "preview-token".
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1])
TOKEN = "preview-token"
LINES = [
    ("voice", "Diablo, what's the weather like?", "Seven degrees and cloudy, sir. Rain from four."),
    ("voice", "Remind me to water the basil at six", "I will remind you at six, sir."),
    ("telegram", "Did the parcel come?", "Not yet, sir. The tracker says tomorrow before noon."),
    ("desktop", "Write a short note to the landlord about the dripping tap",
     "Here is a draft, sir:\n\nHello,\nThe kitchen tap has been dripping since Monday. Could someone look at it this week?\n\nThank you."),
    ("voice", "Turn the desk lamp down a bit", "Done, sir."),
    ("phone", "How long to the station?", "Twelve minutes on foot, sir. The next train leaves in twenty."),
    ("voice", "Play something calm", "Playing a calm mix in the living room, sir."),
    ("telegram", "What's on my list for the weekend?", "Three things, sir: the bike tyre, the library books, and the spare key."),
    ("voice", "How many push-ups did I do this week?", "One hundred and forty, sir, across five breaks."),
    ("api", "Rename the parrot photo folder to Pip", "Renamed, sir. The folder is now called Pip."),
    ("telegram", "Plan a quiet Sunday for me",
     "A quiet Sunday, sir, hour by hour:\n\n" + "\n".join(f"{h}:00  " + task for h, task in zip(range(8, 22), [
         "Sleep in, no alarm", "Coffee on the balcony", "A slow walk by the lake", "Read the library book",
         "Lunch: the leftover soup", "Nap, thirty minutes", "Water the basil and the fern", "Tidy the desk",
         "Call your sister", "Bike tyre, finally", "Cook something simple", "Dinner", "One episode, then stop",
         "Lights low, phone away"]))),
]


def build():
    rows, now, mid = [], time.time(), 0
    for n in range(150):
        channel, said, reply = LINES[n % len(LINES)]
        at = now - (150 - n) * 1900
        mid += 1
        rows.append({"id": mid, "ts": at, "role": "user", "text": said, "channel": channel,
                     "spoken": channel == "voice" or (channel == "telegram" and n % 3 == 0), "section": ""})
        mid += 1
        rows.append({"id": mid, "ts": at + 4, "role": "assistant", "text": reply, "channel": channel,
                     "spoken": channel == "voice", "section": ""})
    return rows


ROWS = build()


def page(query):
    before = int((query.get("before") or ["0"])[0] or 0)
    after = int((query.get("after") or ["0"])[0] or 0)
    limit = max(1, min(int((query.get("limit") or ["60"])[0] or 60), 200))
    words = [w.casefold() for w in (query.get("q") or [""])[0].split()]
    channels = [c for c in (query.get("channel") or [""])[0].split(",") if c]
    hits = [r for r in ROWS if (before <= 0 or r["id"] < before) and r["id"] > after and all(w in r["text"].casefold() for w in words)
            and (not channels or r["channel"] in channels)]
    shown = hits[:limit] if after > 0 else hits[-limit:]
    counts = {}
    for r in ROWS:
        counts[r["channel"]] = counts.get(r["channel"], 0) + 1
    return {"messages": shown, "more": len(hits) > limit, "channels": counts, "now": time.time()}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if self.headers.get("X-Diablo-Token") != TOKEN:
            return self.reply(403, {"detail": "forbidden"})
        if url.path != "/v1/history":
            return self.reply(404, {"detail": "not here"})
        self.reply(200, page(parse_qs(url.query)))

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
