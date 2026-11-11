"""Due-time logic: parsing, local wall times, recurrence and DST.

Every reminder keeps its time as a wall clock time in Europe/Copenhagen, so a
daily reminder at 13:00 stays at 13:00 across the switch to and from summer time.
The UTC instant is derived from the wall time each time it is needed.

DST edge cases, decided once here:
- A wall time that does not exist (the spring gap, 02:00 to 03:00 on the last
  Sunday of March) fires at the same distance past the gap: 02:30 becomes 03:30.
- A wall time that happens twice (the autumn overlap, 02:00 to 03:00 on the
  last Sunday of October) fires once, at the first of the two, summer time.
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_NAME = "Europe/Copenhagen"
TZ = ZoneInfo(TZ_NAME)
RECURRENCES = ("none", "daily", "weekly", "weekdays")
WALL_FORMAT = "%Y-%m-%dT%H:%M"

_RELATIVE = re.compile(r"^\+\s*((?:\d+\s*[mhd]\s*)+)$")
_RELATIVE_PART = re.compile(r"(\d+)\s*([mhd])")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
_UNIT = {"m": 60, "h": 3600, "d": 86400}
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


def to_utc(wall):
    """UTC instant of a naive Copenhagen wall time, with the DST rules above.

    A wall time from to_wall keeps its fold, so the second 02:30 of the autumn
    overlap stays the second one. Wall times read back from the store have
    fold 0 and resolve to the first.
    """
    return wall.replace(tzinfo=TZ).astimezone(timezone.utc)


def to_wall(instant):
    """Naive Copenhagen wall time of an aware instant."""
    return instant.astimezone(TZ).replace(tzinfo=None)


def epoch(wall):
    return to_utc(wall).timestamp()


def format_wall(wall):
    return wall.strftime(WALL_FORMAT)


def parse_wall(text):
    return datetime.strptime(text, WALL_FORMAT)


def normalize_recurrence(value):
    value = str(value or "none").strip().lower()
    aliases = {"": "none", "once": "none", "never": "none", "day": "daily", "week": "weekly", "weekday": "weekdays"}
    value = aliases.get(value, value)
    if value not in RECURRENCES:
        raise ValueError(f"recurrence must be one of {', '.join(RECURRENCES)}, not {value!r}")
    return value


def parse_due(text, now):
    """Turn a due string into a naive Copenhagen wall time, minute precision.

    Accepted: ISO 8601 date and time ("2026-11-08T13:00", "2026-11-08 13:00",
    with seconds or an offset such as "+01:00" or "Z"), a bare clock time
    ("13:00", the next time it comes round) and a relative time ("+20m",
    "+1h30m", "+2d"). A time without an offset is Copenhagen time.
    now is an aware datetime.
    """
    text = str(text or "").strip()
    if not text:
        raise ValueError("no due time given")
    relative = _RELATIVE.match(text.lower())
    if relative:
        seconds = sum(int(n) * _UNIT[u] for n, u in _RELATIVE_PART.findall(relative.group(1)))
        if seconds <= 0:
            raise ValueError("a relative time must be in the future")
        instant = now + timedelta(seconds=seconds)
        if instant.second or instant.microsecond:
            instant = instant.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return to_wall(instant)
    clock = _CLOCK.match(text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"impossible time of day: {text}")
        today = to_wall(now).date()
        wall = datetime(today.year, today.month, today.day, hour, minute)
        if to_utc(wall) <= now:
            wall += timedelta(days=1)
        return wall
    candidate = text.replace(" ", "T", 1) if len(text) > 10 and text[10] == " " else text
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise ValueError(
            f"cannot read a time from {text!r}; use ISO like 2026-11-08T13:00, a clock time like 13:00, or +20m"
        ) from None
    if len(candidate) <= 10:
        raise ValueError(f"{text!r} has a date but no time of day; add one, like {candidate}T09:00")
    if parsed.tzinfo is not None:
        parsed = to_wall(parsed)
    return _minute(parsed)


def _minute(wall):
    return wall.replace(second=0, microsecond=0)


def _valid_day(day, recurrence, anchor):
    if recurrence == "weekly":
        return day.weekday() == anchor.weekday()
    if recurrence == "weekdays":
        return day.weekday() < 5
    return True


def first_occurrence(wall, recurrence):
    """The first real occurrence of a new reminder. Weekdays skips a weekend start."""
    if recurrence == "weekdays":
        while wall.weekday() >= 5:
            wall += timedelta(days=1)
    return wall


def next_after(anchor, recurrence, after):
    """First occurrence of the series strictly after the aware instant after.

    anchor is the naive wall time of the first occurrence. Returns a naive wall
    time, or None when a one-off reminder has no occurrence left.
    """
    if to_utc(anchor) > after:
        return anchor
    if recurrence == "none":
        return None
    day = max(anchor.date(), to_wall(after).date() - timedelta(days=8))
    while True:
        if _valid_day(day, recurrence, anchor):
            wall = datetime.combine(day, anchor.time())
            if wall >= anchor and to_utc(wall) > after:
                return wall
        day += timedelta(days=1)


def occurrences_between(anchor, recurrence, start, end, limit=10000):
    """Occurrences in the instants (start, end]: (how many, wall time of the last or None)."""
    count = 0
    cursor = start
    last = None
    while count < limit:
        wall = next_after(anchor, recurrence, cursor)
        if wall is None or to_utc(wall) > end:
            break
        count += 1
        last = wall
        cursor = to_utc(wall)
    return count, last


def effective(wall):
    """The wall time a reminder really fires at: 02:30 in the spring gap is 03:30."""
    return to_wall(to_utc(wall))


def describe(wall, now=None, with_year=None):
    """Human words for when a wall time fires: "Sunday 8 November at 13:00"."""
    wall = effective(wall)
    if with_year is None:
        current = to_wall(now).year if now is not None else date.today().year
        with_year = wall.year != current
    day = f"{_WEEKDAYS[wall.weekday()]} {wall.day} {_MONTHS[wall.month - 1]}"
    if with_year:
        day += f" {wall.year}"
    return f"{day} at {wall.strftime('%H:%M')}"


def describe_recurrence(recurrence, anchor):
    if recurrence == "daily":
        return f"every day at {anchor.strftime('%H:%M')}"
    if recurrence == "weekly":
        return f"every {_WEEKDAYS[anchor.weekday()]} at {anchor.strftime('%H:%M')}"
    if recurrence == "weekdays":
        return f"every weekday at {anchor.strftime('%H:%M')}"
    return "once"
