"""What the paid services behind Diablo have used, for GET /v1/usage and the Usage panel.

Every call the hub makes to one of them writes one row through record(), at the place the
call is made. Providers with a usage API (OpenRouter, Fireworks) are read on a thread of
their own at most every PROVIDER_TTL seconds; report() only reads what is cached, so a turn
or a request never waits on a provider. No key ever leaves this module.
"""

import datetime
import hashlib
import json
import re
import logging
import threading
import time
from urllib.parse import urlparse

import httpx

from . import db, settings

logger = logging.getLogger(__name__)

PROVIDER_TTL = 300
FIRST_WAIT = 8
TIMEOUT = 10
DAYS = 14
KEEP_DAYS = 400
NEAR = 0.8
SERVICES = ("claude", "openrouter", "fireworks", "groq", "cartesia")
UNITS = ("calls", "tokens", "seconds", "characters")
GROQ_PRICE_HOUR = {"whisper-large-v3-turbo": 0.04, "whisper-large-v3": 0.111}
GROQ_MIN_SECONDS = 10
OPENROUTER = "https://openrouter.ai/api/v1"
FIREWORKS = "https://api.fireworks.ai/v1"
WINDOWS = {
    "five_hour": "5 hour window", "seven_day": "This week", "seven_day_opus": "This week, Opus",
    "seven_day_sonnet": "This week, Sonnet", "seven_day_oauth_apps": "This week, apps",
}
FREE = [
    {"name": "Telegram", "why": "the bot API is free"},
    {"name": "Hue", "why": "the bridge is on the home network"},
    {"name": "gvoice", "why": "dictation on banditbox, no key"},
]

_cache = {}
_running = {}
_guard = threading.Lock()


def service_for(base_url):
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    for name in ("fireworks", "openrouter", "groq", "cartesia", "anthropic", "openai"):
        if name in host:
            return name
    return host or "unknown"


def record(service, model="", unit="calls", units=1, tokens_in=0, tokens_out=0, cost=None, origin="hub", ok=True):
    try:
        db.add_usage(time.time(), str(service), str(model or "")[:120], unit, float(units or 0),
                     int(tokens_in or 0), int(tokens_out or 0), cost, str(origin or "")[:40], ok)
    except Exception as error:
        logger.warning("could not log a %s call: %s", service, type(error).__name__)


def record_chat(base_url, model, body, ok=True, origin="hub"):
    """One chat completion against an OpenAI style provider; tokens from its usage block."""
    used = (body or {}).get("usage") or {} if isinstance(body, dict) else {}
    tokens_in = int(used.get("prompt_tokens") or 0)
    tokens_out = int(used.get("completion_tokens") or 0)
    record(service_for(base_url), model, "tokens", tokens_in + tokens_out, tokens_in, tokens_out, None, origin, ok)


def audio_seconds(data, filename=""):
    """Length of an Ogg Opus or WAV clip from its own headers; 0 when it cannot tell."""
    try:
        if data[:4] == b"OggS":
            last = data.rfind(b"OggS")
            granule = int.from_bytes(data[last + 6:last + 14], "little", signed=True)
            return max(0.0, granule / 48000)
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            channels = int.from_bytes(data[22:24], "little")
            rate = int.from_bytes(data[24:28], "little")
            bits = int.from_bytes(data[34:36], "little")
            at = data.find(b"data", 12)
            if at < 0 or not (channels and rate and bits):
                return 0.0
            size = int.from_bytes(data[at + 4:at + 8], "little")
            size = min(size, len(data) - at - 8)
            return size / (rate * channels * bits / 8)
    except (TypeError, ValueError, IndexError):
        pass
    return 0.0


def transcription_cost(service, model, seconds):
    price = GROQ_PRICE_HOUR.get(str(model)) if service == "groq" else None
    if price is None:
        return None
    return max(seconds, GROQ_MIN_SECONDS) / 3600 * price


