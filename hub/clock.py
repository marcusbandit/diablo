"""The time here, or in a named place, from the system's own time zone database."""

import datetime
import re
import unicodedata
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

ALIASES = {
    "quebec": "America/Toronto",
    "quebec city": "America/Toronto",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "new york city": "America/New_York",
    "nyc": "America/New_York",
    "washington": "America/New_York",
    "washington dc": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "austin": "America/Chicago",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "denmark": "Europe/Copenhagen",
    "aarhus": "Europe/Copenhagen",
    "odense": "Europe/Copenhagen",
    "faroe islands": "Atlantic/Faroe",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "finland": "Europe/Helsinki",
    "germany": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "hamburg": "Europe/Berlin",
    "france": "Europe/Paris",
    "spain": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "italy": "Europe/Rome",
    "milan": "Europe/Rome",
    "netherlands": "Europe/Amsterdam",
    "holland": "Europe/Amsterdam",
    "uk": "Europe/London",
    "united kingdom": "Europe/London",
    "britain": "Europe/London",
    "england": "Europe/London",
    "scotland": "Europe/London",
    "wales": "Europe/London",
    "edinburgh": "Europe/London",
    "ireland": "Europe/Dublin",
    "china": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "india": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "south korea": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "kyoto": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "thailand": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "philippines": "Asia/Manila",
    "new zealand": "Pacific/Auckland",
}


def _key(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s/,-]", " ", text.lower().replace("_", " "))
    text = re.sub(r"^\s*(?:in|at)\s+", "", text)
    text = re.sub(r"^\s*the\s+", "", text)
    return " ".join(text.split())


@lru_cache(maxsize=1)
def _cities():
    """Every zone by its full name and by its last part, like "tokyo" for Asia/Tokyo."""
    by_name, by_city = {}, {}
    for zone in available_timezones():
        by_name[_key(zone)] = zone
        city = _key(zone.rsplit("/", 1)[-1])
        if city and (city not in by_city or zone.count("/") < by_city[city].count("/")):
            by_city[city] = zone
    return by_name, by_city


def resolve(place):
    """The IANA zone for a free text place, or None when it is not known. Never a guess."""
    key = _key(place)
    if not key:
        return None
    by_name, by_city = _cities()
    for candidate in dict.fromkeys([key, key.split(",")[0].strip()]):
        zone = ALIASES.get(candidate) or by_name.get(candidate) or by_city.get(candidate)
        if zone:
            return zone
    return None


def now(place=None, at=None):
    """The time at home exactly as before, or the time in place: weekday, date, clock and zone."""
    place = str(place or "").strip()
    if not place:
        return (at or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    zone = resolve(place)
    if zone is None:
        return f"I do not know which time zone {place} is in."
    try:
        tz = ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        return f"I do not know which time zone {place} is in."
    local = (at or datetime.datetime.now(datetime.timezone.utc)).astimezone(tz)
    return f"{place}: {local.strftime('%A %-d %B %Y, %H:%M')}, {zone} ({local.strftime('%Z')})."
