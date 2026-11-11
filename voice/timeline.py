"""Where a voice turn's time goes, from his name to the thing being done.

Every turn keeps a Timeline: points in milliseconds from the moment the name ended, plus the
hub's own split (Jev, the brain) and a few durations measured inside a step. Finished turns
are appended to ~/.local/state/diablo/turns.jsonl, and `diablo-ears timings` draws them as a
waterfall in the terminal or as an HTML page.
"""

import html
import json
import time
from pathlib import Path

LOG = Path.home() / ".local" / "state" / "diablo" / "turns.jsonl"
KEEP = 2000

# The steps of a turn, as (label, from point, to point), drawn in this order. A step whose
# points are missing is left out, so a loop line (no wake) or a reply cut off still draws.
STEPS = [
    ("wake word", "name", "fired"),
    ("wake checks", "fired", "chime"),
    ("mic queue", "fired", "read"),
    ("wake clip", "read", "clip"),
    ("dictation", "clip", "dictation"),
    ("echo", "dictation", "echo"),
    ("voiceprint", "echo", "voiceprint"),
    ("turn start", "voiceprint", "chime"),
    ("waiting for you", "chime", "speech_start"),
    ("you talking", "speech_start", "speech_end"),
    ("quiet wait", "speech_end", "turn_end"),
    ("voice check", "turn_end", "voice_ok"),
    ("speech to text", "voice_ok", "text"),
    ("first line back", "hub_sent", "ack_back"),
    ("first line voice", "ack_back", "ack_ready"),
    ("first line spoken", "ack_ready", "ack_spoken"),
    ("hub", "hub_sent", "hub_back"),
    ("doing it", "hub_back", "act_done"),
    ("voice fetch", "hub_back", "tts_ready"),
    ("speaking", "tts_ready", "spoken"),
]


class Timeline:
    def __init__(self, kind="wake", start=None, clock=time.monotonic):
        self.clock = clock
        self.t0 = clock() if start is None else start
        self.points = {}
        self.info = {"kind": kind, "at": time.time()}

    def mark(self, name, at=None):
        """A point, once: the first mark of a name wins."""
        if name not in self.points:
            self.points[name] = round(((self.clock() if at is None else at) - self.t0) * 1000)

    def note(self, **values):
        self.info.update({k: v for k, v in values.items() if v is not None})

    def rebase(self, name):
        """Moves the clock's zero to a point, so a loop line counts from his first word."""
        if name not in self.points:
            return
        shift = self.points[name]
        self.t0 += shift / 1000
        self.points = {k: v - shift for k, v in self.points.items()}

    def add_ms(self, name, ms):
        self.info[name] = round(self.info.get(name, 0) + ms)

    def row(self):
        return {**self.info, "points": dict(self.points)}

    def save(self, path=None):
        """Appends the turn; the file keeps its newest KEEP turns."""
        path = Path(path or LOG)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(json.dumps(self.row()) + "\n")
            lines = path.read_text().splitlines()
            if len(lines) > KEEP + 200:
                path.write_text("\n".join(lines[-KEEP:]) + "\n")
        except OSError:
            pass


def load(path=None, last=20):
    path = Path(path or LOG)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-last:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def steps(row):
    """[(label, start ms, end ms)] for one turn, in drawing order."""
    points = row.get("points") or {}
    out = []
    for label, start, end in STEPS:
        if start in points and end in points and points[end] >= points[start]:
            out.append((label, points[start], points[end]))
    return out


def total(row):
    points = row.get("points") or {}
    return max(points.values()) if points else 0


def hub_split(row):
    hub = row.get("hub") or {}
    parts = []
    if hub.get("jev_ms") is not None:
        parts.append(f"jev {hub['jev_ms']} ms")
    if hub.get("first_text_ms") is not None:
        parts.append(f"first line {hub['first_text_ms']} ms")
    if hub.get("brain_ms") is not None:
        parts.append(f"hub turn {hub['brain_ms']} ms")
    if hub.get("model"):
        parts.append(f"{hub.get('backend', '')} {hub['model']}".strip())
    return ", ".join(parts)