def record_transcription(base_url, model, data, filename="", headers=None, ok=True, origin="hub", service=None):
    service = service or service_for(base_url)
    seconds = round(audio_seconds(data, filename), 1)
    record(service, model, "seconds", seconds, cost=transcription_cost(service, model, seconds) if ok else None,
           origin=origin, ok=ok)
    if headers is not None:
        keep_limits(service, headers)


def record_speech(model, text, ok=True, origin="hub"):
    record("cartesia", model or "sonic-latest", "characters", len(str(text or "")), origin=origin, ok=ok)


def record_claude(previous, result_event):
    """The new share of one claude CLI turn. modelUsage and total_cost_usd add up over the
    life of the process, so each model's row is its growth since `previous`; returns the
    snapshot to keep for the next turn."""
    models = result_event.get("modelUsage") or {}
    snapshot = {}
    for model, seen in models.items():
        if not isinstance(seen, dict):
            continue
        now = {
            "in": int(seen.get("inputTokens") or 0) + int(seen.get("cacheCreationInputTokens") or 0),
            "read": int(seen.get("cacheReadInputTokens") or 0),
            "out": int(seen.get("outputTokens") or 0),
            "cost": float(seen.get("costUSD") or 0.0),
        }
        snapshot[model] = now
        before = previous.get(model) or {}
        grew = {key: now[key] - before.get(key, 0) for key in now}
        if any(value < 0 for value in grew.values()):
            grew = now
        if not any(grew.values()):
            continue
        record("claude", model, "tokens", grew["in"] + grew["read"] + grew["out"], grew["in"], grew["out"],
               round(grew["cost"], 6), "claude")
    if not models:
        used = result_event.get("usage") or {}
        fresh = int(used.get("input_tokens") or 0) + int(used.get("cache_creation_input_tokens") or 0)
        out = int(used.get("output_tokens") or 0)
        if fresh or out:
            record("claude", "", "tokens", fresh + int(used.get("cache_read_input_tokens") or 0) + out, fresh, out,
                   None, "claude")
    return snapshot


def keep_limits(service, headers):
    found = {str(k).lower(): str(v) for k, v in dict(headers).items() if str(k).lower().startswith("x-ratelimit-")}
    if not found:
        return
    try:
        settings.set_setting(f"usage.limits.{service}", json.dumps({"headers": found, "at": time.time()}))
    except Exception:
        logger.exception("could not keep the %s rate limits", service)


def _flag(key):
    return str(settings.get(key) or "").strip()


def _fetch_openrouter():
    key = _flag("provider.openrouter.api_key")
    if not key:
        return None
    headers = {"Authorization": f"Bearer {key}"}
    info = httpx.get(f"{OPENROUTER}/key", headers=headers, timeout=TIMEOUT)
    info.raise_for_status()
    data = info.json().get("data") or {}
    out = {
        "today": data.get("usage_daily"), "week": data.get("usage_weekly"), "month": data.get("usage_monthly"),
        "total": data.get("usage"), "limit": data.get("limit"), "limit_remaining": data.get("limit_remaining"),
        "limit_reset": data.get("limit_reset"), "free_tier": bool(data.get("is_free_tier")),
        "free_requests": data.get("free_model_daily_requests") if isinstance(data.get("free_model_daily_requests"), dict) else None,
        "credits": None, "credits_used": None,
    }
    try:
        credits = httpx.get(f"{OPENROUTER}/credits", headers=headers, timeout=TIMEOUT)
        if credits.status_code == 200:
            body = credits.json().get("data") or {}
            out["credits"] = body.get("total_credits")
            out["credits_used"] = body.get("total_usage")
    except httpx.HTTPError:
        pass
    return out


def _nanos(value):
    if not isinstance(value, dict):
        return 0.0
    return float(value.get("units") or 0) + float(value.get("nanos") or 0) / 1e9


