"""Usage: how much of every paid service behind Diablo has gone this month, from hub/usage.py."""

import datetime as dt

from .. import usage

ID = "usage"
TITLE = "Usage"
TOOLS = [
    {
        "name": "usage_summary",
        "description": (
            "How much Marcus has used of every paid service behind you this month: Claude Code's plan windows, "
            "OpenRouter and Fireworks spend, Groq Whisper and Cartesia voice. Each line says whether the number "
            "comes from the provider or is counted by the hub."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]
WRITES = ()
STATUS = {"ok": "fine", "near": "near the limit", "out": "out", "off": "not set up"}


def call(name, arguments):
    if name != "usage_summary":
        raise ValueError(name)
    return usage.summary()


def panel(section):
    report = usage.report()
    services = report["services"]
    tiles, rows = [], []
    for service in services:
        figure, words = usage.headline(service)
        tiles.append({"label": service["name"], "value": figure, "icon": "insights"})
        rows.append({
            "title": service["name"],
            "detail": f"{words}. {service['source_note']}",
            "trailing": STATUS.get(service["status"], service["status"]),
        })
    days = services[0]["history"] if services else []
    labels = [dt.date.fromisoformat(day["day"]).strftime("%a %d") for day in days]
    series = [{"name": s["name"], "values": [day["calls"] for day in s["history"]]} for s in services]
    return [
        {"kind": "stats", "items": tiles, "caption": "This month"},
        {"kind": "bars", "title": "Calls a day, last two weeks", "icon": "trend", "labels": labels, "series": series,
         "stacked": True},
        {"kind": "list", "title": "Where each one stands", "icon": "insights", "items": rows},
    ]