def text_report(rows, width=50):
    out = []
    for row in rows:
        whole = max(total(row), 1)
        stamp = time.strftime("%H:%M:%S", time.localtime(row.get("at", 0)))
        said = str(row.get("text") or "")[:60]
        out.append(f"{stamp}  {row.get('kind', '')}  \"{said}\"  {whole / 1000:.1f} s  {row.get('outcome', '')}")
        for label, start, end in steps(row):
            left = round(start / whole * width)
            size = max(1, round((end - start) / whole * width))
            out.append(f"  {label:16} {' ' * left}{'#' * size}{' ' * max(0, width - left - size)} {end - start:6} ms")
        split = hub_split(row)
        if split:
            out.append(f"  {'':16} hub: {split}")
        extra = [f"{k} {row[k]} ms" for k in ("probe_ms", "stt_ms", "voice_check_ms", "act_ms") if k in row]
        if extra:
            out.append(f"  {'':16} inside: " + ", ".join(extra))
        out.append("")
    return "\n".join(out) if out else "no turns timed yet: say Diablo and ask something"


COLORS = {
    "wake word": "var(--chart-4)", "wake checks": "var(--chart-4)", "waiting for you": "var(--muted)", "you talking": "var(--chart-2)",
    "quiet wait": "var(--chart-3)", "voice check": "var(--chart-5)", "speech to text": "var(--chart-6)",
    "hub": "var(--chart-1)", "doing it": "var(--success, #3c9)", "voice fetch": "var(--chart-5)",
    "speaking": "var(--chart-2)",
}


def html_report(rows):
    """A self-contained page: one card per turn, a waterfall of its steps, newest first."""
    cards = []
    for row in reversed(rows):
        whole = max(total(row), 1)
        stamp = time.strftime("%H:%M:%S", time.localtime(row.get("at", 0)))
        bars = []
        for label, start, end in steps(row):
            left, size = start / whole * 100, max((end - start) / whole * 100, 0.4)
            bars.append(
                f'<div class="r"><div class="l">{label}</div><div class="t">'
                f'<div class="b" style="left:{left:.2f}%;width:{size:.2f}%;background:{COLORS.get(label, "var(--chart-1)")}"></div>'
                f'</div><div class="v">{(end - start) / 1000:.2f} s</div></div>')
        split = hub_split(row)
        inside = [f"{k.replace('_ms', '').replace('_', ' ')} {row[k]} ms"
                  for k in ("probe_ms", "stt_ms", "voice_check_ms", "act_ms") if k in row]
        foot = " · ".join(x for x in [("hub: " + split) if split else "", ("inside: " + ", ".join(inside)) if inside else ""] if x)
        cards.append(
            f'<section class="c"><div class="h"><span class="s">{html.escape(str(row.get("text") or "(nothing heard)"))}</span>'
            f'<span class="m">{stamp} · {html.escape(str(row.get("kind", "")))} · {html.escape(str(row.get("outcome", "")))} · '
            f'<b>{whole / 1000:.2f} s</b></span></div>{"".join(bars)}'
            f'{f"<div class=f>{html.escape(foot)}</div>" if foot else ""}</section>')
    body = "".join(cards) or "<p>No turns timed yet: say Diablo and ask something.</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice timings</title><style>
body{{font-family:var(--font-sans,system-ui);margin:0}}
.c{{border:1px solid var(--border,#333);border-radius:var(--radius,8px);padding:12px 16px;margin:0 0 12px;background:var(--card,#111)}}
.h{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
.s{{font-weight:600}} .m{{color:var(--muted-foreground,#999);font-size:13px}}
.r{{display:grid;grid-template-columns:120px 1fr 64px;gap:8px;align-items:center;font-size:13px;margin:3px 0}}
.t{{position:relative;height:14px;background:var(--secondary,#222);border-radius:3px}}
.b{{position:absolute;top:0;height:100%;border-radius:3px}}
.v{{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted-foreground,#999)}}
.f{{margin-top:6px;font-size:12px;color:var(--muted-foreground,#999)}}
</style></head><body>{body}</body></html>"""
