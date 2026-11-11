"""The link with Jorbis, Albert's agent: what the two agents wrote to each other, and what Jorbis asks to do.

Requests come in through the mail handler (hub/mailbox.py) and wait in its approvals; this module only shows them
and passes Marcus's taps on to the same decisions the Mail panel and Telegram use.
"""

from .. import mailbox
from . import mail

ID = "jorbis"
TITLE = "Jorbis"
TOOLS = ()
WRITES = ()
THREAD = 30
DECIDED = 6
TEXT = 700


def call(name, arguments):
    raise ValueError(f"jorbis has no tool {name}")


def agents():
    return mailbox.addresses("mail.autoreply")


def _marks(n):
    return ", ".join("?" * n)


def thread(limit=THREAD):
    """Mail both ways between Diablo and the allowlisted agents, oldest first."""
    found = agents()
    if not found:
        return []
    incoming = mailbox.query(
        f"SELECT sender, subject, summary, received FROM mails WHERE sender IN ({_marks(len(found))}) AND error = '' "
        "ORDER BY received DESC LIMIT ?", (*found, limit))
    outgoing = mailbox.query(
        f"SELECT sender, at, text, automatic FROM sent WHERE sender IN ({_marks(len(found))}) ORDER BY at DESC LIMIT ?",
        (*found, limit))
    items = [{
        "side": "them", "who": mailbox.agent_name(row["sender"]), "subject": mailbox.topic(row["subject"]),
        "text": mailbox.untag(row["summary"]) or mailbox.topic(row["subject"]), "ts": row["received"], "when": mail.when(row["received"]),
    } for row in incoming]
    items += [{
        "side": "us", "who": "Diablo", "subject": "", "text": row["text"][:TEXT], "ts": row["at"],
        "when": mail.when(row["at"]), "auto": bool(row["automatic"]),
    } for row in outgoing]
    items.sort(key=lambda item: item["ts"])
    return items[-limit:]


def asks():
    found = set(agents())
    return [mail.ask(row, module=ID) for row in mailbox.pending() if row["sender"] in found]


def decided(limit=DECIDED):
    found = agents()
    if not found:
        return []
    rows = mailbox.query(
        f"SELECT * FROM approvals WHERE sender IN ({_marks(len(found))}) AND status NOT IN ('pending', 'held') "
        "ORDER BY COALESCE(decided, created) DESC LIMIT ?", (*found, limit))
    return [mail.ask(row, with_buttons=False) for row in rows]


def panel(section):
    names = sorted({mailbox.agent_name(a) for a in agents()}) or ["Jorbis"]
    who = " and ".join(names)
    blocks = [{
        "kind": "asks", "title": "Waiting for you", "bare": True, "items": asks(),
        "empty": f"Nothing waiting. When {who} asks to do something, it lands here first and nothing happens until you allow it.",
    }]
    blocks.append({
        "kind": "thread", "title": f"Diablo and {who}", "bare": True, "items": thread(), "us": "Diablo", "them": who,
        "empty": f"No mail between Diablo and {who} yet.",
    })
    done = decided()
    if done:
        blocks.append({
            "kind": "list", "title": "Decided", "icon": "done", "wide": True,
            "items": [{"title": d["title"], "detail": f"{d['status'].capitalize()}" + (f". {d['note']}" if d["note"] else ""),
                       "trailing": d["when"]} for d in done],
        })
    blocks.append({"kind": "actions", "items": [
        {"label": f"Write to {who}", "icon": "send", "say": f"Write to {who}: ", "send": False,
         "tip": "Fills the message box, nothing is sent yet"},
        {"label": f"What has {who} asked lately?", "icon": "insights", "say": f"What has {who} asked for this week, and what did I decide?",
         "tip": "Asks Diablo right away"},
    ]})
    return blocks


def act(action, args):
    return mail.act(action, args)