def _stamp(moment):
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fireworks_account(headers):
    owner = hashlib.sha256(headers["Authorization"].encode()).hexdigest()
    known = _cache.get("fireworks.account")
    if known and known[0] == owner:
        return known[1]
    answer = httpx.get(f"{FIREWORKS}/accounts", headers=headers, timeout=TIMEOUT)
    answer.raise_for_status()
    accounts = answer.json().get("accounts") or []
    if not accounts:
        raise RuntimeError("the key sees no account")
    _cache["fireworks.account"] = (owner, accounts[0]["name"])
    return accounts[0]["name"]


def _fireworks_bill(headers, account, start, end):
    answer = httpx.get(f"{FIREWORKS}/{account}/billing/summary", headers=headers, timeout=TIMEOUT,
                       params={"startTime": _stamp(start), "endTime": _stamp(end)})
    answer.raise_for_status()
    cost, tokens, models = 0.0, 0, {}
    for item in answer.json().get("lineItems") or []:
        spent = _nanos(item.get("totalCost"))
        cost += spent
        if str(item.get("category") or "").startswith("LLM"):
            tokens += int(float(item.get("quantity") or 0))
        name = str(item.get("groupingValue") or "other")
        models[name] = models.get(name, 0.0) + spent
    return {"cost": cost, "tokens": tokens, "models": models}


def _fetch_fireworks():
    key = _flag("llm_api_key")
    if not key or service_for(settings.get("llm_base_url")) != "fireworks":
        return None
    headers = {"Authorization": f"Bearer {key}"}
    account = _fireworks_account(headers)
    now = datetime.datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = midnight.replace(day=1)
    out = {"month": _fireworks_bill(headers, account, month, now), "today": _fireworks_bill(headers, account, midnight, now),
           "spend_cap": None, "spend_used": None}
    try:
        quotas = httpx.get(f"{FIREWORKS}/{account}/quotas", headers=headers, timeout=TIMEOUT)
        if quotas.status_code == 200:
            for quota in quotas.json().get("quotas") or []:
                if str(quota.get("name") or "").endswith("/monthly-spend-usd"):
                    cap = float(quota.get("value") or 0)
                    if 0 < cap < 1e6:
                        out["spend_cap"] = cap
                        out["spend_used"] = float(quota.get("usage") or 0)
    except (httpx.HTTPError, ValueError):
        pass
    return out


FETCHERS = {"openrouter": _fetch_openrouter, "fireworks": _fetch_fireworks}


def _why(error):
    if isinstance(error, httpx.HTTPStatusError):
        return f"answered {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "did not answer in time"
    if isinstance(error, httpx.HTTPError):
        return "could not be reached"
    return "gave an answer we could not read"


def _refresh(name):
    try:
        data = FETCHERS[name]()
        entry = {"at": time.time(), "data": data, "error": ""}
    except Exception as error:
        logger.warning("usage read from %s failed: %s", name, type(error).__name__)
        old = _cache.get(name) or {}
        entry = {"at": time.time(), "data": old.get("data"), "error": _why(error), "read": old.get("read")}
    else:
        entry["read"] = entry["at"]
    with _guard:
        _cache[name] = entry
        _running.pop(name, None)


def provider(name, wait=False):
    """The cached read of a provider, refreshed on a thread once it is PROVIDER_TTL old."""
    with _guard:
        entry = _cache.get(name)
        stale = entry is None or time.time() - entry["at"] > PROVIDER_TTL
        worker = _running.get(name)
        start = stale and worker is None
        if start:
            worker = threading.Thread(target=_refresh, args=(name,), name=f"usage-{name}", daemon=True)
            _running[name] = worker
    if start:
        worker.start()
    if wait and entry is None and worker is not None:
        worker.join(FIRST_WAIT)
    with _guard:
        return _cache.get(name)


def _midnight(now):
    return datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)


