"""The time here, or in a named place, from the system's own time zone database.

Places resolve through a short alias list, then the canonical zones and countries in the tz
database's zone.tab and iso3166.tab. A place that matches nothing, or a country that spans
several zones, resolves to nothing rather than to a guess.
"""

import datetime
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError

ALIASES = {
    "quebec": "America/Toronto",
    "quebec city": "America/Toronto",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "new york city": "America/New_York",
    "nyc": "America/New_York",
    "washington dc": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "austin": "America/Chicago",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "aarhus": "Europe/Copenhagen",
    "odense": "Europe/Copenhagen",
    "munich": "Europe/Berlin",
    "hamburg": "Europe/Berlin",
    "spain": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "portugal": "Europe/Lisbon",
    "milan": "Europe/Rome",
    "florence": "Europe/Rome",
    "venice": "Europe/Rome",
    "naples": "Europe/Rome",
    "krakow": "Europe/Warsaw",
    "edinburgh": "Europe/London",
    "manchester": "Europe/London",
    "china": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "korea": "Asia/Seoul",
    "kyoto": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "new zealand": "Pacific/Auckland",
    "utc": "Etc/UTC",
    "gmt": "Etc/GMT",
}

COUNTRY_ALIASES = {
    "usa": "US", "us": "US", "america": "US", "united states of america": "US",
    "uk": "GB", "united kingdom": "GB", "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "holland": "NL", "uae": "AE", "czechia": "CZ",
}

ACRONYMS = {"uk", "usa", "us", "nyc", "dc", "uae", "utc", "gmt"}


def _key(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s/,-]", " ", text.lower().replace("_", " ").replace("&", " and "))
    text = re.sub(r"^\s*(?:in|at)\s+", "", text)
    text = re.sub(r"^\s*the\s+", "", text)
    return " ".join(text.split())


def _tables():
    for root in TZPATH:
        try:
            return (Path(root, "zone.tab").read_text(encoding="utf-8"),
                    Path(root, "iso3166.tab").read_text(encoding="utf-8"))
        except OSError:
            continue
    return "", ""


def _rows(text):
    return [line.split("\t") for line in text.splitlines() if line.strip() and not line.startswith("#")]


@lru_cache(maxsize=1)
def _index():
    zone_tab, iso = _tables()
    zone_country, country_zones = {}, {}
    for row in _rows(zone_tab):
        if len(row) >= 3:
            zone_country[row[2]] = row[0]
            country_zones.setdefault(row[0], []).append(row[2])
    countries, clashes = {}, set()
    for row in _rows(iso):
        if len(row) < 2:
            continue
        found = re.match(r"^(.*?)\s*(?:\((.*)\))?$", row[1].strip())
        base, extra = found.group(1), found.group(2)
        keys = [_key(base)]
        if extra:
            keys.append(_key(extra) if extra.isupper() else _key(f"{extra} {base}"))
        for key in keys:
            if key in countries and countries[key] != row[0]:
                clashes.add(key)
            countries.setdefault(key, row[0])
    for key in clashes:
        countries.pop(key, None)
    countries.update({key: cc for key, cc in COUNTRY_ALIASES.items()})
    by_name, by_city = {}, {}
    for zone in sorted(zone_country):
        by_name[_key(zone)] = zone
        by_city.setdefault(_key(zone.rsplit("/", 1)[-1]), zone)
    return zone_country, country_zones, countries, by_name, by_city


def _lookup(key):
    """(zone or None, the countries the words name)."""
    zone_country, country_zones, countries, by_name, by_city = _index()
    zone = ALIASES.get(key) or by_name.get(key) or by_city.get(key)
    if zone:
        return zone, {zone_country.get(zone)} - {None}
    cc = countries.get(key)
    if cc:
        zones = country_zones.get(cc) or []
        return (zones[0] if len(zones) == 1 else None), {cc}
    return None, set()


def _display(key, zone):
    if "/" in key:
        return zone.rsplit("/", 1)[-1].replace("_", " ")
    return " ".join(word.upper() if word in ACRONYMS else word.capitalize() for word in key.split())


def match(place):
    """(IANA zone, the place's name to say) for free text like "Quebec" or "Montreal, Canada", or None.
    A qualifier after a comma has to agree with the match, so "Perth, Scotland" is not Australia."""
    key = _key(place)
    if not key:
        return None
    zone, _ = _lookup(key)
    if zone:
        return zone, _display(key, zone)
    head, comma, rest = key.partition(",")
    head, rest = head.strip(), rest.strip()
    if not comma or not head or not rest:
        return None
    zone, _ = _lookup(head)
    _, where = _lookup(rest)
    if zone and _index()[0].get(zone) in where:
        return zone, _display(head, zone)
    return None


def resolve(place):
    """The IANA zone for a free text place, or None when it is not known. Never a guess."""
    found = match(place)
    return found[0] if found else None


def local(place, at=None):
    """(name, zone, the moment there) for a place, or None when its zone is not known here."""
    found = match(place)
    if found is None:
        return None
    zone, name = found
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name, zone, (at or datetime.datetime.now(datetime.timezone.utc)).astimezone(tz)


def now(place=None, at=None):
    """The time at home exactly as before, or the time in place: weekday, date, clock and zone."""
    place = str(place or "").strip()
    if not place:
        return (at or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    there = local(place, at)
    if there is None:
        return f"I do not know which time zone {place} is in."
    name, zone, moment = there
    return f"{name}: {moment.strftime('%A %-d %B %Y, %H:%M')}, {zone} ({moment.strftime('%Z')})."
