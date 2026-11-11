"""Diablo's words for a reminder: a refined, courteous butler. No dashes, ever."""

from . import schedule

LATE_AFTER_SECONDS = 120

ON_TIME = (
    "Pardon the interruption. You asked me to remind you: {text}",
    "The hour you named has arrived. {text}",
    "As you requested, a gentle reminder: {text}",
    "A word, as promised. {text}",
)


def is_late(due_at, now):
    return now.timestamp() - due_at > LATE_AFTER_SECONDS


def render(delivery, now):
    """The Telegram text for one delivery row joined with its reminder."""
    text = str(delivery["text"]).strip()
    due = schedule.parse_wall(delivery["due_local"])
    if is_late(delivery["due_at"], now):
        when = schedule.describe(due, now)
        lines = [f"My apologies, this reminder arrives late. It was due {when}, and I was not able to deliver it then.", text]
        missed = int(delivery.get("missed") or 0)
        if missed:
            times = "once more" if missed == 1 else f"{missed} more times"
            lines.append(f"It came due {times} while I was away; I bring it to you just this once.")
    else:
        template = ON_TIME[int(delivery["reminder_id"]) % len(ON_TIME)]
        lines = [template.format(text=text)]
    recurrence = delivery.get("recurrence") or "none"
    if recurrence != "none" and delivery.get("reminder_status") == "active" and delivery.get("next_local"):
        following = schedule.parse_wall(delivery["next_local"])
        lines.append(f"I shall remind you again {schedule.describe(following, now)}.")
    return clean("\n\n".join(lines))


SOURCES = {"cli": "Marcus set it by hand", "diablo": "you set it in a conversation"}


def task(delivery, now):
    """The instruction the hub's brain gets for a scheduled task. It says where the task came from and never claims
    more authority than that: the brain judges it like any other order."""
    due = schedule.parse_wall(delivery["due_local"])
    origin = SOURCES.get(delivery.get("source") or "", "set earlier")
    head = f"[scheduled task #{delivery['reminder_id']} for {schedule.describe(due, now, with_year=True)}, {origin}"
    if is_late(delivery["due_at"], now):
        head += ", running late"
    head += "] Do this now, yourself, then say in one or two lines what you did:"
    return clean(f"{head}\n{str(delivery['text']).strip()}")


def stale_task(delivery, now):
    """A task more than two hours past its time is not run blind; Marcus hears it was missed instead."""
    due = schedule.parse_wall(delivery["due_local"])
    return clean(f"A task you scheduled for {schedule.describe(due, now)} could not run in time, so I left it alone: "
                 f"{str(delivery['text']).strip()}\nTell me if it should still happen.")


def clean(message):
    """Belt and braces for the house rule: no em or en dashes reach Marcus."""
    return message.replace("\u2014", ", ").replace("\u2013", "-")