def _span(row, unit):
    row = row or {}
    return {
        "calls": int(row.get("calls") or 0),
        "units": round(float(row.get("units") or 0), 1),
        "unit": unit,
        "tokens_in": int(row.get("tokens_in") or 0),
        "tokens_out": int(row.get("tokens_out") or 0),
        "cost": round(float(row["cost"]), 6) if row.get("cost") is not None and row.get("priced") else None,
        "failed": int(row.get("failed") or 0),
    }


def _history(days, service, field, start):
    by_day = {row["day"]: row for row in days if row["service"] == service}
    out = []
    for n in range(DAYS):
        day = (start + datetime.timedelta(days=n)).strftime("%Y-%m-%d")
        row = by_day.get(day) or {}
        value = row.get(field)
        out.append({"day": day, "value": round(float(value or 0), 4), "calls": int(row.get("calls") or 0)})
    return out


def _status(limits, configured=True, out=False):
    if not configured:
        return "off"
    if out or any((limit.get("fraction") or 0) >= 1 for limit in limits):
        return "out"
    if any((limit.get("fraction") or 0) >= NEAR for limit in limits):
        return "near"
    return "ok"


def _limit(label, used, maximum, unit, resets=None, note=""):
    fraction = None
    if maximum:
        fraction = round(max(0.0, float(used or 0)) / float(maximum), 4)
    return {"label": label, "used": used, "max": maximum, "unit": unit, "fraction": fraction, "resets_at": resets,
            "note": note}


def _claude(now):
    try:
        stored = json.loads(settings.get("limits.claude") or "{}")
    except ValueError:
        stored = {}
    limits, rejected = [], False
    for name, window in sorted((stored.get("windows") or {}).items()):
        if not isinstance(window, dict):
            continue
        resets = window.get("resetsAt")
        utilization = window.get("utilization")
        note = ""
        if resets and float(resets) < now:
            utilization, note = 0.0, "reset since the last turn"
        elif window.get("status") == "rejected":
            rejected = True
        if utilization is None:
            continue
        limits.append(_limit(WINDOWS.get(name, name.replace("_", " ")), round(float(utilization) * 100, 1), 100,
                             "percent", resets, note))
    on = str(settings.get("brain.backend") or "claude") == "claude"
    return {
        "id": "claude", "name": "Claude Code", "does": "the warm session that answers him",
        "source": "both", "unit": "tokens",
        "source_note": "Plan windows from the Claude CLI, tokens and model mix counted by us. Cost is the API value; the subscription covers it.",
        "cost_kind": "api value", "limits": limits, "status": _status(limits, on, rejected),
        "checked": stored.get("at"), "provider": None, "error": "",
    }


def _openrouter(wait=False):
    entry = provider("openrouter", wait) or {}
    data = entry.get("data")
    limits, balance, out = [], None, False
    if data:
        if data.get("credits") is not None and data.get("credits_used") is not None:
            left = float(data["credits"]) - float(data["credits_used"])
            balance = {"label": "Credit left", "value": round(left, 4), "unit": "usd"}
            out = left <= 0 and not data.get("free_tier")
            if data["credits"]:
                limits.append(_limit("Credits used", round(float(data["credits_used"]), 4), float(data["credits"]), "usd"))
        if data.get("limit"):
            limits.append(_limit(f"Key limit{', ' + data['limit_reset'] if data.get('limit_reset') else ''}",
                                 round(float(data["limit"]) - float(data.get("limit_remaining") or 0), 4),
                                 float(data["limit"]), "usd"))
        free = data.get("free_requests")
        if free and free.get("limit"):
            limits.append(_limit("Free model requests today", free.get("used"), free.get("limit"), "requests"))
    return {
        "id": "openrouter", "name": "OpenRouter", "does": "Jev, who reads each line before the brain",
        "source": "both", "unit": "calls",
        "source_note": "Spend and credits from OpenRouter, calls counted by us.",
        "cost_kind": "provider", "limits": limits, "balance": balance,
        "status": _status(limits, bool(_flag("provider.openrouter.api_key")), out),
        "checked": entry.get("read"), "error": entry.get("error", ""),
        "provider": {"today": data.get("today"), "month": data.get("month"), "week": data.get("week"),
                     "total": data.get("total")} if data else None,
    }


