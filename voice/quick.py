"""Questions with one quick answer, answered here with no model: the time, the weather, the bus.

Each returns (words to say, card to show) in well under a second: the clock is local, the
weather one Open-Meteo call, the bus one Rejseplanen departure board. Anything they cannot
answer goes on to the hub as before.
"""

import datetime
import re

import httpx

TIME = re.compile(r"\b(what(?:'s| is)? the time|what time is it|time is it|tell me the time|what'?s the clock)\b", re.I)
WEATHER = re.compile(r"\b(weather|raining|rain today|rain tomorrow|will it rain|temperature outside|how (?:cold|warm|hot) is it|forecast)\b", re.I)
BUS = re.compile(r"\b(bus|buses|next departure|when does the .* leave)\b", re.I)
TOMORROW = re.compile(r"\btomorrow\b", re.I)

# Home and the bus stop are per-box values from ~/.config/diablo/config.toml (home_name,
# home_lat, home_lon, bus_stop_id, bus_stop_name, bus_line, bus_toward), never written here.
# Without them the weather and bus questions go on to the hub like any other line.
REJSEPLAN = "https://www.rejseplan.dk/bin/rest.exe"

SKY = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "fog", 51: "drizzle",
       53: "drizzle", 55: "drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow", 80: "showers",
       81: "showers", 82: "heavy showers", 85: "snow showers", 86: "snow showers", 95: "thunder",
       96: "thunder and hail", 99: "thunder and hail"}


QUESTION = re.compile(r"^\W*(?:diablo\W+)?(?:what|when|how|will|is|does|do|tell me|whats|what's|when's|hey)\b", re.I)
WORK = re.compile(r"\b(add|make|build|create|write|fix|change|thread|card|app|code|dashboard|widget|feature)\b", re.I)
QUICK_WORDS = 10


def answer(text, cfg=None, now=None):
    """(words, card) for a quick question, or None when the line is not one: a short question,
    not a request that only mentions the weather or a bus ("add a weather card to the dashboard")."""
    text = str(text or "")
    if len(text.split()) > QUICK_WORDS or WORK.search(text):
        return None
    if not (QUESTION.search(text) or text.rstrip().endswith("?")):
        return None
    if TIME.search(text):
        return time_answer(now)
    if WEATHER.search(text):
        return weather_answer(tomorrow=bool(TOMORROW.search(text)), cfg=cfg)
    if BUS.search(text) and re.search(r"\b(when|next|how long|leave|come|coming|here|time)\b", text, re.I):
        return bus_answer(cfg=cfg, now=now)
    return None


def time_answer(now=None):
    now = now or datetime.datetime.now()
    clock = now.strftime("%H:%M")
    return f"It's {clock}.", {"kind": "time", "title": "Now", "big": clock,
                              "lines": [now.strftime("%A %-d %B")]}


def _pick(cfg, *keys):
    for key in keys:
        value = cfg.get(key)
        if value not in (None, ""):
            return value
    return None


def home(cfg):
    """{name, lat, lon} from the local config, or None when this box has no home set."""
    cfg = cfg or {}
    lat, lon = _pick(cfg, "home_lat", "home.lat"), _pick(cfg, "home_lon", "home.lon")
    if lat is None or lon is None:
        return None
    return {"name": str(_pick(cfg, "home_name", "home.name") or "home"), "lat": float(lat), "lon": float(lon)}


def weather_answer(tomorrow=False, cfg=None, client=None):
    place = home(cfg)
    if place is None:
        return None
    get = (client or httpx).get
    try:
        data = get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "hourly": "precipitation_probability",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "wind_speed_unit": "ms"}, timeout=4).json()
    except (httpx.HTTPError, ValueError) as error:
        return f"I couldn't get the weather: {error}.", None
    now, day = data.get("current") or {}, data.get("daily") or {}
    i = 1 if tomorrow else 0

    def at(key, default=None):
        values = day.get(key) or []
        return values[i] if len(values) > i else default

    low, high = round(at("temperature_2m_min", 0)), round(at("temperature_2m_max", 0))
    sky, rain = SKY.get(at("weather_code"), "mixed"), at("precipitation_probability_max", 0)
    rain_from = _rain_from(data.get("hourly") or {}, i)
    if tomorrow:
        words = f"Tomorrow {low} to {high} degrees, {sky}" + (f", rain likely from {rain_from}." if rain_from else ".")
        card = {"kind": "weather", "title": f"Tomorrow · {place['name']}", "big": f"{low}° to {high}°",
                "lines": [sky + (f", rain from {rain_from}" if rain_from else ""), f"rain chance {rain}%"]}
    else:
        temp, feels = round(now.get("temperature_2m", 0)), round(now.get("apparent_temperature", 0))
        now_sky = SKY.get(now.get("weather_code"), "mixed")
        words = f"{temp} degrees and {now_sky}" + (f", rain from {rain_from}." if rain_from else ".")
        card = {"kind": "weather", "title": f"Now · {place['name']}", "big": f"{temp}°",
                "lines": [f"{now_sky}, feels like {feels}°, wind {round(now.get('wind_speed_10m', 0))} m/s",
                          f"today {low}° to {high}°" + (f", rain from {rain_from}" if rain_from else f", rain chance {rain}%")]}
    return words, card


