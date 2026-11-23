"""Where a spoken turn's time goes, from the end of Marcus's speech to the first word he hears back.

The ears time each turn on their own clock (voice/timeline.py) and the hub times its own part
(brains.stage_times). Once a spoken turn is over the ears send both here (POST
/v1/voice/timing). record() puts them on one line of stages, in ms from the end of his speech,
logs that line as one JSON object in the hub's log and keeps it in voice-turns.jsonl, where
summary() reads the medians and 90th percentiles (GET /v1/voice/timing).

The stages, in order:

    speech_end      0, the end-of-speech detection on the ears
    transcript      the words are ready
    hub_in          the ears hand the words to the hub
    first_token     the model's first token of any kind, thinking included
    first_text      the model's first written word
    first_sentence  the first whole sentence is in the ears' hands
    tts_audio       the first sentence's audio is synthesised
    playback        the first word of the reply plays
    beat            the cached acknowledgement plays, when there is one

The hub's ms count from the moment the request arrived, the ears' from their own clock; the
two meet at hub_in, so the network hop the request took is the only part counted twice.
"""

import json
import logging
import statistics
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATE = Path.home() / ".local" / "state" / "diablo"
LOG_NAME = "voice-turns.jsonl"
KEEP = 500
SUMMARY_TURNS = 20
MODEL_BACKENDS = ("claude", "api", "opencode")
_writing = threading.Lock()

STAGES = ("speech_end", "transcript", "hub_in", "first_token", "first_text", "first_sentence",
          "tts_audio", "playback", "beat")
HUB_STAGES = {"first_token": "first_token_ms", "first_text": "first_text_token_ms"}
EARS_POINTS = {"transcript": ("text",), "hub_in": ("hub_sent",), "first_sentence": ("first_sentence",),
               "tts_audio": ("ack_ready", "tts_ready"), "playback": ("first_audio",), "beat": ("beat_audio",)}
HUB_KEPT = ("hub_start_ms", "guarded_ms", "picked_ms", "locked_ms", "context_ms", "warmed_ms", "sent_ms", "first_token_ms", "first_text_token_ms", "said_ms", "brain_ms",
            "jev_ms", "backend", "model", "effort", "tier", "tools")


def log_path():
    return STATE / LOG_NAME


def _ms(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number) if number == number and abs(number) < 1e8 else None


def stages(points, hub=None):
    """{stage: ms from the end of his speech} for one turn, from the ears' points (ms on their
    clock) and the hub's split (ms from the request's arrival). A stage never reached, or one
    that would come before the end of his speech, is left out."""
    points = {k: _ms(v) for k, v in (points or {}).items()}
    hub = hub or {}
    end = points.get("speech_end")
    if end is None:
        return {}
    out = {"speech_end": 0}
    for stage, names in EARS_POINTS.items():
        found = next((points[name] for name in names if points.get(name) is not None), None)
        if found is not None and found >= end:
            out[stage] = found - end
    sent = points.get("hub_sent")
    for stage, key in HUB_STAGES.items():
        offset = _ms(hub.get(key))
        if sent is not None and offset is not None and sent + offset >= end:
            out[stage] = sent + offset - end
    said = _ms(hub.get("said_ms"))
    if "first_sentence" not in out and sent is not None and said is not None and sent + said >= end:
        out["first_sentence"] = sent + said - end
    return {stage: out[stage] for stage in STAGES if stage in out}


def record(report, now=None):
    """One finished spoken turn from the ears: the line logged and kept, or None when the
    report has no end of speech to count from."""
    report = report if isinstance(report, dict) else {}
    hub = report.get("hub") if isinstance(report.get("hub"), dict) else {}
    found = stages(report.get("points"), hub)
    if not found:
        return None
    row = {"at": round(now if now is not None else time.time(), 3), "kind": str(report.get("kind") or "")[:40],
           "outcome": str(report.get("outcome") or "")[:80], "stages": found,
           "hub": {k: hub[k] for k in HUB_KEPT if hub.get(k) is not None}}
    for key in ("background", "burst"):
        if report.get(key):
            row[key] = True
    logger.info("voice turn %s", json.dumps(row, sort_keys=True))
    _keep(row)
    return row


def _keep(row):
    path = log_path()
    try:
        with _writing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            lines = path.read_text().splitlines()
            if len(lines) > KEEP + 100:
                path.write_text("\n".join(lines[-KEEP:]) + "\n")
    except OSError:
        logger.exception("could not keep the voice turn timing")


def load(last=SUMMARY_TURNS):
    try:
        lines = log_path().read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("stages"), dict):
            rows.append(row)
    return rows[-max(int(last), 1):]


def percentile(values, share):
    """The nearest-rank percentile: the smallest value with at least share of them at or below it."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, -(-len(ordered) * share // 1))
    return ordered[int(rank) - 1]


def summary(rows):
    """{stage: {"n", "median_ms", "p90_ms"}} over the rows, each stage over the turns that reached it."""
    out = {}
    for stage in STAGES[1:]:
        values = [row["stages"][stage] for row in rows if isinstance(row["stages"].get(stage), (int, float))]
        if values:
            out[stage] = {"n": len(values), "median_ms": round(statistics.median(values)),
                          "p90_ms": round(percentile(values, 0.9))}
    return out


def conversational(row):
    """A turn a model answered without running a tool; a direct light or desktop change is not one."""
    hub = row.get("hub") or {}
    return hub.get("tools") == 0 and hub.get("backend") in MODEL_BACKENDS


def report(last=SUMMARY_TURNS):
    """The last turns and their medians: over every spoken turn, and over the conversational ones."""
    rows = load(last)
    talk = [row for row in rows if conversational(row)]
    return {"turns": rows, "summary": summary(rows), "conversational": summary(talk),
            "counted": {"all": len(rows), "conversational": len(talk)}}


def text(found):
    """The report as a few lines for a terminal."""
    lines = []
    for title, key, count in (("every spoken turn", "summary", "all"),
                              ("conversational turns, no tools", "conversational", "conversational")):
        lines.append(f"{title}, last {found['counted'][count]}, ms from the end of his speech:")
        stats = found.get(key) or {}
        if not stats:
            lines.append("  none timed yet")
        for stage in STAGES[1:]:
            if stage in stats:
                each = stats[stage]
                lines.append(f"  {stage:15} median {each['median_ms']:6}  p90 {each['p90_ms']:6}  over {each['n']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