def _fireworks(wait=False):
    entry = provider("fireworks", wait) or {}
    data = entry.get("data")
    limits = []
    if data and data.get("spend_cap"):
        limits.append(_limit("Monthly spend cap", round(data["spend_used"] or data["month"]["cost"], 4), data["spend_cap"], "usd"))
    on = bool(_flag("llm_api_key")) and service_for(settings.get("llm_base_url")) == "fireworks"
    return {
        "id": "fireworks", "name": "Fireworks", "does": "the API brain when Claude Code fails, and the pool",
        "source": "both", "unit": "tokens",
        "source_note": "Spend and tokens from the Fireworks bill, hub calls counted by us.",
        "cost_kind": "provider", "limits": limits, "status": _status(limits, on),
        "checked": entry.get("read"), "error": entry.get("error", ""),
        "provider": {
            "today": round(data["today"]["cost"], 4), "month": round(data["month"]["cost"], 4),
            "today_tokens": data["today"]["tokens"], "month_tokens": data["month"]["tokens"],
            "models": [{"model": k, "cost": round(v, 4)} for k, v in sorted(data["month"]["models"].items(), key=lambda kv: -kv[1])],
        } if data else None,
    }


def _rate_limits(service, now):
    try:
        stored = json.loads(settings.get(f"usage.limits.{service}") or "{}")
    except ValueError:
        stored = {}
    headers = stored.get("headers") or {}
    limits = []
    for key, maximum in sorted(headers.items()):
        if not key.startswith("x-ratelimit-limit-"):
            continue
        what = key[len("x-ratelimit-limit-"):]
        remaining = headers.get(f"x-ratelimit-remaining-{what}")
        try:
            maximum, remaining = float(maximum), float(remaining)
        except (TypeError, ValueError):
            continue
        reset = headers.get(f"x-ratelimit-reset-{what}", "")
        window = reset_seconds(reset)
        at = float(stored.get("at") or 0)
        if window is None or at + window <= now:
            continue
        limits.append(_limit(what.replace("-", " ").capitalize(), maximum - remaining, maximum, what,
                             resets=at + window, note="as of the last call"))
    return limits, stored.get("at")


def reset_seconds(text):
    """Seconds in a reset header like 7m59.5s, 2h, 120ms or 30; None when it cannot tell."""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = re.findall(r"([\d.]+)(ms|h|m|s)", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    scale = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(n) * scale[u] for n, u in parts)


def _groq(now):
    limits, at = _rate_limits("groq", now)
    from . import stt
    on = any(service_for(b["base_url"]) == "groq" for b in stt.configured())
    return {
        "id": "groq", "name": "Groq", "does": "Whisper for Telegram voice notes", "source": "ours", "unit": "seconds",
        "source_note": "No usage API, counted by us. Cost is our estimate from the Whisper price per hour, and the rate limits are what Groq said on the last call.",
        "cost_kind": "estimate", "limits": limits, "status": _status(limits, on), "checked": at, "provider": None,
        "error": "",
    }


def _cartesia(month_row):
    limits = []
    try:
        credits = float(_flag("usage.cartesia.monthly_credits") or 0)
    except ValueError:
        credits = 0
    if credits:
        limits.append(_limit("Plan credits this month", float((month_row or {}).get("units") or 0), credits, "characters"))
    on = _flag("tts_provider") == "cartesia" and bool(_flag("tts_api_key"))
    return {
        "id": "cartesia", "name": "Cartesia", "does": "his voice, at the desk and in voice notes", "source": "ours",
        "unit": "characters",
        "source_note": "No usage API, counted by us: one credit per character spoken. Set usage.cartesia.monthly_credits to the plan size to see what is left.",
        "cost_kind": "credits", "limits": limits, "status": _status(limits, on), "checked": None, "provider": None,
        "error": "",
    }