def _rain_from(hourly, day_index, threshold=50):
    """The first hour of the day (from now, for today) with rain likelier than threshold."""
    times, chances = hourly.get("time") or [], hourly.get("precipitation_probability") or []
    today = datetime.date.today() + datetime.timedelta(days=day_index)
    hour_now = datetime.datetime.now().hour
    for stamp, chance in zip(times, chances):
        try:
            moment = datetime.datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if moment.date() != today or (day_index == 0 and moment.hour < hour_now):
            continue
        if (chance or 0) >= threshold:
            return moment.strftime("%H:%M")
    return None


def bus_answer(cfg=None, now=None, client=None):
    cfg = cfg or {}
    stop, line = _pick(cfg, "bus.stop_id", "bus_stop_id"), _pick(cfg, "bus.line", "bus_line")
    toward = _pick(cfg, "bus.toward", "bus_toward")
    if not (stop and line and toward):
        return None
    stop, line, toward = str(stop), str(line), str(toward)
    stop_name = str(_pick(cfg, "bus.stop_name", "bus_stop_name") or "the stop")
    get = (client or httpx).get
    try:
        board = get(f"{REJSEPLAN}/departureBoard", params={"id": stop, "format": "json"}, timeout=5,
                    follow_redirects=True).json().get("DepartureBoard") or {}
    except (httpx.HTTPError, ValueError) as error:
        return f"I couldn't reach Rejseplanen: {error}.", None
    rows = board.get("Departure") or []
    rows = rows if isinstance(rows, list) else [rows]
    now = now or datetime.datetime.now()
    upcoming = []
    for row in rows:
        if str(row.get("line") or "") != line and line not in str(row.get("name") or ""):
            continue
        if toward.lower() not in str(row.get("direction") or "").lower():
            continue
        when = _when(row.get("rtDate") or row.get("date"), row.get("rtTime") or row.get("time"), now)
        if when is not None and when >= now - datetime.timedelta(minutes=1):
            upcoming.append((when, bool(row.get("rtTime"))))
    if not upcoming:
        return f"No {line} toward {toward} in the next while.", {
            "kind": "bus", "title": f"{line} → {toward} St. · {stop_name}", "big": "none soon",
            "lines": ["nothing on the board right now"]}
    upcoming.sort()
    minutes = [max(0, round((when - now).total_seconds() / 60)) for when, _ in upcoming[:3]]
    first = "now" if minutes[0] == 0 else f"in {minutes[0]} minute{'s' if minutes[0] != 1 else ''}"
    then = f", then {minutes[1]}" if len(minutes) > 1 else ""
    words = f"The {line} to {toward} comes {first}{then}."
    card = {"kind": "bus", "title": f"{line} → {toward} St. · {stop_name}",
            "big": "now" if minutes[0] == 0 else f"{minutes[0]} min",
            "lines": [("then " + ", ".join(f"{m} min" for m in minutes[1:])) if len(minutes) > 1 else "",
                      " · ".join(when.strftime("%H:%M") for when, _ in upcoming[:3])
                      + ("" if upcoming[0][1] else ", timetable, no live data")]}
    return words, card


def _when(date, clock, now):
    try:
        day = datetime.datetime.strptime(str(date), "%d.%m.%y").date() if date else now.date()
        hour, minute = (int(x) for x in str(clock).split(":"))
        return datetime.datetime.combine(day, datetime.time(hour, minute))
    except (ValueError, TypeError):
        return None