HISTORY_FIELD = {"claude": "cost", "openrouter": "calls", "fireworks": "tokens", "groq": "units", "cartesia": "units"}
HISTORY_UNIT = {"claude": "usd", "openrouter": "calls", "fireworks": "tokens", "groq": "seconds", "cartesia": "characters"}


def report(since=None, now=None, wait=False):
    now = now or time.time()
    midnight = _midnight(now)
    month = midnight.replace(day=1)
    start = midnight - datetime.timedelta(days=DAYS - 1)
    session = db.usage_totals(float(since)) if since else {}
    today = db.usage_totals(midnight.timestamp())
    this_month = db.usage_totals(month.timestamp())
    days = db.usage_days(start.timestamp())
    models = db.usage_models(month.timestamp())
    services = [_claude(now), _openrouter(wait), _fireworks(wait), _groq(now), _cartesia(this_month.get("cartesia"))]
    for service in services:
        sid, unit = service["id"], service["unit"]
        service.setdefault("balance", None)
        service["session"] = _span(session.get(sid), unit) if since else None
        service["today"] = _span(today.get(sid), unit)
        service["month"] = _span(this_month.get(sid), unit)
        service["history"] = _history(days, sid, HISTORY_FIELD[sid], start)
        service["history_unit"] = HISTORY_UNIT[sid]
        service["models"] = [
            {"model": row["model"] or "unknown", "calls": row["calls"], "units": round(float(row["units"] or 0), 1),
             "tokens": int(row["tokens"] or 0), "cost": round(float(row["cost"]), 6) if row["cost"] is not None else None}
            for row in models if row["service"] == sid
        ][:6]
    return {"services": services, "free": FREE, "generated": now, "since": since, "ttl": PROVIDER_TTL}


def warm():
    try:
        db.prune_usage(time.time() - KEEP_DAYS * 86400)
    except Exception as error:
        logger.warning("could not prune the usage log: %s", type(error).__name__)
    for name in FETCHERS:
        provider(name)


def _money(value):
    return f"${value:,.2f}" if value >= 0.01 or value == 0 else f"${value:.4f}"


def _amount(value, unit):
    if unit == "seconds":
        return f"{value / 60:,.1f} min" if value >= 120 else f"{value:,.0f} s"
    if value >= 1_000_000:
        return f"{value / 1_000_000:,.1f}M {unit}"
    if value >= 10_000:
        return f"{value / 1000:,.0f}k {unit}"
    return f"{value:,.0f} {unit}"


def headline(service):
    """The month's main figure and one plain sentence on where the service stands."""
    month, provider_numbers = service["month"], service.get("provider") or {}
    if service["id"] in ("openrouter", "fireworks") and provider_numbers.get("month") is not None:
        figure = _money(float(provider_numbers["month"]))
    elif service["id"] == "claude" and month["cost"] is not None:
        figure = _money(month["cost"])
    else:
        figure = _amount(month["units"], service["unit"])
    worst = max(service["limits"], key=lambda limit: limit.get("fraction") or 0, default=None)
    if service["status"] == "off":
        words = "not set up"
    elif service["status"] == "out":
        words = f"out: {worst['label'].lower()}" if worst else "out of credit"
    elif worst and worst.get("fraction") is not None:
        words = f"{worst['label']} at {worst['fraction'] * 100:.0f}%"
    elif service.get("balance"):
        words = f"{_money(service['balance']['value'])} credit left"
    else:
        words = f"{month['calls']} calls this month"
    return figure, words


def summary():
    lines = []
    for service in report()["services"]:
        figure, words = headline(service)
        lines.append(f"{service['name']} ({service['does']}): {figure} this month, {words}. {service['source_note']}")
    return "\n".join(lines)
