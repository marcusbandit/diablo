"""The card: a full-screen layer on Hyprland's overlay layer, so it sits above fullscreen video and games."""

import datetime as dt
import json
import math
import os
import random
import shutil
import subprocess
import time

import gi

import catalog
import figure
import musclemap

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

# Monocraft draws on a 9 unit grid (120 of 1080 units a pixel), so it only stays crisp at multiples
# of 9px. It does the shouting: the number, the headline, the eyebrow. Everything else is sans,
# and no sans text goes below 14px.
PIXEL = "Monocraft"
SANS = "Adwaita Sans"

SLATE = {
    "bg0": "#08090b", "bg2": "#181b1f", "bg3": "#1d2126", "bg4": "#262b31", "bg5": "#3a4149",
    "fg6": "#545d67", "fg7": "#78838f", "fg8": "#a5aeb8", "fg9": "#d0d7dd", "fg10": "#eef2f5",
    "dim": "#5b8fb0", "mid": "#7fb3d4", "bright": "#b8dcf0", "alarm": "#ff7a4d",
}
RED = "#ff4545"
WIN = "#7ee0a0"
WORKED = "#ff8a4a"  # muscles worked, on the body map and lit on the figure
STRETCHED = "#5fd0c0"  # muscles stretched

# Accent per skip streak. One skip only raises an eyebrow; the heat starts at two.
RAGE = ["#7fb3d4", "#e2bd76", "#e8c25a", "#ff8a4a", RED]
# Saturated versions for the hold fill, so it reads as the level's colour at a glance.
SAT = ["#3f9fe0", "#e9a83c", "#f5bf1a", "#ff7a1f", "#ff2e2e"]
TINT = [0.0, 0.02, 0.06, 0.10, 0.16]
EYEBROW_COLOR = ["#9cc6e0", "#ecc98a", "#f3dc94", "#ffb183", "#ff8a8a"]

EYEBROWS = ["TIME TO MOVE", "YOU SKIPPED ONE", "TWO SKIPS IN A ROW", "THREE SKIPS", "{skips} SKIPS IN A ROW"]

# A coach, not a bully: straight with you while you show up, rougher the longer you dodge, and
# quick to say so when you do well. Tier 0 is no skips; each tier after is one more skip in a row.
INSULTS = {
    "mild": [
        ["{go} {set}, then back to it.", "Up you get. {set} and you're done.",
         "Quick set. You've got this one.", "Stand up, shake it out, {set}."],
        ["You skipped the last one. Not this one.", "One skip is a blip. Two is a habit.",
         "Last one got away. Get this one."],
        ["Two in a row. That's how streaks die.", "Twice now. The chair is winning.",
         "Two skips. Do this one and we're square."],
        ["Three skips. Enough. {go}", "Three in a row. You know better than this.",
         "Three. No more excuses tonight."],
        ["{skips} skips. One set breaks it. Do one.", "{skips} in a row. Even a short set counts.",
         "I'll keep coming back. Every time.", "{skips} skips. {Get}, any number."],
    ],
    "spicy": [
        ["{go} {set}. Let's go.", "Pause it. {Get}: {set}.",
         "Up from the desk. {set}, clean.", "Move your ass. {set}, no excuses."],
        ["You skipped the last one. Not this one, dammit.", "One skip is a blip. Two is a fucking habit.",
         "Missed one. {Get} and make it up."],
        ["Two in a row. What the hell are you doing?", "Twice. You're letting it slide. {go}",
         "Two skips. {Get}, now."],
        ["Three skips. Cut the shit and {get}.", "Three in a row. You're better than this.",
         "Three. You're not tired, you're dodging."],
        ["{skips} skips. {Get}, for fuck's sake.", "{skips} in a row. One set. Any set. Now.",
         "I'm not going away. {skips} skips. {go}", "{skips} skips. Do the damn {ex}."],
    ],
}

# Tier 0 with a run of done cards behind it: say so.
ON_A_ROLL = {
    "mild": ["{m} in a row. Make it {m1}.", "{m} sets straight. Keep the chain going.",
             "{m} done in a row. That's a habit forming."],
    "spicy": ["{m} in a row. Make it {m1}.", "{m} straight. Don't break the chain now.",
              "{m} sets in a row. Hell yes. Keep going."],
}

STALE = {
    "mild": ["{ago} since your last set. Start small, start now.", "Back after {ago}. Easy one to restart."],
    "spicy": ["Last set {ago}. Shake the rust off.", "{ago} without a set. Get back on it."],
}

# Stretch cards don't shout: you can't force a split.
STRETCH_LINES = {
    "mild": ["Breathe out and sink a little deeper.", "Slow and steady. Splits take months.",
             "Relax into it. No bouncing.", "Ease in. Tight today is fine."],
    "spicy": ["Breathe out and sink. Don't bounce.", "Tight hamstrings don't fix themselves.",
              "Hard enough to want out. Never into pain.", "Slow down. Splits aren't rushed."],
}
REST_LINES = ["You trained hard. Just stretch.", "Rest day. Loosen up what you used.",
              "Nothing heavy today. Breathe and hold."]

# Past the day's target every card is a bonus: it still asks, it doesn't press.
BONUS_LINES = {
    "mild": ["Target's in. Another {set} if you feel like it.", "Bonus set. {set}, or pass. Both are fine.",
             "You're done for today. This one's extra.", "Up for another {set}? No pressure tonight."],
    "spicy": ["Target's done. Another {set} if you're feeling it.", "Bonus round. {set}, or don't. Your call.",
              "You already won today. This one's a freebie.", "Extra credit. {set} if you want it, pass if you don't."],
}
BONUS_PASSES = {
    "mild": ["Fair enough. The target's already in.", "No problem. You did the work today.",
             "Sure. Rest up, you earned it."],
    "spicy": ["Fair. You already did the damn work.", "No sweat. The target's in the bag.",
              "Sure. You earned the night off."],
}

# The first move on the card leans on a part that hurts: smaller, and kind about it.
HURT_LINES = {
    "mild": ["Your {part} hurt, so {set} today. Easy ones count.", "Go easy. {set}, slow and clean.",
             "Sore {part}. Just {set}, no more."],
    "spicy": ["Your {part} hurt. Fine, {set}. Easy ones count.", "Go easy. {set}, slow and clean.",
              "Sore {part}. Just {set}, nothing heroic."],
}
HURT_PASSES = ["Fine. Let it heal.", "Good call. Rest it.", "No problem. Next one, if it feels better."]

SAMPLES = {"mild": "Floor. 14 push-ups, then back to it.", "spicy": "Floor. 14 push-ups. Let's go."}

SKIP_HOLD = ["Hold to skip", "Hold to skip again", "Hold to skip a third", "Hold to dodge again", "Hold to dodge again"]

# After a skip, by how many came before it.
SKIP_REACTIONS = {
    "mild": ["Skipped. I'll be back.", "Two now. The next one isn't optional.",
             "Three. You're digging a hole.", "Noted. I'll keep coming.", "Still here. Still coming back."],
    "spicy": ["Skipped. I'll be back, and so will the reps.", "Two. The next one isn't fucking optional.",
              "Three. You're digging a hole.", "Noted. This is getting ugly.", "Still coming back. Every damn time."],
}

# Pixel icons for the intro, one character a pixel: '#' body, 'o' accent.
ICONS = {
    "video": ["###########", "#.........#", "#..oo.oo..#", "#..oo.oo..#", "#..oo.oo..#",
              "#..oo.oo..#", "#.........#", "###########", "....###....", "..#######.."],
    "music": ["....oooooo.", "....oooooo.", "....o....o.", "....o....o.", "....o....o.",
              "....o....o.", ".oooo..oooo", "ooooo.ooooo", "ooooo.ooooo", ".ooo...ooo."],
    "call": ["...#####...", "..#.....#..", ".#.......#.", "#.........#", "#.........#",
             "oo.......oo", "ooo.....ooo", "ooo.....ooo", "ooo.....ooo", ".oo.....oo."],
    "check": ["........##", ".......##.", "##....##..", ".##..##...", "..####....", "...##....."],
    "grudge": ["..#######..", ".#.......#.", "#.#.....#.#", "#..#...#..#", "#.##...##.#",
               "#.........#", "#...###...#", "#..#...#..#", ".#.......#.", "..#######.."],
}

REST_BETWEEN_SETS = 30  # David rests 30 s to a minute between sets
HOLD_W = 420  # the Log it button's width: 360 plus its padding
PAIN_SCALE = 6  # the body map you click when it hurts: big enough to hit a forearm
HURT_W = 150  # "It hurts" beside the skip; the two together are as wide as Log it


def css(accent, tier):
    s = SLATE
    sat = SAT[tier] if accent in RAGE else accent
    t = TINT[tier]
    hot = tier >= 2
    scan = "repeating-linear-gradient(to bottom, alpha(black, 0) 0px, alpha(black, 0) 3px, alpha(black, 0.28) 3px, alpha(black, 0.28) 4px)"
    vignette = f"radial-gradient(ellipse at center, alpha(black, 0) 35%, alpha(black, {0.45 if tier == 3 else 0.6}) 100%)"
    layers = [scan, vignette] if tier >= 4 else [vignette] if tier == 3 else []
    glow = f", 0 0 {60 + 40 * tier}px alpha({accent}, {0.10 * tier})" if hot else ""
    return f"""
* {{ font-family: "{SANS}", "Noto Sans", sans-serif; }}
window.pushup {{ background-color: transparent; }}
.backdrop {{
    background-color: alpha(mix({s['bg0']}, {accent}, {t}), 0.90);
    {f"background-image: {', '.join(layers)};" if layers else ""}
}}
.backdrop.punished {{ background-color: alpha(#0b0b0c, 0.95); background-image: none; }}
.backdrop.won {{ background-color: alpha(mix({s['bg0']}, {WIN}, 0.10), 0.92); background-image: none; }}
.card {{
    background-color: mix({s['bg2']}, {accent}, {t / 2});
    border: {'1px solid ' + s['bg5'] if not hot else f'2px solid alpha({accent}, 0.75)'};
    border-radius: 24px;
    padding: 40px 56px 34px 56px;
    box-shadow: 0 30px 80px alpha(black, 0.6){glow};
}}
.card.punished {{
    background-color: #141517;
    border: 2px solid alpha({RED}, 0.8);
    box-shadow: 0 30px 80px alpha(black, 0.7);
    animation: none;
}}
.card.won {{
    background-color: mix({s['bg2']}, {WIN}, 0.07);
    border: 2px solid alpha({WIN}, 0.85);
    box-shadow: 0 30px 80px alpha(black, 0.6), 0 0 160px alpha({WIN}, 0.28);
    animation: none;
}}
.goal-chip {{ background-color: alpha({WIN}, 0.16); border-radius: 999px; padding: 6px 16px; }}
.goal-chip label {{ color: {WIN}; font-size: 16px; font-weight: 700; }}
@keyframes shake {{
    0% {{ transform: translate(0, 0) rotate(0deg); }}
    3% {{ transform: translate(-16px, 2px) rotate(-1.2deg); }}
    6% {{ transform: translate(16px, -2px) rotate(1.2deg); }}
    9% {{ transform: translate(-12px, 2px) rotate(-0.8deg); }}
    12% {{ transform: translate(12px, -1px) rotate(0.8deg); }}
    15% {{ transform: translate(-6px, 0) rotate(-0.4deg); }}
    18% {{ transform: translate(0, 0) rotate(0deg); }}
    100% {{ transform: translate(0, 0) rotate(0deg); }}
}}
.card.shake {{ animation: shake 3s ease-in-out infinite; }}

.body {{ color: {s['fg8']}; font-size: 16px; }}
.meta {{ color: {s['fg8']}; font-size: 16px; }}
.meta.big {{ color: {s['fg9']}; font-size: 18px; }}
.hint {{ color: {s['fg9']}; font-size: 16px; }}
.keycap {{
    color: {s['fg10']};
    font-size: 14px;
    font-weight: 600;
    border: 1px solid {s['fg7']};
    border-bottom-width: 3px;
    border-radius: 7px;
    padding: 1px 8px;
    min-width: 12px;
}}
.chip {{
    background-color: alpha({s['dim']}, 0.16);
    color: {s['bright']};
    border-radius: 999px;
    padding: 8px 18px;
    font-size: 16px;
}}

scale {{ padding: 12px 0 0 0; }}
scale trough {{ background-color: {s['bg4']}; min-height: 12px; border-radius: 999px; border: none; }}
scale highlight {{ background-color: {accent}; border-radius: 999px; }}
scale slider {{
    background-color: {s['fg10']};
    min-width: 30px; min-height: 30px;
    margin: -10px;
    border-radius: 999px;
    border: none;
    box-shadow: 0 2px 8px alpha(black, 0.5);
}}
scale marks.bottom {{ color: {s['fg8']}; font-size: 14px; font-feature-settings: "tnum"; }}
scale marks.bottom indicator {{ background-color: {s['fg6']}; min-height: 6px; min-width: 2px; }}
scale marks.top indicator {{ background-color: {accent}; min-height: 14px; min-width: 3px; }}

.hold {{ background-color: alpha({accent}, 0.06); border: 2px solid alpha({accent}, 0.45); border-radius: 16px; transition: background-color 120ms ease-out, border-color 120ms ease-out; }}
.hold.hot {{ background-color: alpha({accent}, 0.12); border-color: alpha({accent}, 0.8); }}
.hold-fill {{ background-color: {sat}; border-radius: 14px; }}
.countdown trough {{ background-color: {s['bg4']}; min-height: 4px; min-width: 320px; border: none; border-radius: 4px; }}
.countdown progress {{ background-color: {accent}; min-height: 4px; min-width: 4px; border: none; border-radius: 4px; }}
.countdown.won progress {{ background-color: {WIN}; }}
.countdown.short progress {{ background-color: {s['fg7']}; }}
.countdown.punished progress {{ background-color: {RED}; }}
.chip.below {{ background-color: alpha({s['bg3']}, 0.95); border: 1px solid {s['bg5']}; }}
.hold-label {{ color: {s['fg9']}; font-size: 18px; font-weight: 600; }}
.hold-label.on {{ color: {s['fg10']}; text-shadow: 0 1px 3px alpha(black, 0.8); }}

.panel {{ background-color: {s['bg3']}; border-radius: 16px; padding: 20px; }}
.swatch {{ min-width: 12px; min-height: 12px; border-radius: 3px; }}

.row {{ background-color: {s['bg3']}; border-radius: 16px; padding: 20px; }}
.row-title, .panel-title, .stat-name {{ color: {s['fg10']}; font-size: 18px; font-weight: 600; }}
.row-body {{ color: {s['fg9']}; font-size: 16px; }}
.preview {{
    background-color: {s['bg3']};
    border: 1px solid {s['bg4']};
    border-radius: 16px;
    padding: 20px;
}}
.dot {{ background-color: {s['fg6']}; border-radius: 999px; min-width: 10px; min-height: 10px; }}
.dot.on {{ background-color: {s['mid']}; min-width: 34px; }}

button {{
    border-radius: 12px;
    padding: 12px 26px;
    font-size: 18px;
    font-weight: 700;
    border: none;
    box-shadow: none;
    background-image: none;
    transition: background-color 120ms ease-out, color 120ms ease-out, border-color 120ms ease-out;
}}
button:focus-visible {{ outline: 2px solid alpha({accent}, 0.9); outline-offset: 3px; }}
button.primary {{ background-color: {accent}; color: {s['bg0']}; min-width: 200px; }}
button.primary:hover {{ background-color: mix({accent}, white, 0.22); }}
button.primary:active {{ background-color: mix({accent}, black, 0.15); }}
button.huge {{ min-width: 360px; padding: 18px 30px; font-size: 22px; border-radius: 16px; }}
button.quiet {{ background-color: transparent; color: {s['fg8']}; font-weight: normal; font-size: 16px; }}
button.quiet:hover {{ color: {s['fg10']}; background-color: alpha({s['fg10']}, 0.06); }}
button.quiet:active {{ background-color: alpha({s['fg10']}, 0.12); }}
button:disabled {{ opacity: 0.35; }}
button.choice {{
    background-color: {s['bg3']};
    border: 2px solid {s['bg4']};
    border-radius: 16px;
    padding: 14px 20px;
    font-weight: normal;
}}
button.choice:hover {{ border-color: {s['dim']}; background-color: alpha({s['mid']}, 0.05); }}
button.choice:active {{ background-color: alpha({s['mid']}, 0.14); }}
button.choice.picked {{ border-color: {s['mid']}; background-color: alpha({s['mid']}, 0.10); }}
.choice-body {{ color: {s['fg8']}; font-size: 16px; }}

.stage {{ background-color: {s['bg3']}; border-radius: 16px; }}
button.pill {{
    background-color: alpha({s['dim']}, 0.14);
    color: {s['fg9']};
    border-radius: 999px;
    padding: 8px 10px 8px 18px;
    font-size: 16px;
    font-weight: 600;
}}
button.side {{
    padding: 0 12px 0 18px;
    min-height: 0;
    background-color: alpha({accent}, 0.06);
    border: 2px solid alpha({accent}, 0.45);
    border-radius: 16px;
    color: {s['fg9']};
    font-size: 18px;
    font-weight: 600;
}}
button.side:hover {{ background-color: alpha({accent}, 0.12); border-color: alpha({accent}, 0.8); color: {s['fg10']}; }}
button.pill.first {{ background-color: alpha({accent}, 0.30); color: {s['fg10']}; }}
button.pill:hover {{ background-color: alpha({s['dim']}, 0.26); color: {s['fg10']}; }}
button.pill:active {{ background-color: alpha({s['dim']}, 0.38); }}
button.pill .keycap, .tab .keycap {{ padding: 0 6px; }}
button.tile-btn {{
    background-color: {s['bg3']};
    border: 2px solid {s['bg3']};
    border-radius: 16px;
    padding: 6px 6px 10px 6px;
    font-weight: normal;
}}
button.tile-btn:hover {{ border-color: {s['dim']}; }}
button.tile-btn:active {{ background-color: alpha({s['mid']}, 0.14); }}
button.tile-btn.picked {{ border-color: {s['bg5']}; }}
button.tile-btn.off {{ opacity: 0.3; background-color: transparent; border-color: {s['bg3']}; }}
.tile-name {{ color: {s['fg9']}; font-size: 16px; font-weight: 600; }}
.tile-need {{ color: {s['fg8']}; font-size: 13px; }}
button.tile-btn:disabled {{ opacity: 0.55; background-color: transparent; border: 2px dashed {s['bg4']}; }}
.tab.gear {{ border: 1px solid {s['bg5']}; }}
.tab.gear.on {{ border-color: {s['mid']}; }}
.tab {{ background-color: transparent; color: {s['fg8']}; border-radius: 999px; padding: 8px 20px; font-size: 16px; }}
.tab:hover {{ background-color: alpha({s['mid']}, 0.08); color: {s['fg10']}; }}
.tab:active {{ background-color: alpha({s['mid']}, 0.22); }}
.tab.on {{ background-color: alpha({s['mid']}, 0.16); color: {s['bright']}; font-weight: 700; }}
button.choice.picked:active, .tab.on:active {{ background-color: alpha({s['mid']}, 0.26); }}
.legend {{ color: {s['fg8']}; font-size: 14px; }}
.muscle-name {{ color: {s['fg9']}; font-size: 14px; }}
.muscle-name.low {{ color: {s['fg6']}; }}
.mbar {{ background-color: {WORKED}; border-radius: 3px; min-height: 6px; }}
.sbar {{ background-color: {STRETCHED}; border-radius: 3px; min-height: 4px; }}
.mtrack {{ background-color: {s['bg4']}; border-radius: 3px; }}
.swatch.worked {{ background-color: {WORKED}; }}
.swatch.stretched {{ background-color: {STRETCHED}; }}
.faint {{ color: {s['fg6']}; }}
.rest-pill {{ background-color: alpha({WIN}, 0.12); color: {WIN}; border-radius: 999px; padding: 8px 18px; font-size: 16px; }}
"""


def label(text, cls, wrap=False, xalign=0.5, width=0):
    lab = Gtk.Label(label=text, xalign=xalign)
    for c in cls.split():
        lab.add_css_class(c)
    if wrap:
        lab.set_wrap(True)
        lab.set_justify(Gtk.Justification.CENTER if xalign == 0.5 else Gtk.Justification.LEFT)
        lab.set_max_width_chars(width or 52)
    return lab


def button(text, cls, cb):
    b = Gtk.Button(label=text)
    for c in cls.split():
        b.add_css_class(c)
    b.connect("clicked", lambda *_: cb())
    return clickable(b)


def vbox(spacing, *children, cls=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if cls:
        box.add_css_class(cls)
    for c in children:
        box.append(c)
    return box


def hbox(spacing, *children, center=True):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if center:
        box.set_halign(Gtk.Align.CENTER)
    for c in children:
        box.append(c)
    return box


def keycap(text):
    cap = label(text, "keycap")
    cap.set_valign(Gtk.Align.CENTER)
    return cap


def hint(*pairs):
    """Key hints: the key as a keycap, what it does beside it."""
    row = hbox(26)
    for key, what in pairs:
        row.append(hbox(8, keycap(key), label(what, "hint")))
    return row


def rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def pixel_icon(rows, colors, cell=4):
    area = Gtk.DrawingArea()
    area.set_content_width(len(rows[0]) * cell)
    area.set_content_height(len(rows) * cell)

    def draw(_area, cr, _w, _h):
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch in colors:
                    cr.set_source_rgb(*rgb(colors[ch]))
                    cr.rectangle(x * cell, y * cell, cell, cell)
                    cr.fill()

    area.set_draw_func(draw)
    return area


class PixelText(Gtk.DrawingArea):
    """Monocraft drawn without antialiasing, so every font pixel lands as a whole block of screen
    pixels. A Gtk.Label puts glyphs at fractional positions and smears them."""

    def __init__(self, text, size, color, spacing=0, align=Pango.Alignment.CENTER):
        super().__init__()
        assert size % 9 == 0, "Monocraft is only crisp at multiples of 9px"
        self.size, self.color, self.spacing, self.strike, self.align = size, color, spacing, None, align
        self.set_halign(Gtk.Align.CENTER)
        self.set_draw_func(self.draw)
        self.set_text(text)

    def setup(self, layout):
        opts = cairo.FontOptions()
        opts.set_antialias(cairo.ANTIALIAS_NONE)
        opts.set_hint_style(cairo.HINT_STYLE_NONE)
        opts.set_hint_metrics(cairo.HINT_METRICS_ON)
        PangoCairo.context_set_font_options(layout.get_context(), opts)
        layout.context_changed()
        desc = Pango.FontDescription.from_string(PIXEL)
        desc.set_absolute_size(self.size * Pango.SCALE)
        layout.set_font_description(desc)
        if self.spacing:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(self.spacing * Pango.SCALE))
            layout.set_attributes(attrs)
        layout.set_spacing(self.size // 3 * Pango.SCALE)
        layout.set_alignment(self.align)
        layout.set_text(self.text, -1)
        return layout

    def set_text(self, text, size=None):
        self.text, self.size = text, size or self.size
        w, h = self.setup(self.create_pango_layout(None)).get_pixel_size()
        self.set_content_width(w)
        self.set_content_height(h)
        self.queue_draw()

    def set_color(self, color, strike=None):
        self.color, self.strike = color, strike
        self.queue_draw()

    def draw(self, _area, cr, width, height):
        layout = self.setup(PangoCairo.create_layout(cr))
        w, h = layout.get_pixel_size()
        x, y = 0 if self.align == Pango.Alignment.LEFT else (width - w) // 2, (height - h) // 2
        cr.set_source_rgb(*rgb(self.color))
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)
        if self.strike:
            px = self.size // 9
            cr.set_source_rgb(*rgb(self.strike))
            cr.rectangle(0, y + (h // 2 // px) * px, width, px)
            cr.fill()


class FigureView(Gtk.DrawingArea):
    """A looping pixel figure doing a move. crop is (x, y, w, h) in screen pixels, for small tiles."""

    def __init__(self, move, accent, scale=3, crop=None):
        super().__init__()
        self.move, self.accent, self.scale = move, accent, scale
        self.crop = crop or (0, 0, figure.W * scale, figure.H * scale)
        self.set_content_width(self.crop[2])
        self.set_content_height(self.crop[3])
        self.start = time.monotonic() - random.random() * move.period
        self.set_draw_func(self.draw)
        # Headless screenshots hold every figure still at a share of its loop, so the stream settles.
        self.still = os.environ.get("PUSHUP_STILL")
        if not self.still:
            self.add_tick_callback(self.tick)
        self.drawn = 0.0

    def tick(self, _widget, clock):
        # Whole pixels at a time, so 40 frames a second looks the same as 144 and costs a quarter.
        now = clock.get_frame_time() / 1e6
        if now - self.drawn >= 1 / 40:
            self.drawn = now
            self.queue_draw()
        return True

    def set_move(self, move):
        self.move = move
        self.start = time.monotonic()
        self.queue_draw()

    def draw(self, _area, cr, width, height):
        cr.translate((width - self.crop[2]) // 2 - self.crop[0], (height - self.crop[3]) // 2 - self.crop[1])
        t = float(self.still) * self.move.period if self.still else time.monotonic() - self.start
        figure.draw(cr, self.move, t, self.accent, self.scale)


TILE_CROP = (24, 16, 144, 112)  # where the figure is, at scale 2: drops the empty edges
STAGE_CROP = (42, 0, 200, 192)  # the card's figure at scale 3, without the empty margins
SMALL_CROP = (34, 22, 124, 102)  # tighter, for the intro's grid of every move
GEAR_LABELS = {"bar": "Pull-up bar", "table": "Sturdy table", "chairs": "Two chairs", "parallettes": "Parallettes"}


def move_for(name):
    return figure.MOVES.get(name) or figure.STRETCHES.get(name) or figure.TRAINING.get(name) or figure.TRAINING["other"]


def title_case(name):
    """Sentence case for a name standing on its own: 'Pike push-ups'."""
    return name[:1].upper() + name[1:]


def muscles_of(name):
    """(work, stretch) for one move or activity, as body map input."""
    if name in catalog.ACTIVITIES:
        a = catalog.ACTIVITIES[name]
        return a["muscles"], a.get("stretch", {})
    x = catalog.info(name)
    return ({}, x.get("muscles", {})) if x.get("stretch") else (x.get("muscles", {}), {})


def tint_for(name):
    return STRETCHED if catalog.is_stretch(name) else WORKED


class MuscleView(Gtk.DrawingArea):
    """The body map, front and back."""

    def __init__(self, work=None, stretch=None, scale=2):
        super().__init__()
        self.work, self.stretch, self.scale = work or {}, stretch or {}, scale
        w, h = musclemap.size(scale)
        self.set_content_width(w)
        self.set_content_height(h)
        self.set_draw_func(lambda _a, cr, _w, _h: musclemap.draw(cr, self.work, self.stretch, self.scale))

    def show(self, work, stretch):
        self.work, self.stretch = work or {}, stretch or {}
        self.queue_draw()


class PainMap(Gtk.DrawingArea):
    """The body map you click to say where it hurts: every muscle, left and right on its own.
    Hovering names the spot, a click marks it red, another click lets it go."""

    def __init__(self, scale, changed):
        super().__init__()
        self.scale, self.changed = scale, changed
        self.picked, self.hot = [], None
        w, h = musclemap.size(scale)
        self.set_content_width(w)
        self.set_content_height(h)
        self.set_draw_func(self.draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self.hover(self.spot(x, y)))
        motion.connect("leave", lambda *_: self.hover(None))
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, _n, x, y: self.toggle(self.spot(x, y)))
        self.add_controller(click)

    def spot(self, x, y):
        return musclemap.hit(x / self.scale, y / self.scale)

    def hover(self, spot):
        if spot != self.hot:
            self.hot = spot
            self.set_cursor_from_name("pointer" if spot else None)
            self.queue_draw()
            self.changed()

    def toggle(self, spot):
        if spot:
            (self.picked.remove if spot in self.picked else self.picked.append)(spot)
            self.queue_draw()
            self.changed()

    def clear(self):
        self.picked, self.hot = [], None
        self.queue_draw()

    def draw(self, _area, cr, _w, _h):
        marks = {s: musclemap.PAIN for s in self.picked}
        if self.hot:
            marks[self.hot] = musclemap.PAIN_HOVER if self.hot in self.picked else musclemap.HOVER
        musclemap.draw(cr, {}, {}, self.scale, marks=marks)


def spot_name(spot):
    """'Left triceps'."""
    return f"{spot[1].capitalize()} {spot[0]}"


def clickable(b):
    """A hand over a button the way a link has one on the web, only while it can be clicked: a
    button still waiting out the arm delay keeps the arrow."""
    def sync(*_):
        b.set_cursor_from_name("pointer" if b.get_sensitive() else None)
    b.connect("notify::sensitive", sync)
    sync()
    return b


def keyed(text, key, cls, cb):
    """A button with its key shown as a keycap at the end."""
    b = clickable(Gtk.Button())
    b.set_child(hbox(10, label(text, ""), keycap(key)))
    for c in cls.split():
        b.add_css_class(c)
    b.connect("clicked", lambda *_: cb())
    return b


class RepSlider(Gtk.DrawingArea):
    """The rep slider, drawn by hand so the goal can be a notch cut into the track with a marker
    under it, clear of the thumb. Mouse drag and wheel move it; the card handles the keys."""

    PAD, TRACK_Y, TRACK_H, R = 26, 22, 12, 15

    def __init__(self, adj, goal, accent, interactive=True, labels=True, step=1):
        super().__init__()
        self.adj, self.goal, self.accent, self.labels, self.step = adj, goal, accent, labels, step
        # Reaching the goal turns the thumb green, unless the card is already the stretch teal.
        self.done_color = accent if accent == STRETCHED else WIN
        self.set_content_height(84 if labels else 58)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        adj.connect("value-changed", lambda *_: self.queue_draw())
        self.hover = self.dragging = False
        if interactive:
            self.set_cursor_from_name("grab")
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", lambda _g, x, _y: (self.grabbed(True), self.seek(x)))
            drag.connect("drag-update", lambda g, dx, _dy: self.seek(g.get_start_point()[1] + dx))
            drag.connect("drag-end", lambda *_: self.grabbed(False))
            self.add_controller(drag)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: self.hovered(True))
            motion.connect("leave", lambda *_: self.hovered(False))
            self.add_controller(motion)
            wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
            wheel.connect("scroll", self.on_wheel)
            self.add_controller(wheel)

    def hovered(self, on):
        self.hover = on
        self.queue_draw()

    def grabbed(self, on):
        self.dragging = on
        self.set_cursor_from_name("grabbing" if on else "grab")
        self.queue_draw()

    def on_wheel(self, _ctl, _dx, dy):
        self.adj.set_value(self.adj.get_value() + (-self.step if dy > 0 else self.step))
        return True

    def x_of(self, v, width):
        return self.PAD + (width - 2 * self.PAD) * v / self.adj.get_upper()

    def seek(self, x):
        width = self.get_width()
        v = (x - self.PAD) / max(1, width - 2 * self.PAD) * self.adj.get_upper()
        self.adj.set_value(max(0, min(self.adj.get_upper(), round(v / self.step) * self.step)))

    @staticmethod
    def pill(cr, x0, y, x1, h):
        r = h / 2
        if x1 - x0 < h:
            x1 = x0 + h
        cr.new_sub_path()
        cr.arc(x0 + r, y + r, r, math.pi / 2, 3 * math.pi / 2)
        cr.arc(x1 - r, y + r, r, -math.pi / 2, math.pi / 2)
        cr.close_path()

    def draw(self, _area, cr, width, _height):
        top, value = self.adj.get_upper(), round(self.adj.get_value())
        x0, x1, y, h = self.PAD, width - self.PAD, self.TRACK_Y, self.TRACK_H
        cr.set_source_rgb(*rgb(SLATE["bg4"]))
        self.pill(cr, x0, y, x1, h)
        cr.fill()
        if value > 0:
            cr.set_source_rgb(*rgb(self.accent))
            self.pill(cr, x0, y, self.x_of(value, width), h)
            cr.fill()
        made_it = False
        if self.goal is not None:
            # The goal: a notch through the track, and a marker under it.
            gx = round(self.x_of(self.goal, width))
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.rectangle(gx - 2, y - 1, 4, h + 2)
            cr.fill()
            cr.set_operator(cairo.OPERATOR_OVER)
            made_it = value >= self.goal
        if self.labels:
            desc = Pango.FontDescription.from_string(SANS)
            desc.set_absolute_size(14 * Pango.SCALE)
            every = 5 if top <= 40 else 10 if top <= 100 else 30
            for v in range(0, int(top) + 1, every):
                layout = PangoCairo.create_layout(cr)
                layout.set_font_description(desc)
                layout.set_text(str(v), -1)
                tw, _th = layout.get_pixel_size()
                cr.set_source_rgb(*rgb(SLATE["fg8"]))
                cr.move_to(round(self.x_of(v, width) - tw / 2), y + h + 26)
                PangoCairo.show_layout(cr, layout)
        tx, ty = self.x_of(value, width), y + h / 2
        # Pointed at, the thumb grows and gets a halo; held, the halo deepens.
        r = self.R + (2 if self.hover or self.dragging else 0)
        thumb = self.done_color if made_it else SLATE["fg10"]
        if self.hover or self.dragging:
            cr.set_source_rgba(*rgb(self.done_color if made_it else self.accent), 0.35 if self.dragging else 0.2)
            cr.arc(tx, ty, r + 8, 0, 2 * math.pi)
            cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.arc(tx, ty + 2, r + 1, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*rgb(thumb))
        cr.arc(tx, ty, r, 0, 2 * math.pi)
        cr.fill()
        if self.goal is not None:
            # The goal's marker under the notch goes on last, so a hovered thumb never hides it.
            cr.set_source_rgb(*rgb(self.done_color if made_it else self.accent))
            cr.move_to(gx, y + h + 12)
            cr.line_to(gx - 7, y + h + 20)
            cr.line_to(gx + 7, y + h + 20)
            cr.close_path()
            cr.fill()


def balance(text, max_chars):
    """Break a headline in two near the middle, at a sentence break when one fits."""
    if len(text) <= max_chars:
        return text
    words = text.split(" ")
    splits = []
    for i in range(1, len(words)):
        left, right = " ".join(words[:i]), " ".join(words[i:])
        if len(left) <= max_chars and len(right) <= max_chars:
            splits.append((left, right))
    if not splits:
        return text
    sentence = [s for s in splits if s[0][-1] in ".?!,"]
    left, right = min(sentence or splits, key=lambda s: abs(len(s[0]) - len(s[1])))
    return f"{left}\n{right}"


def headline(text, color=SLATE["fg10"]):
    """27px pixel text, two balanced lines at most; a longer line drops to 18px."""
    pt = PixelText("", 27, color)
    set_headline(pt, text)
    return pt


def set_headline(pt, text):
    small = len(text) > 66
    pt.set_text(balance(text, 50 if small else 33), 18 if small else 27)


def ago(ts, now=None):
    if not ts:
        return "never"
    secs = max(0, (now or time.time()) - ts)
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h {int(secs % 3600 // 60)}m ago"
    days = int(secs // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


def streak_days(values):
    """Indexes of the days in the current streak: days in a row with reps, counting back from today,
    or from yesterday when today is still empty."""
    end = len(values) - 1
    if end >= 0 and not values[end]:
        end -= 1
    out = []
    while end >= 0 and values[end]:
        out.append(end)
        end -= 1
    return out


PUSH = catalog.PUSH_UPS


def text_at(cr, text, x, y, color, size=14, align=0.5, bold=False):
    """Sans text on a chart, x anchored at its share align of the text's width, y its top."""
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string(f"{SANS} {'Bold' if bold else ''}")
    desc.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    w, h = layout.get_pixel_size()
    cr.set_source_rgb(*rgb(color))
    cr.move_to(round(x - w * align), round(y))
    PangoCairo.show_layout(cr, layout)
    return w


def dashed(cr, x0, y0, x1, y1, color, alpha=1.0, width=2, dash=6):
    cr.set_source_rgba(*rgb(color), alpha)
    cr.set_line_width(width)
    cr.set_dash([dash, dash])
    cr.move_to(x0, y0)
    cr.line_to(x1, y1)
    cr.stroke()
    cr.set_dash([])


def cross(cr, x, y, size=9, color=RED):
    """A pixel X, for a skip or a missed day, centred on x, top at y."""
    px = max(1, size // 5)
    cr.set_source_rgb(*rgb(color))
    for i in range(0, size, px):
        cr.rectangle(round(x - size / 2 + i), y + i, px, px)
        cr.rectangle(round(x + size / 2 - i - px), y + i, px, px)
    cr.fill()


def hour_floor(ts):
    return dt.datetime.fromtimestamp(ts).replace(minute=0, second=0, microsecond=0).timestamp()


class DayChart(Gtk.DrawingArea):
    """Today, set by set, on a clock. With a target, the push-ups climb as a staircase toward it and
    a dashed line shows the pace that finishes an hour before the deadline. Without one, every set
    is a column as tall as its share of the goal. Skips sit on the floor as red crosses."""

    LEFT, RIGHT, TOP, BOTTOM = 10, 78, 24, 28

    def __init__(self, events, accent, width, height, target=None, deadline=None, mark_last=False, now=None):
        super().__init__()
        self.events = sorted(events, key=lambda e: e["t"])
        self.accent, self.target, self.deadline, self.mark_last = accent, target, deadline, mark_last
        self.now = now or time.time()
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self.draw)

    def span(self):
        times = [e["t"] for e in self.events] or [self.now]
        t0 = hour_floor(min(times))
        t1 = max(times[-1], self.now) + 2700
        if self.target and self.deadline and self.deadline - t0 < 16 * 3600:
            t1 = max(t1, self.deadline)
        return t0, max(t1, t0 + 4 * 3600)

    def draw(self, _area, cr, width, height):
        t0, t1 = self.span()
        x0, x1, y0, y1 = self.LEFT, width - self.RIGHT, self.TOP, height - self.BOTTOM
        X = lambda t: x0 + (x1 - x0) * (t - t0) / (t1 - t0)  # noqa: E731
        bw = 12 if (x1 - x0) > 500 else 10
        # The clock along the floor: every second hour, and the deadline when it's on the chart.
        cr.set_source_rgb(*rgb(SLATE["bg5"]))
        cr.rectangle(x0, y1, x1 - x0, 2)
        cr.fill()
        h = dt.datetime.fromtimestamp(t0)
        tick = h.timestamp() + (h.hour % 2) * 3600
        while tick <= t1:
            x = X(tick)
            text_at(cr, dt.datetime.fromtimestamp(tick).strftime("%H:%M"), x, y1 + 8, SLATE["fg7"],
                    align=0 if x - x0 < 24 else 1 if x1 - x < 24 else 0.5)
            tick += 7200
        if t0 <= self.now <= t1 and not self.mark_last:
            dashed(cr, X(self.now), y0 - 4, X(self.now), y1, SLATE["fg7"], 0.7, width=1, dash=3)
            text_at(cr, "now", X(self.now) + 6, y0 - 4, SLATE["fg8"], align=0)
        if self.target:
            self.draw_climb(cr, X, x0, x1, y0, y1, bw)
        else:
            self.draw_sets(cr, X, x0, x1, y0, y1, bw)
        for e in self.events:
            if e["result"] == "skipped":
                cross(cr, X(e["t"]) - 6, y1 - 14, 12)

    def draw_climb(self, cr, X, x0, x1, y0, y1, bw):
        pushes = [e for e in self.events if e["exercise"] in PUSH and e["did"] > 0]
        total = sum(e["did"] for e in pushes)
        top = max(self.target * 1.15, total * 1.08, 10)
        Y = lambda v: y1 - (y1 - y0) * v / top  # noqa: E731
        # The pace that gets the target in an hour before the deadline, from the first set.
        if self.deadline and pushes:
            start, end = pushes[0]["t"], self.deadline - 3600
            if end > start:
                dashed(cr, X(start), y1, X(end), Y(self.target), SLATE["fg6"], 0.9, width=2, dash=4)
        gy = round(Y(self.target))
        dashed(cr, x0, gy, x1, gy, WIN, 0.55, width=2)
        text_at(cr, f"target {self.target}", x1 + 10, gy - 9, WIN, bold=True, align=0)
        # The staircase: what was done so far, solid under the steps.
        cum, last_x = 0, None
        cr.set_source_rgba(*rgb(SLATE["bg5"]), 0.7)
        cr.move_to(X(pushes[0]["t"]) if pushes else x0, y1)
        for e in pushes:
            cr.line_to(X(e["t"]), Y(cum))
            cum += e["did"]
            cr.line_to(X(e["t"]), Y(cum))
        if pushes:
            end = X(min(max(self.now, pushes[-1]["t"]), self.span()[1]))
            cr.line_to(end, Y(cum))
            cr.line_to(end, y1)
        cr.close_path()
        cr.fill()
        cum, prev = 0, None
        for i, e in enumerate(pushes):
            before, cum = cum, cum + e["did"]
            x = X(e["t"]) if prev is None else max(X(e["t"]), prev + bw + 3)
            prev = x
            last = self.mark_last and i == len(pushes) - 1
            color = WIN if cum >= self.target else self.accent
            cr.set_source_rgb(*rgb(SLATE["fg10"] if last else color))
            cr.rectangle(round(x - bw), round(Y(cum)), bw, max(2, round(Y(before) - Y(cum))))
            cr.fill()
            if last_x is None or x - last_x >= 22 or i == len(pushes) - 1:
                text_at(cr, str(e["did"]), x - bw / 2, Y(cum) - 20, SLATE["fg10"] if last else SLATE["fg9"],
                        bold=last)
                last_x = x
        # Everything else done today, small on the floor in its own colour.
        for e in self.events:
            if e["did"] > 0 and e["exercise"] not in PUSH:
                cr.set_source_rgb(*rgb(tint_for(e["exercise"])))
                cr.rectangle(round(X(e["t"]) - 5), y1 - 10, 8, 8)
                cr.fill()

    def draw_sets(self, cr, X, x0, x1, y0, y1, bw):
        top = 1.5
        Y = lambda r: y1 - (y1 - y0) * min(r, top) / top  # noqa: E731
        dashed(cr, x0, round(Y(1)), x1, round(Y(1)), WIN, 0.55, width=2)
        text_at(cr, "goal", x1 + 10, Y(1) - 9, WIN, bold=True, align=0)
        last_x = prev = None
        sets = [e for e in self.events if e["did"] > 0]
        for i, e in enumerate(sets):
            ratio = e["did"] / e["goal"] if e.get("goal") else 1
            stretch = catalog.is_stretch(e["exercise"])
            color = STRETCHED if stretch else SLATE["fg7"] if e["result"] == "manual" else WIN if ratio >= 1 else self.accent
            last = self.mark_last and i == len(sets) - 1
            # Sets logged in one go share a moment: they stand side by side, not on top of each other.
            x = X(e["t"]) if prev is None else max(X(e["t"]), prev + bw + 3)
            prev = x
            cr.set_source_rgb(*rgb(SLATE["fg10"] if last else color))
            cr.rectangle(round(x - bw), round(Y(ratio)), bw, round(y1 - Y(ratio)))
            cr.fill()
            if last_x is None or x - last_x >= 22 or i == len(sets) - 1:
                text_at(cr, str(e["did"]), x - bw / 2, Y(ratio) - 20, SLATE["fg10"] if last else SLATE["fg9"])
                last_x = x


class MonthChart(Gtk.DrawingArea):
    """Days as columns, oldest first. With targets, each day's target is a dim bar with what got done
    over it, green once the target fell, and a past day with nothing in it gets a red cross. Without,
    each day is its sets, with that day's skips stacked on top in hollow red. Today is outlined."""

    TOP, BOTTOM = 22, 24

    def __init__(self, cols, accent, width, height):
        super().__init__()
        self.cols, self.accent = cols, accent
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, width, height):
        cols = self.cols
        n = len(cols)
        gap = 4 if width / max(1, n) > 16 else 2
        w = (width - gap * (n - 1)) / n
        y1 = height - self.BOTTOM
        h = y1 - self.TOP
        top = max([1, *(max(c.get("target") or 0, c["value"] + c.get("skips", 0)) for c in cols)])
        for i, c in enumerate(cols):
            x = round(i * (w + gap))
            cw = round((i + 1) * (w + gap) - gap) - x
            v, target, when = c["value"], c.get("target"), c["when"]
            vh = round(h * v / top)
            if target is not None:
                th = max(3, round(h * target / top))
                cr.set_source_rgb(*rgb(SLATE["bg4"] if when == "future" else SLATE["bg5"]))
                cr.rectangle(x, y1 - th, cw, th)
                cr.fill()
                if c.get("trained") and v < target:
                    # A training day: the day's work was done elsewhere, so the target counts as kept.
                    cr.set_source_rgba(*rgb(WIN), 0.45)
                    cr.rectangle(x, y1 - th, cw, th)
                    cr.fill()
                if v:
                    cr.set_source_rgb(*rgb(WIN if v >= target else self.accent))
                    cr.rectangle(x, y1 - vh, cw, vh)
                    cr.fill()
                elif when == "past" and not c.get("trained"):
                    cr.set_source_rgba(*rgb(RED), 0.8)
                    cr.set_line_width(2)
                    cr.rectangle(x + 1, y1 - th + 1, cw - 2, th - 2)
                    cr.stroke()
                    cross(cr, x + cw / 2, y1 - 16, 10)
                outline = th
            else:
                if v:
                    cr.set_source_rgb(*rgb(self.accent if when == "today" else SLATE["dim"]))
                    cr.rectangle(x, y1 - vh, cw, vh)
                    cr.fill()
                else:
                    cr.set_source_rgb(*rgb(SLATE["bg4"]))
                    cr.rectangle(x, y1 - 3, cw, 3)
                    cr.fill()
                if c.get("skips"):
                    sh = max(4, round(h * c["skips"] / top))
                    cr.set_source_rgba(*rgb(RED), 0.85)
                    cr.set_line_width(2)
                    cr.rectangle(x + 1, y1 - vh - sh + 1, cw - 2, sh - 2)
                    cr.stroke()
                outline = max(vh, 3)
            if when == "today":
                cr.set_source_rgb(*rgb(SLATE["fg10"]))
                cr.set_line_width(2)
                cr.rectangle(x - 1, y1 - outline - 1, cw + 2, outline + 1)
                cr.stroke()
            if v and cw >= 16:
                done = target is not None and v >= target
                sh = max(4, round(h * c["skips"] / top)) if c.get("skips") and target is None else 0
                text_at(cr, str(v), x + cw / 2, y1 - max(vh + sh, outline if target is not None else 0) - 20,
                        WIN if done else SLATE["fg10"] if when == "today" else SLATE["fg8"], bold=when == "today")
            if c.get("label"):
                # The end labels stay inside the chart: "today" on a narrow last column would hang off it.
                edge = 1 if i == n - 1 and cw < 40 else 0 if i == 0 and cw < 40 else 0.5
                text_at(cr, c["label"], x + cw * edge, y1 + 6, SLATE["fg10"] if when == "today" else SLATE["fg7"],
                        bold=when == "today", align=edge)


def challenge_cols(c):
    """The challenge's days as MonthChart columns: the target, what got done, past, today or to come."""
    ramp = c.get("ramp") or [c["target"]] * c["days"]
    done = {h["day"]: h for h in c.get("history", [])}
    n = max(len(ramp), c["day"])
    out = []
    for i in range(n):
        day = i + 1
        when = "past" if day < c["day"] else "today" if day == c["day"] else "future"
        lab = str(day) if day in (1, n) or day == c["day"] or day % 5 == 0 else ""
        out.append({"value": done.get(day, {}).get("done", 0), "target": done.get(day, {}).get("target", ramp[min(i, len(ramp) - 1)]),
                    "when": when, "label": lab, "trained": bool(done.get(day, {}).get("trained"))})
    return out


def challenge_strip(c, accent, width=820, height=150):
    """The month with its scale: the top target over it, the day numbers under it."""
    top = max([*(c.get("ramp") or [c["target"]]), *(h["done"] for h in c.get("history", [])), 1])
    return vbox(6, label(f"{top} push-ups", "legend", xalign=0), MonthChart(challenge_cols(c), accent, width, height))


class Spark(Gtk.DrawingArea):
    """Fourteen days of one move's best set, as little pixel columns, today brightest."""

    def __init__(self, values, color, width=196, height=34):
        super().__init__()
        self.values, self.color = values, color
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, width, height):
        n = len(self.values)
        gap = 3
        w = (width - gap * (n - 1)) / n
        top = max([1, *self.values])
        for i, v in enumerate(self.values):
            x = round(i * (w + gap))
            cw = round((i + 1) * (w + gap) - gap) - x
            if v:
                vh = max(3, round((height - 2) * v / top))
                cr.set_source_rgb(*rgb(self.color if i == n - 1 else SLATE["fg7"]))
                cr.rectangle(x, height - vh, cw, vh)
            else:
                cr.set_source_rgb(*rgb(SLATE["bg4"]))
                cr.rectangle(x, height - 2, cw, 2)
            cr.fill()


SHORT = {"bulgarian split squats": "Bulgarians"}
GROUP_ROWS = (("push", "Push", "chest, shoulders, triceps"), ("pull", "Pull", "back, biceps, grip"),
              ("legs", "Legs", "thighs, glutes, calves"), ("core", "Core", "abs, obliques, lower back"))


def mix(a, b, t):
    """Colour a, t of the way to colour b."""
    return tuple(x + (y - x) * t for x, y in zip(rgb(a), rgb(b)))


class TimelineChart(Gtk.DrawingArea):
    """Four weeks by muscle group: a column a day, a row each for push, pull, legs and core, warmer
    for more work, a ring where logged training hit the group, today at the right. Each row ends in
    its sets and the moves that gave them."""

    LEFT, RIGHT, ROW, GAP, FOOT = 190, 210, 46, 8, 30

    def __init__(self, t, width=1100):
        super().__init__()
        self.t = t
        self.set_content_width(width)
        self.set_content_height(len(GROUP_ROWS) * (self.ROW + self.GAP) + self.FOOT)
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, width, _height):
        t, n = self.t, self.t["days"]
        x0, x1 = self.LEFT, width - self.RIGHT
        gap = 4
        cw = (x1 - x0 - gap * (n - 1)) / n
        top = max(30.0, *(v for g, _, _ in GROUP_ROWS for v in t["work"][g]))
        first = dt.date.fromisoformat(t["first"])
        rings = {(s["day"], g) for s in t["sessions"] for g in s["groups"]}
        for r, (g, name, sub) in enumerate(GROUP_ROWS):
            y = r * (self.ROW + self.GAP)
            total = sum(t["sets"][g])
            text_at(cr, name, 0, y + 3, SLATE["fg10"], 18, align=0, bold=True)
            text_at(cr, sub, 0, y + 26, SLATE["fg7"], 14, align=0)
            for i in range(n):
                x = round(x0 + i * (cw + gap))
                w = round(x0 + (i + 1) * (cw + gap) - gap) - x
                v = t["work"][g][i]
                if v > 0:
                    cr.set_source_rgb(*mix(SLATE["bg5"], WORKED, 0.3 + 0.7 * min(1.0, (v / top) ** 0.5)))
                else:
                    cr.set_source_rgb(*rgb(SLATE["bg4"]))
                cr.rectangle(x, y + 2, w, self.ROW - 4)
                cr.fill()
                if (i, g) in rings:
                    cr.set_source_rgb(*rgb(SLATE["fg10"]))
                    cr.set_line_width(2)
                    cr.rectangle(x + 1, y + 3, w - 2, self.ROW - 6)
                    cr.stroke()
            move = SHORT.get(t["moves"][g], t["moves"][g])
            text_at(cr, catalog.plural(total, "set"), x1 + 22, y + 3, SLATE["fg10"] if total else RED, 18, align=0, bold=True)
            text_at(cr, f"mostly {move}" if move else "nothing yet", x1 + 22, y + 26, SLATE["fg7"], 14, align=0)
        y = len(GROUP_ROWS) * (self.ROW + self.GAP) + 4
        for i in range(n):
            if i == n - 1 or (n - 1 - i) % 7 == 0:
                d = first + dt.timedelta(days=i)
                text = "today" if i == n - 1 else d.strftime("%-d %b")
                x = x0 + i * (cw + gap) + cw / 2
                text_at(cr, text, x, y, SLATE["fg10"] if i == n - 1 else SLATE["fg7"], 14, align=0.5 if i < n - 1 else 0.75)


def focused_connector():
    try:
        mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3).stdout)
        return next((m["name"] for m in mons if m.get("focused")), None)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class Card:
    def __init__(self, args):
        self.args = args
        self.result = ""
        self.armed = []
        self.ready = False
        self.closing = False
        self.level = None
        self.spice = args.spice if args.spice in INSULTS else "spicy"
        self.reminder_mode = not (args.intro or args.graph or args.add or args.challenge_intro)
        self.challenge = json.loads(args.challenge) if getattr(args, "challenge", "") else None
        self.offer = json.loads(args.options) if self.reminder_mode and args.options else []
        self.at = 0
        self.calm = bool(args.calm)
        # The day's push-ups are in: the card is a bonus, passing it is free and quick.
        self.bonus = bool(self.reminder_mode and self.challenge and self.challenge.get("left", 1) <= 0)
        self.stretching = bool(self.offer and self.offer[0].get("stretch"))
        # Its first move leans on a part you said hurts: the card asks for less and never holds a pass against you.
        self.easy = (self.offer[0].get("easy") or "") if self.offer and not (self.calm or self.bonus) else ""
        self.timer = None
        self.timer_state = None
        self.tier = min(args.skips, len(RAGE) - 1) if self.reminder_mode and not (args.calm or self.bonus or self.easy) else 0
        # Stretch cards wear the stretch colour everywhere, so the eyebrow isn't fighting a blue button.
        self.accent = STRETCHED if self.stretching else RAGE[self.tier]
        self.history = [int(x) for x in args.history.split(",") if x.strip().isdigit()] if args.history else []
        self.done_today = [tuple(d) for d in json.loads(getattr(args, "done", "") or "[]")]
        self.stats = json.loads(args.stats) if getattr(args, "stats", "") else {}
        self.momentum = self.stats.get("momentum", 0)
        self.holding = None
        self.pending = None
        self.with_receipts = None
        self.key_released = False
        self.below = None
        self.app = Gtk.Application(application_id=None, flags=0)
        self.app.connect("activate", self.build)

    def run(self):
        self.app.run([])
        return self.result

    def finish(self, result):
        self.result = result
        self.app.quit()

    def build(self, app):
        provider = Gtk.CssProvider()
        provider.load_from_string(css(self.accent, self.tier))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        win = Gtk.Window(application=app, title="pushup")
        win.add_css_class("pushup")
        self.place(win)

        self.backdrop = Gtk.Box()
        self.backdrop.add_css_class("backdrop")
        self.backdrop.set_hexpand(True)
        self.backdrop.set_vexpand(True)
        if self.args.intro:
            card = self.intro()
        elif self.args.challenge_intro:
            card = self.challenge_intro()
        elif self.args.graph:
            card = self.graph()
        elif self.args.add:
            card = self.add_card()
        else:
            card = self.reminder()
        self.card = card
        card.add_css_class("card")
        if self.tier >= 4:
            card.add_css_class("shake")
        if self.args.opaque:
            # For recordings: hide whatever is on screen behind the card.
            prov = Gtk.CssProvider()
            prov.load_from_string(".backdrop { background-color: #08090b; }")
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        column = vbox(18, card)
        if self.reminder_mode:
            # Same space with or without the pill, so the card never moves.
            if not self.below:
                self.below = label("nothing paused", "chip below")
                self.below.set_opacity(0)
            column.append(self.below)
        column.set_halign(Gtk.Align.CENTER)
        column.set_valign(Gtk.Align.CENTER)
        column.set_hexpand(True)
        self.backdrop.append(column)
        win.set_child(self.backdrop)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        keys.connect("key-released", self.on_key_up)
        win.add_controller(keys)

        for b in self.armed:
            b.set_sensitive(False)
        GLib.timeout_add(int(self.args.arm_delay * 1000), self.arm)
        if self.args.close_after:
            GLib.timeout_add(int(self.args.close_after * 1000), lambda: self.finish("timeout"))
        if self.args.react:
            self.ready = True
            if self.args.react.startswith("done "):
                self.adj.set_value(int(self.args.react.split()[1]))
            self.react(self.args.react, close=False)
        elif self.args.holding:
            self.show_hold(self.args.holding)
        if self.args.add and self.args.pick:
            kind = "home" if self.args.pick in catalog.EXERCISES else "stretch" if catalog.is_stretch(self.args.pick) else "training"
            self.add_pick(kind, self.args.pick)
        if self.reminder_mode and self.stretching and (self.args.start_timer or self.args.timer_at >= 0):
            self.ready = True
            self.timer_start(max(0.0, self.args.timer_at))
        if self.reminder_mode and self.args.swap:
            for _ in range(self.args.swap):
                self.swap()
        if self.args.value is not None and self.reminder_mode:
            self.adj.set_value(self.args.value)
        win.present()

    def place(self, win):
        if LayerShell and "Wayland" in type(Gdk.Display.get_default()).__name__ and LayerShell.is_supported():
            LayerShell.init_for_window(win)
            LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
            LayerShell.set_namespace(win, "pushup")
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(win, edge, True)
            LayerShell.set_exclusive_zone(win, -1)
            mode = LayerShell.KeyboardMode.NONE if self.args.no_grab else LayerShell.KeyboardMode.EXCLUSIVE
            LayerShell.set_keyboard_mode(win, mode)
            name = focused_connector()
            monitors = Gdk.Display.get_default().get_monitors()
            for i in range(monitors.get_n_items()):
                mon = monitors.get_item(i)
                if mon.get_connector() == name:
                    LayerShell.set_monitor(win, mon)
        else:
            win.set_decorated(False)
            if "Broadway" in type(Gdk.Display.get_default()).__name__:
                # Headless screenshots: the size the browser grabbing it will have.
                w, h = (int(v) for v in os.environ.get("SHOT_SIZE", "1600,1000").split(","))
                win.set_default_size(w, h)
            else:
                win.set_default_size(1600, 1000)
                win.maximize()

    def arm(self):
        for b in self.armed:
            b.set_sensitive(True)
        self.ready = True
        return False

    def on_key(self, _ctl, keyval, _code, _state):
        name = Gdk.keyval_name(keyval) or ""
        if self.closing:
            if self.pending and self.key_released and name in ("Return", "KP_Enter", "space", "Escape"):
                self.finish(self.pending)
            elif self.with_receipts and self.key_released and name in ("r", "R"):
                self.open_receipts()
            return True
        if not self.ready:
            return True
        if self.args.challenge_intro:
            if name in ("Return", "KP_Enter", "space"):
                self.finish("start")
            elif name == "Escape":
                self.finish("closed")
            return True
        if self.args.intro:
            page = self.stack.get_visible_child_name()
            if name in ("Return", "KP_Enter", "space"):
                self.intro_next()
            elif name == "Escape":
                self.finish(self.intro_result())
            elif name in ("1", "2", "3") and page == "3":
                self.pick(("easy", "normal", "hard")[int(name) - 1])
            elif name in ("1", "2") and page == "4":
                self.pick_spice(("mild", "spicy")[int(name) - 1])
            return True
        if self.args.graph:
            count = len(self.receipt_tabs)
            if name.isdigit() and 1 <= int(name) <= count:
                self.receipt_tab(int(name))
            elif name in ("Tab", "ISO_Left_Tab"):
                at = int(self.receipt_stack.get_visible_child_name())
                self.receipt_tab((at - 1 + (-1 if name == "ISO_Left_Tab" else 1)) % count + 1)
            elif name in ("Return", "KP_Enter"):
                self.finish("now")
            elif name in ("l", "L"):
                self.finish("add")
            elif name in ("Escape", "q"):
                self.finish("closed")
            return True
        if self.args.add:
            return self.add_key(name)
        if self.where_box.get_visible():
            if name in ("Return", "KP_Enter"):
                self.hurt_in()
            elif name in ("Escape", "BackSpace"):
                self.where_back()
            return True
        step = {"Left": -1, "Right": 1, "Down": -5, "Up": 5, "Page_Down": -5, "Page_Up": 5}.get(name)
        if name in ("Tab", "ISO_Left_Tab"):
            self.swap(-1 if name == "ISO_Left_Tab" else 1)
        elif name in ("t", "T") and not self.calm:
            self.finish(json.dumps({"result": "trained"}))
        elif name in ("h", "H"):
            self.say_hurt()
        elif name in ("s", "S") and self.stretching:
            self.stretch_main()
        elif step:
            self.adj.set_value(self.adj.get_value() + step)
        elif name == "Home":
            self.adj.set_value(0)
        elif name == "End":
            self.adj.set_value(self.adj.get_upper())
        elif name in ("Return", "KP_Enter", "space") and self.stretching:
            self.stretch_main()
        elif name in ("Return", "KP_Enter", "space"):
            self.log_set()
        elif name == "Escape":
            self.hold_start()
        return True

    def on_key_up(self, _ctl, keyval, _code, _state):
        if self.closing:
            self.key_released = True
        if (Gdk.keyval_name(keyval) or "") == "Escape":
            self.hold_stop()

    # The reminder: the number, the slider, log. Nothing else while you should be on the floor.

    def reminder(self):
        a = self.args
        first = self.offer[0]
        n = first["goal"]
        self.goal = n
        self.exercise = first["exercise"]
        stale = self.tier == 0 and a.last and time.time() - a.last > 86400
        self.said = None  # a line that names the move, said again for the move Tab lands on
        if self.calm:
            text, brow, brow_color = random.choice(REST_LINES), "REST DAY", STRETCHED
        elif self.stretching:
            text, brow, brow_color = random.choice(STRETCH_LINES[self.spice]), "STRETCH", STRETCHED
        elif self.bonus:
            self.said = (random.choice(BONUS_LINES[self.spice]), {})
            brow, brow_color = f"DAY {self.challenge['day']} OF {self.challenge['days']}: BONUS SET", WIN
        else:
            m = self.momentum
            rolling = self.tier == 0 and m >= 3 and not stale
            pool = STALE[self.spice] if stale else ON_A_ROLL[self.spice] if rolling else INSULTS[self.spice][self.tier]
            self.said = (random.choice(pool), {"skips": a.skips, "ago": ago(a.last), "m": m, "m1": m + 1})
            brow, brow_color = EYEBROWS[self.tier].format(skips=a.skips), EYEBROW_COLOR[self.tier]
            if self.challenge and self.tier == 0:
                brow = f"DAY {self.challenge['day']} OF {self.challenge['days']}"
            if self.easy and not rolling:
                self.said = (random.choice(HURT_LINES[self.spice]), {"part": catalog.PART_NAMES[self.easy]})
                brow = f"{brow}: GO EASY" if self.challenge else "GO EASY"
                brow_color = STRETCHED
            if rolling:
                # A run of done cards gets said out loud, in the colour of a goal hit.
                brow = f"{brow}: {m} IN A ROW" if self.challenge else f"{m} IN A ROW"
                brow_color = WIN
        if self.said:
            text = self.said[0].format(**self.said[1], **self.words(n))

        self.eyebrow = PixelText(brow, 18, brow_color, spacing=4)
        # The headline always gets room for two lines, so every card is the same height.
        self.line = headline(text)
        self.line.set_valign(Gtk.Align.CENTER)
        self.line.set_hexpand(True)
        line_slot = Gtk.Box()
        line_slot.set_size_request(-1, 27 * 2 + 9)
        line_slot.append(self.line)
        line_slot.set_margin_top(16)

        # The move and the muscles it hits on the left, how many on the right.
        self.figure = FigureView(move_for(self.exercise), tint_for(self.exercise), crop=STAGE_CROP)
        self.muscles = MuscleView(*muscles_of(self.exercise), scale=3)
        self.muscles.set_valign(Gtk.Align.CENTER)
        self.muscles.set_margin_end(10)
        self.figure.set_valign(Gtk.Align.END)
        stage = hbox(0, self.figure, self.muscles)
        stage.add_css_class("stage")
        stage.set_valign(Gtk.Align.CENTER)
        self.hero = PixelText(str(n), 144, SLATE["fg10"])
        self.unit = PixelText("", 27, SLATE["fg9"])
        self.unit.set_margin_top(10)
        # One leg's number, said out loud: EACH LEG, and what that makes in total.
        self.side = PixelText(" ", 18, self.accent, spacing=2)
        self.side.set_margin_top(10)
        # The challenge: where today stands, moving with the slider.
        self.progress = label(" ", "meta", wrap=True, width=38)
        self.progress.set_justify(Gtk.Justification.CENTER)
        self.progress.set_margin_top(14)
        count = vbox(0, self.hero, self.unit, self.side, self.progress)
        count.set_valign(Gtk.Align.CENTER)
        count.set_hexpand(True)
        count.set_size_request(300, -1)
        self.stage_row = stage_row = hbox(24, stage, count)
        stage_row.set_margin_top(18)

        self.swap_btn = keyed("Another stretch" if self.stretching else "Another exercise", "Tab", "pill",
                              lambda: self.swap())
        trained = keyed("I trained today", "T", "pill", lambda: self.finish(json.dumps({"result": "trained"})))
        self.armed += [self.swap_btn, trained]
        pills = hbox(12, self.swap_btn, *([] if self.calm else [trained]))
        pills.set_margin_top(14)
        self.swap_btn.set_visible(len(self.offer) > 1)
        card = vbox(0, self.eyebrow, line_slot, stage_row, pills)
        card.set_size_request(900, -1)
        # The pill sits under the card, on the dimmed screen, so it never changes the card: why the
        # goal is low tonight, and what got paused.
        self.below = label("", "chip below", wrap=True, width=64)
        self.below.set_halign(Gtk.Align.CENTER)

        # One scale per level, so where the goal sits on the track means something.
        top = max(first["top"], n)
        self.adj = Gtk.Adjustment(value=n, lower=0, upper=top, step_increment=1, page_increment=5)
        self.slider = RepSlider(self.adj, n, self.accent)
        self.slider.set_margin_top(26)

        # On a stretch the main thing is the timer; it logs the hold when it runs out.
        self.log_btn = button("Start the timer" if self.stretching else "Log it", "primary huge",
                              self.stretch_main if self.stretching else self.log_set)
        self.log_btn.set_halign(Gtk.Align.CENTER)
        self.log_btn.set_margin_top(28)
        self.armed.append(self.log_btn)

        # Skipping is a hold, not a click, and the fill shows how far along it is.
        self.hold_fill = Gtk.Box()
        self.hold_fill.add_css_class("hold-fill")
        self.hold_fill.set_halign(Gtk.Align.START)
        self.hold_fill.set_size_request(0, -1)
        self.skip_word = "Hold to pass" if self.calm or self.bonus or self.easy else SKIP_HOLD[self.tier]
        self.hold_label = label(self.skip_word, "hold-label")
        frame = Gtk.Box()
        frame.add_css_class("hold")
        frame.set_size_request(HOLD_W, 58)
        self.hold_frame = frame
        self.hold_w = HOLD_W
        hold = Gtk.Overlay()
        hold.set_child(frame)
        hold.add_overlay(self.hold_fill)
        hold.add_overlay(self.hold_label)
        hold.set_halign(Gtk.Align.CENTER)
        press = Gtk.GestureDrag()
        press.connect("drag-begin", lambda *_: self.hold_start())
        press.connect("drag-end", lambda *_: self.hold_stop())
        press.connect("cancel", lambda *_: self.hold_stop())
        hold.add_controller(press)
        hold.set_cursor_from_name("pointer")
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: frame.add_css_class("hot"))
        motion.connect("leave", lambda *_: frame.remove_css_class("hot"))
        hold.add_controller(motion)
        # Beside the skip: it hurts. It asks where, then the card comes back eased.
        self.hurt_btn = keyed("It hurts", "H", "side", self.say_hurt)
        self.hurt_btn.set_size_request(HURT_W, 58)
        self.armed.append(self.hurt_btn)
        hold.set_halign(Gtk.Align.FILL)
        under = hbox(12, self.hurt_btn, hold)
        under.set_margin_top(12)

        keys = hint(("← →", "hold length" if self.stretching else "adjust"),
                    ("Enter", "start" if self.stretching else "log"), ("Esc", self.skip_word.lower()))
        keys.set_margin_top(24)
        act = vbox(0, self.slider, self.log_btn, under, keys)

        self.after = vbox(0)
        self.after.set_valign(Gtk.Align.CENTER)
        self.foot = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=160)
        self.foot.set_vhomogeneous(True)
        self.foot.add_named(act, "act")
        self.foot.add_named(self.after, "after")
        # The body map takes the place of the move, the slider and the buttons while you pick.
        self.where_box = self.where_page()
        self.where_box.set_visible(False)
        card.append(self.foot)
        card.append(self.where_box)
        self.adj.connect("value-changed", self.on_slide)
        self.show_offer()
        return card

    def show_offer(self):
        """Point the card at the current exercise: goal, unit, figure, slider and the why pill."""
        self.timer_stop()
        o = self.offer[self.at]
        self.exercise, self.goal = o["exercise"], o["goal"]
        self.figure.set_move(move_for(self.exercise))
        self.muscles.show(*muscles_of(self.exercise))
        if self.stretching:
            self.eyebrow.set_text(("REST DAY: " if self.calm else "STRETCH: ")
                                  + catalog.GOALS[catalog.STRETCHES[self.exercise]["goal"]].upper())
        self.slider.goal = self.goal
        self.adj.set_upper(max(o["top"], self.goal))
        self.adj.set_value(self.goal)
        self.on_slide(self.adj)
        self.slider.queue_draw()
        # A stretch's goal is already in the eyebrow; only a rest day needs explaining.
        why = o.get("why") if not (self.stretching and not self.calm) else ""
        notes = ([why] if why else []) + (
            [f"{self.args.paused} is paused until you're done"] if self.args.paused else [])
        self.below.set_label(". ".join(notes) or "nothing paused")
        if self.said and self.at:
            set_headline(self.line, self.said[0].format(**self.said[1], **self.words(self.goal)))
        # Already eased, or a stretch: nothing to say hurts.
        hurts = not (self.calm or self.stretching or o.get("easy"))
        self.hurt_btn.set_visible(hurts)
        self.hold_w = HOLD_W - HURT_W - 12 if hurts else HOLD_W
        self.hold_frame.set_size_request(self.hold_w, 58)
        self.below.set_opacity(1 if notes else 0)

    def words(self, n):
        """What a coach line can say about this set: the count, the set with its unit ('30 seconds
        of plank'), and where to go for it ('Wall.', 'get to the wall')."""
        go, get = catalog.place(self.exercise)
        return {"n": n, "ex": self.exercise, "set": f"{n} {catalog.unit(self.exercise, n)}",
                "go": go, "get": get, "Get": get[0].upper() + get[1:]}

    def easy_now(self):
        """The part that hurts if the move on screen leans on it, else ''."""
        o = self.offer[self.at] if self.offer else {}
        return "" if self.calm or self.bonus or o.get("stretch") else o.get("easy") or ""

    def where_page(self):
        """Where it hurts: the body map, big, in place of the move and the slider. Click every
        spot that hurts, then log it."""
        title = label("Click every spot that hurts, left and right apart. Click again to let it go.", "meta big")
        title.set_margin_top(10)
        self.pain = PainMap(PAIN_SCALE, self.pain_changed)
        self.pain.set_halign(Gtk.Align.CENTER)
        self.pain.set_margin_top(18)
        gw, gap = musclemap.GW * PAIN_SCALE, 6 * PAIN_SCALE
        front, back = label("FRONT", "hint"), label("BACK", "hint")
        front.set_size_request(gw, -1)
        back.set_size_request(gw, -1)
        sides = hbox(gap, front, back)
        sides.set_margin_top(6)
        self.pain_name = PixelText(" ", 18, self.accent, spacing=2)
        self.pain_name.set_margin_top(14)
        self.pain_list = label(" ", "meta", wrap=True, width=60)
        self.pain_list.set_justify(Gtk.Justification.CENTER)
        self.pain_list.set_margin_top(6)
        self.pain_log = button("Log it", "primary huge", self.hurt_in)
        self.pain_log.set_halign(Gtk.Align.CENTER)
        self.pain_log.set_margin_top(18)
        keys = hint(("Click", "pick a spot"), ("Enter", "log"), ("Esc", "back"))
        keys.set_margin_top(18)
        return vbox(0, title, self.pain, sides, self.pain_name, self.pain_list, self.pain_log, keys)

    def pain_changed(self):
        """Say the spot under the pointer, what's picked, and what Log will save."""
        hot, picked = self.pain.hot, self.pain.picked
        self.pain_name.set_text((spot_name(hot) if hot else "Hover a muscle").upper())
        part = catalog.hurt_part(self.exercise)
        self.pain_list.set_label("Picked: " + catalog.spots_text([list(s) for s in picked]) if picked
                                 else f"Nothing picked yet. Log it now and it's just your {catalog.PART_NAMES[part]}."
                                 if part else "Pick at least one spot.")
        self.pain_log.set_label(f"Log {len(picked)} spot{'s' if len(picked) != 1 else ''}" if picked else "Log it")
        self.pain_log.set_sensitive(bool(picked or part))

    def say_hurt(self):
        if self.closing or not self.hurt_btn.get_visible():
            return
        self.hold_stop()
        self.pain.clear()
        self.pain_changed()
        self.swap_btn.get_parent().set_visible(False)
        self.stage_row.set_visible(False)
        self.was_said = (self.line.text, self.line.size, self.eyebrow.text)
        set_headline(self.line, "Where does it hurt?")
        self.eyebrow.set_text("IT HURTS")
        self.foot.set_visible(False)
        self.where_box.set_visible(True)

    def where_back(self):
        self.line.set_text(*self.was_said[:2])
        self.eyebrow.set_text(self.was_said[2])
        self.swap_btn.get_parent().set_visible(True)
        self.stage_row.set_visible(True)
        self.where_box.set_visible(False)
        self.foot.set_visible(True)

    def hurt_in(self):
        """The daemon writes down the spots, or the move's main part when none were picked, and
        brings the card back, eased."""
        if not self.closing and (self.pain.picked or catalog.hurt_part(self.exercise)):
            self.finish(json.dumps({"result": "hurt", "exercise": self.exercise,
                                    "spots": [list(s) for s in self.pain.picked]}))

    def swap(self, step=1):
        if self.closing or len(self.offer) < 2:
            return
        self.at = (self.at + step) % len(self.offer)
        self.show_offer()

    def unit_text(self, v):
        e = catalog.info(self.exercise)
        if e.get("hard") and e.get("sets", 1) > 1:
            return f"{catalog.unit(self.exercise, v)}, {e['sets']} sets"
        per = not catalog.each_side(self.exercise)
        return (catalog.unit(self.exercise, v, per) if v == self.goal
                else f"of {self.goal} {catalog.unit(self.exercise, self.goal, per)}")

    def side_text(self, v):
        e = catalog.info(self.exercise)
        if e.get("stretch"):
            # A stretch card says which stretch, how it's split up, and nothing about totals.
            return self.exercise.upper() if e.get("hard") else " "
        side = catalog.each_side(self.exercise)
        return f"EACH {side.upper()}: {2 * v} IN TOTAL" if side else " "

    def progress_text(self, v):
        e = catalog.info(self.exercise)
        if e.get("hard"):
            return e["cue"] + (". Sink in, squeeze, then go deeper" if e.get("pnf") else "")
        c = self.challenge
        if not c:
            return " "
        if self.exercise == "bulgarian split squats":
            b = c["bss"]
            return f"Bulgarians: set {b['sets'] + 1} of {b['cap']} today"
        if self.exercise == "body rows" and c.get("rows", {}).get("cap"):
            r = c["rows"]
            return f"Rows: set {r['sets'] + 1} of {r['cap']} today, chest to the edge"
        if self.exercise not in ("push-ups", "decline push-ups"):
            return " "
        total = c["done"] + v
        if total >= c["target"]:
            return f"{total} push-ups today, the {c['target']} is done"
        return f"{total} of {c['target']} push-ups today"

    def on_slide(self, adj):
        v = round(adj.get_value())
        if v != adj.get_value():
            adj.set_value(v)
            return
        self.hero.set_text(str(v))
        self.hero.set_color(SLATE["fg8"] if v < self.goal else SLATE["fg10"])
        text = self.unit_text(v)
        self.unit.set_text(text, 27 if len(text) <= 16 else 18)
        self.side.set_text(self.side_text(v))
        self.progress.set_label(self.progress_text(v))

    def show_hold(self, frac):
        self.hold_fill.set_size_request(int(self.hold_w * min(1, frac)), -1)
        on = frac > 0
        self.hold_label.set_label(("Keep holding to pass" if self.calm or self.bonus or self.easy_now() else "Keep holding to skip")
                                  if on else self.skip_word)
        (self.hold_label.add_css_class if on else self.hold_label.remove_css_class)("on")

    def hold_start(self):
        if self.holding or self.closing or not self.ready:
            return
        # Past the target a pass is free, so it only needs to be more than a stray keypress.
        need = 0.6 if self.bonus else 1.2 + 0.4 * self.tier
        start = time.monotonic()

        def tick():
            if self.holding is None:
                return False
            frac = (time.monotonic() - start) / need
            self.show_hold(frac)
            if frac >= 1:
                self.holding = None
                self.react("skip")
                return False
            return True

        self.holding = GLib.timeout_add(16, tick)

    def hold_stop(self):
        if self.holding:
            GLib.source_remove(self.holding)
            self.holding = None
            self.show_hold(0)

    def log_set(self):
        if self.timer:
            # Stopped early: log the shorter of the sides that were held. A side never started
            # doesn't drag it to zero, which would read as a skipped set.
            state = self.timer_state
            held = list(state["held"])
            side = state["phases"][state["phase"]][3]
            if side:
                held.append((side, round(state["phases"][state["phase"]][1] - max(0, state["end"] - time.monotonic()))))
            GLib.source_remove(self.timer)
            self.timer = None
            self.set_held(held)
        self.react(f"done {round(self.adj.get_value())}")

    def answer(self, result):
        """What the card prints for the daemon: JSON with the exercise it was about."""
        gentle = self.calm or self.bonus or self.easy_now()
        if result == "skip" or (result == "done 0" and gentle):
            return json.dumps({"result": "pass" if gentle else "skip", "exercise": self.exercise})
        if result.startswith("done "):
            return json.dumps({"result": "done", "did": int(result.split()[1]), "exercise": self.exercise})
        return result

    # The hold timer, for stretches: counts the hold down, each side in turn, then logs it.

    @staticmethod
    def held_total(held):
        """What a stopped or finished hold logs: the seconds on the weaker side, every set added up.
        A side never started doesn't drag it to zero, which would read as a skipped set."""
        sides = {}
        for side, secs in held:
            sides[side] = sides.get(side, 0) + max(0, secs)
        sides = {k: v for k, v in sides.items() if v > 0} or sides
        return min(sides.values()) if sides else 0

    def set_held(self, held):
        # Two sets of 30 s log 60, past the slider's end, so the slider makes room first.
        total = self.held_total(held)
        self.adj.set_upper(max(self.adj.get_upper(), total))
        self.adj.set_value(total)

    def phases(self, target):
        """The hold as phases: (eyebrow, seconds, what the number means, side or None for a break).
        Sided stretches do the right side, a few seconds to switch, the left. Stretches with sets
        rest between them. A PNF stretch splits each hold the way David teaches it: sink in, squeeze
        without moving, then relax and go deeper."""
        e = catalog.info(self.exercise)
        sides = ("RIGHT", "LEFT") if e.get("sided") else ("",)
        sets = int(e.get("sets", 1))
        out = []
        for n in range(sets):
            for j, side in enumerate(sides):
                if out:
                    out.append(("SWITCH SIDES", self.args.switch, "seconds to switch sides", None) if j
                               else ("REST", REST_BETWEEN_SETS, "seconds, then the next set", None))
                where = f"SET {n + 1} OF {sets}" if sets > 1 else ""
                where = ", ".join(w for w in (where, f"{side} SIDE" if side else "") if w)
                key = side or "hold"
                if e.get("pnf"):
                    a = max(3, target // 3)
                    parts = (("SINK IN", a, "seconds, sink in and hold"), ("SQUEEZE", a, e["pnf"]),
                             ("RELAX, GO DEEPER", target - 2 * a, "seconds, breathe out and sink"))
                else:
                    parts = (("HOLD", target, "seconds left"),)
                for name, length, unit in parts:
                    out.append((f"{where}: {name}" if where else name, length, unit, key))
        return out

    def timer_start(self, skip=0.0):
        """Count the hold down in phases (see phases()). A chime marks every change, since you're on
        the floor and not looking at the screen."""
        if self.timer or self.closing or not self.ready:
            return
        target = max(5, round(self.adj.get_value()))  # what the slider says, not the card's goal
        phases = self.phases(target)
        state = {"phase": 0, "end": time.monotonic() + phases[0][1], "target": target, "held": [], "phases": phases}
        self.timer_state = state
        self.log_btn.set_label("Done, log it")
        self.slider.set_sensitive(False)

        def enter(i):
            brow, length, unit, side = phases[i]
            state["phase"], state["end"] = i, time.monotonic() + length
            self.eyebrow.set_text(brow)
            self.unit.set_text(unit, 18)
            self.hero.set_color(SLATE["fg7"] if side is None else WIN if "SQUEEZE" in brow else SLATE["fg10"])

        def tick():
            left = state["end"] - time.monotonic()
            if left > 0:
                self.hero.set_text(str(math.ceil(left)))
                return True
            brow, length, _, side = phases[state["phase"]]
            if side:
                state["held"].append((side, length))
            if state["phase"] + 1 < len(phases):
                self.chime()
                enter(state["phase"] + 1)
                return True
            self.chime()
            self.timer = None
            self.set_held(state["held"])
            self.log_set()
            return False

        enter(0)
        # Tests and screenshots can start partway in.
        while skip > 0:
            length = phases[state["phase"]][1]
            if skip < length or state["phase"] + 1 == len(phases):
                state["end"] -= skip
                break
            skip -= length
            if phases[state["phase"]][3]:
                state["held"].append((phases[state["phase"]][3], length))
            enter(state["phase"] + 1)
        tick()
        self.timer = GLib.timeout_add(100, tick)

    def chime(self):
        if self.args.quiet:
            return
        sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
        player = shutil.which("pw-play") or shutil.which("paplay")
        if player and os.path.exists(sound):
            subprocess.Popen([player, sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def eyebrow_is_switch(self):
        """In a break between holds: switching sides or resting between sets."""
        return bool(self.timer_state) and self.timer_state["phases"][self.timer_state["phase"]][3] is None

    def stretch_main(self):
        """The stretch card's big button: start the hold, or end it early and log what was held."""
        if self.timer:
            self.log_set()
        else:
            self.timer_start()

    def timer_stop(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
            self.log_btn.set_label("Start the timer")
            self.slider.set_sensitive(True)

    def reaction_text(self, did):
        n, spicy = self.goal, self.spice == "spicy"
        if self.stretching:
            part = max(muscles_of(self.exercise)[1].items(), key=lambda kv: kv[1], default=("legs", 1))[0]
            if did >= n:
                return random.choice([f"Held. Your {part} owe you one.", "Good. A little looser than yesterday.",
                                      "That's how splits happen. Slowly."])
            return random.choice([f"{did} of {n}. Still counts.", "Shorter, but you did it."])
        m1 = self.momentum + 1  # this set included
        if self.bonus and did < n:
            # Past the target nothing is short: it's all extra.
            pool = [f"{did} extra. Every one counts.", "Bonus reps are still reps. Logged."]
        elif self.easy_now() and did < n:
            pool = [f"{did} on sore {catalog.PART_NAMES[self.easy_now()]}. That counts.", f"{did}. Gentle is fine today. Logged."]
        elif did > n:
            pool = [f"{did}? Hell yes. Above the ask.", f"{did} when I asked for {n}. That's how it's done."] if spicy \
                else [f"{did}. Above the ask. That's strength showing up.", f"{did} when I asked for {n}. Good."]
        elif did == n and self.tier >= 2:
            pool = ["That's how you answer a skip. Good.", "There you are. The chain starts again."]
        elif did == n and m1 >= 3:
            pool = [f"{m1} in a row. That's a habit now.", f"Clean set. {m1} straight."]
        elif did == n:
            pool = ["Done. Solid set.", "Goal hit. Back to your show.", "Clean set. That's the job."] if spicy \
                else ["Clean set. Good work.", "Done. That's the job.", "Goal hit. Back to it."]
        elif did >= n * 0.6:
            pool = [f"{did} of {n}. Close, not done. Next one.", f"{did} of {n}. Logged. Finish the next one."] if spicy \
                else [f"{did} of {n}. Logged. Finish the next one.", f"{did} of {n}. Close. The next one goes the distance."]
        else:
            pool = [f"{did} of {n}. Weak set. Shake it off and own the next one."] if spicy \
                else [f"{did} of {n}. Something beats nothing. Logged."]
        return random.choice(pool)

    def react(self, result, close=True):
        """Three different endings: goal hit, short, and skipped."""
        if self.closing:
            return
        self.closing = close
        self.key_released = False
        self.hold_stop()
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        self.swap_btn.get_parent().set_visible(False)
        did = int(result.split()[1]) if result.startswith("done") else 0
        if self.challenge and self.exercise in PUSH:
            self.progress.set_label(" ")  # the ending's count says it again, right below
        for w in (self.card, self.backdrop):
            w.remove_css_class("shake")
        mood = "punished"
        if did > 0:
            won = did >= self.goal
            mood = "won" if won else "short"
            if won:
                self.card.add_css_class("won")
                self.backdrop.add_css_class("won")
                self.eyebrow.set_text("OVER THE GOAL" if did > self.goal else "GOAL HIT")
                self.eyebrow.set_color(WIN)
                self.hero.set_color(WIN)
            else:
                self.eyebrow.set_text("BONUS LOGGED" if self.bonus and not self.stretching else "LOGGED. SHORT.")
                self.eyebrow.set_color(SLATE["fg8"])
            set_headline(self.line, self.reaction_text(did))
            if self.stretching:
                # A stretch adds no reps: its ending is the hold and what it loosened.
                # The hero already says how long; the ending says where the hold is going.
                total = hbox(0, label(self.offer[self.at].get("progress") or "Logged.", "meta big"))
                # The stage already shows what it loosened; the ending only needs the number.
                graph = Gtk.Box()
            else:
                # Sets, not reps: twelve push-ups and forty seconds of plank don't add up to anything.
                # In the challenge the push-ups do, toward the day's target.
                done = self.done_today + [(self.exercise, did)]
                c = self.challenge
                if c and self.exercise in PUSH:
                    got = c["done"] + did
                    count = PixelText(str(got), 36, WIN if got >= c["target"] else SLATE["fg10"])
                    where = (("that set finished the target" if got - did < c["target"] else f"{got - c['target']} past the target")
                             if got >= c["target"] else f"{c['target'] - got} to go")
                    lines = (f"of {c['target']} push-ups today, {where}", ", ".join(catalog.day_lines(done)))
                else:
                    count = PixelText(str(len(done)), 36, SLATE["fg10"])
                    lines = (catalog.plural(len(done), "set").split(" ", 1)[1] + " today", ", ".join(catalog.day_lines(done)))
                total = hbox(14, count, vbox(2, label(lines[0], "meta big", xalign=0),
                                             label(lines[1], "meta", xalign=0, wrap=True, width=46)))
                events = self.stats.get("today", []) + [{"t": time.time(), "exercise": self.exercise, "did": did,
                                                         "goal": self.goal, "result": "hit" if won else "short"}]
                graph = DayChart(events, self.accent, 720, 150, target=c and self.exercise in PUSH and c["target"],
                                 deadline=c and c["deadline"], mark_last=True)
            total.get_last_child().set_valign(Gtk.Align.CENTER)
            graph.set_halign(Gtk.Align.CENTER)
            graph.set_margin_top(14)
            if won:
                chip = hbox(10, pixel_icon(ICONS["check"], {"#": WIN}, cell=3), label("goal", ""))
                chip.add_css_class("goal-chip")
                chip.get_first_child().set_valign(Gtk.Align.CENTER)
                chip.set_margin_bottom(16)
                self.after.append(chip)
            self.after.append(total)
            self.after.append(graph)
            wait = 4000
        elif self.calm or self.bonus or self.easy_now():
            # A rest day pass, a bonus card past the target, or a part that hurts: no red, no grudge.
            mood = "short"
            self.eyebrow.set_text("PASSED")
            self.eyebrow.set_color(SLATE["fg8"])
            if self.easy_now() and not (self.calm or self.bonus):
                set_headline(self.line, random.choice(HURT_PASSES))
                self.after.append(label(f"Your {catalog.PART_NAMES[self.easy_now()]} hurt. A pass doesn't count against you.", "meta big"))
            elif self.calm:
                set_headline(self.line, "Fine. Rest up, then.")
                self.after.append(label("Rest days don't count against you.", "meta big"))
            else:
                set_headline(self.line, random.choice(BONUS_PASSES[self.spice]))
                c = self.challenge
                why = ("The push-ups are in." if self.stretching
                       else f"{c['done']} push-ups today, {c['done'] - c['target']} past the target.")
                self.after.append(label(f"{why} A pass here costs nothing.", "meta big"))
            wait = 2000
        else:
            self.card.add_css_class("punished")
            self.backdrop.add_css_class("punished")
            self.eyebrow.set_text("SKIPPED" if result == "skip" else "ZERO")
            self.eyebrow.set_color(RED)
            text = SKIP_REACTIONS[self.spice][min(self.args.skips, 4)] if result == "skip" else "Zero is a skip. Next one, do something."
            set_headline(self.line, text)
            self.line.set_color(RED)
            self.hero.set_text(str(self.goal))
            self.hero.set_color(SLATE["fg6"], strike=RED)
            self.unit.set_text(f"{catalog.unit(self.exercise, self.goal)}, not done", 18)
            self.unit.set_color(SLATE["fg7"])
            # Where the chart would be: the shame.
            skips = self.args.skips + 1
            shame = hbox(18, PixelText(str(skips), 72, RED),
                         vbox(4, label(f"skip{'s' if skips != 1 else ''} in a row", "meta big", xalign=0),
                              label(f"Last set: {ago(self.args.last)}", "meta", xalign=0),
                              label(self.skip_cost(), "meta", xalign=0)))
            shame.get_last_child().set_valign(Gtk.Align.CENTER)
            self.after.append(shame)
            wait = 2600

        # A way out: a bar that runs down to the auto close, and Enter to go now.
        bar = Gtk.ProgressBar()
        bar.add_css_class("countdown")
        bar.add_css_class(mood)
        bar.set_fraction(1)
        bar.set_halign(Gtk.Align.CENTER)
        bar.set_margin_top(28)
        out = hint(("Enter", "close now"))
        out.set_margin_top(14)
        self.after.append(bar)
        if close:
            stats = keyed("Full stats", "R", "pill", self.open_receipts)
            stats.set_halign(Gtk.Align.CENTER)
            stats.set_margin_top(18)
            self.after.append(stats)
        self.after.append(out)
        if not close:
            self.foot.set_transition_type(Gtk.StackTransitionType.NONE)
            bar.set_fraction(0.6)
        self.foot.set_visible_child_name("after")
        if close:
            result = self.answer(result)
            self.pending = result
            if result.startswith("{"):
                self.with_receipts = json.dumps({**json.loads(result), "receipts": True})
            start = time.monotonic()

            def tick():
                left = 1 - (time.monotonic() - start) * 1000 / wait
                bar.set_fraction(max(0, left))
                if left <= 0:
                    self.finish(result)
                    return False
                return True

            GLib.timeout_add(30, tick)

    def skip_cost(self):
        """What the skip leaves undone, said plainly: the day's push-ups, or the run it just broke."""
        c = self.challenge
        if c and c["done"] < c["target"]:
            return f"{c['target'] - c['done']} push-ups still to go today."
        if self.momentum >= 3 and not self.args.skips:
            return f"That ends a run of {self.momentum} sets."
        return "One set with reps in it clears the count."

    def open_receipts(self):
        """From the ending: close now and ask for the receipts, which open once the set is logged."""
        if self.with_receipts:
            self.finish(self.with_receipts)

    # The receipts: today set by set, the month, how many cards got done, how strong the sets are,
    # the week on the body and every move's totals.

    def verdict(self):
        """The coach's one line on the receipts: what the numbers say, good or bad."""
        c, week = self.challenge, self.stats.get("week") or {}
        total = sum(week.values())
        done = week.get("hit", 0) + week.get("short", 0)
        if not self.args.last:
            return "Nothing logged yet. The first set is the hardest one."
        if c:
            hist = c.get("history", [])
            missed = len(hist) >= 2 and hist[-2]["done"] == 0 and not hist[-2].get("trained")
            if c.get("trained"):
                return f"Trained today: {', '.join(c['trained'])}. The push-ups take the day off."
            if c["done"] >= c["target"] * 1.5:
                return f"{c['done']} today. That's a big day. Well done."
            if c["done"] >= c["target"]:
                return "Target's in. Everything from here is extra."
            if missed:
                return "Yesterday was a zero. Today you answer it." if c["done"] else "Yesterday was a zero. Don't make it two."
        if total < 3:
            return "Not enough cards yet to judge. Keep going."
        share = done / total
        if share >= 0.9:
            return f"{done} of {total} cards done this week. That's discipline."
        if share >= 0.7:
            return "Mostly showing up. Tighten up the skips."
        if share >= 0.5:
            return "Half in, half out. Pick a side."
        return "You skipped more than you did this week. That changes now."

    def graph(self):
        a = self.args
        last = a.last and dt.datetime.fromtimestamp(a.last).strftime("%a %-d %b, %H:%M")
        brow = f"THE RECEIPTS: DAY {self.challenge['day']} OF {self.challenge['days']}" if self.challenge else "THE RECEIPTS"
        line = PixelText(balance(self.verdict(), 44), 27, SLATE["fg10"], align=Pango.Alignment.LEFT)
        line.set_halign(Gtk.Align.START)
        eyebrow = PixelText(brow, 18, EYEBROW_COLOR[0], spacing=4)
        eyebrow.set_halign(Gtk.Align.START)
        when = label(f"Last set {ago(a.last)}: {catalog.describe_set(a.last_exercise, a.last_reps)}, {last}" if last
                     else "Nothing logged yet", "meta", xalign=0)
        head = vbox(10, eyebrow, line, when)
        head.set_hexpand(True)

        self.receipt_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=140)
        self.receipt_stack.set_vhomogeneous(True)
        self.receipt_stack.set_hhomogeneous(True)
        self.receipt_tabs = {}
        for key, (name, build) in enumerate((("Overview", self.overview_panel), ("Body", self.body_panel),
                                             ("Moves", self.moves_panel), ("Timeline", self.timeline_panel)), 1):
            page = build()
            page.set_valign(Gtk.Align.START)
            page.set_hexpand(True)
            self.receipt_stack.add_named(page, str(key))
            t = clickable(Gtk.Button())
            t.set_child(hbox(10, label(name, ""), keycap(str(key))))
            t.add_css_class("tab")
            t.connect("clicked", lambda _b, k=key: self.receipt_tab(k))
            self.receipt_tabs[key] = t
        tabs = hbox(6, *self.receipt_tabs.values(), center=False)
        tabs.set_valign(Gtk.Align.START)
        top = hbox(24, head, tabs, center=False)
        self.receipt_stack.set_margin_top(22)
        self.receipt_tab(min(len(self.receipt_tabs), max(1, a.page)))

        now = button("Do a set now", "primary huge", lambda: self.finish("now"))
        add = keyed("Log training", "L", "pill", lambda: self.finish("add"))
        close = button("Close", "quiet", lambda: self.finish("closed"))
        self.armed += [now, add, close]
        actions = hbox(24, close, add, now)
        add.set_valign(Gtk.Align.CENTER)
        actions.set_margin_top(22)
        keys = hint(("Tab", "next tab"), ("Enter", "a set now"), ("Esc", "close"))
        keys.set_margin_top(14)
        card = vbox(0, top, self.receipt_stack, actions, keys)
        card.set_size_request(1180, -1)
        return card

    def receipt_tab(self, key):
        self.receipt_stack.set_visible_child_name(str(key))
        for k, t in self.receipt_tabs.items():
            (t.add_css_class if k == key else t.remove_css_class)("on")

    def stat(self, value, name, note="", color=SLATE["fg10"], size=54):
        """A number that matters, what it is, and what it means against last week."""
        num = PixelText(value, size, color)
        num.set_halign(Gtk.Align.START)
        col = vbox(4, num, label(name, "stat-name", xalign=0))
        if note:
            col.append(label(note, "meta", xalign=0, wrap=True, width=34))
        return col

    def overview_panel(self):
        a, st, c = self.args, self.stats, self.challenge
        events = st.get("today", [])
        sets = [e for e in events if e["did"] > 0]
        skipped = sum(1 for e in events if e["result"] == "skipped")
        # Today: the count, how it's going, and every set on the clock.
        if c:
            count = PixelText(f"{c['done']} / {c['target']}", 54, WIN if c["done"] >= c["target"] else SLATE["fg10"])
            what = "push-ups today. " + self.pace_text()
        else:
            count = PixelText(str(len(sets)), 54, SLATE["fg10"])
            what = f"{'set' if len(sets) == 1 else 'sets'} today: " + (", ".join(catalog.day_lines(self.done_today)) or "none yet")
        count.set_halign(Gtk.Align.START)
        biggest = max((e["did"] for e in sets if e["exercise"] in PUSH), default=0)
        facts = ([catalog.plural(len(sets), "set")] if c else []) + ([f"{skipped} skipped"] if skipped else ["no skips"]) + (
            [f"biggest push-up set {biggest}"] if biggest else [])
        today = vbox(
            6,
            label("Today", "panel-title", xalign=0),
            hbox(18, count, label(what, "meta big", xalign=0, wrap=True, width=40), center=False),
            label(", ".join(facts).capitalize() + ".", "meta", xalign=0),
        )
        chart = DayChart(events, self.accent, 700, 210, target=c and c["target"], deadline=c and c["deadline"])
        chart.set_margin_top(14)
        legend = ("Each column is a set of push-ups, stacked toward the target. Dashed: the pace that finishes "
                  "an hour early. Small squares are other moves." if c else
                  "Each column is a set, as tall as its share of the goal. Green hit it.")
        today = vbox(0, today, chart, label(legend + " Red crosses are skips.", "legend", xalign=0, wrap=True, width=90))
        today.add_css_class("panel")
        today.set_hexpand(True)

        # The three numbers a coach looks at: showing up, every card, and getting stronger.
        h = self.history or [0]
        streak = a.streak if a.streak >= 0 else len(streak_days(h))
        week, prev = st.get("week") or {}, st.get("last_week") or {}

        def share(w):
            t = sum(w.values())
            return (w.get("hit", 0) + w.get("short", 0)) / t if t else None

        now_s, prev_s = share(week), share(prev)
        total = sum(week.values())
        follow = self.stat(f"{round(now_s * 100)}%" if now_s is not None else "-", "of cards done, last 7 days",
                           (f"{total - week.get('skipped', 0)} of {total}"
                            + (f". Last week {round(prev_s * 100)}%." if prev_s is not None else ".")) if total else
                           "No cards yet this week.",
                           WIN if (now_s or 0) >= 0.9 else RAGE[3] if now_s is not None and now_s < 0.5 else SLATE["fg10"])
        main = st.get("main", "push-ups")
        (best, avg), (pbest, _) = self.strength()
        if best and pbest:
            diff = best - pbest
            trend = (f"Up {diff} on last week" if diff > 0 else f"Down {-diff} on last week" if diff < 0
                     else "Same as last week")
        else:
            trend = "First week of it"
        u = " s" if catalog.info(main).get("timed") else ""
        strong = self.stat(f"{best}{u.strip()}" if best else "-", f"best set of {main}, 7 days",
                           f"{trend}. Average set {avg:.1f}{u}." if best else "No sets this week.",
                           WIN if best and pbest and best > pbest else SLATE["fg10"])
        days = self.stat(str(streak), "day streak" if streak != 1 else "day streak, keep it going",
                         "Days in a row with a set or training.", self.accent)
        numbers = vbox(22, days, follow, strong)
        numbers.add_css_class("panel")
        numbers.set_size_request(340, -1)
        upper = hbox(14, today, numbers, center=False)

        month = self.month_panel()
        month.set_margin_top(14)
        return vbox(0, upper, month)

    def strength(self):
        days = self.stats.get("days") or []

        def window(ds):
            best = max((d["best"] for d in ds), default=0)
            n = sum(d["main_sets"] for d in ds)
            return best, (sum(d["reps"] for d in ds) / n if n else 0)
        return window(days[-7:]), window(days[-14:-7])

    def pace_text(self):
        """Where the push-ups stand against the pace that finishes an hour before the deadline."""
        c = self.challenge
        if c["done"] >= c["target"]:
            return "Target done, every set now is extra."
        pushes = [e for e in self.stats.get("today", []) if e["exercise"] in PUSH and e["did"] > 0]
        end = c["deadline"] - 3600
        if not pushes or end <= pushes[0]["t"]:
            return f"{c['target'] - c['done']} to go."
        frac = min(1, max(0, (time.time() - pushes[0]["t"]) / (end - pushes[0]["t"])))
        ahead = c["done"] - round(c["target"] * frac)
        pace = (f"{ahead} ahead of pace" if ahead > 0 else "right on pace" if ahead == 0 else f"{-ahead} behind pace")
        return f"{c['target'] - c['done']} to go, {pace}."

    def month_panel(self):
        c = self.challenge
        if c:
            b = c["bss"]
            title = f"The month: day {c['day']} of {c['days']}"
            r = c.get("rows") or {}
            note = (f"Bulgarians {b['sets']} of {b['cap']} sets today, {b['goal']} each leg. "
                    + (f"Rows {r['sets']} of {r['cap']}. " if r.get("cap") else "")
                    + ("Feet up on." if c.get("feet_up") else "Feet up off until you say so."))
            cols = challenge_cols(c)
            missed = sum(1 for col in cols if col["when"] == "past" and not col["value"])
            hit = sum(1 for col in cols if col["when"] != "future" and col["value"] >= col["target"])
            # Today only counts against you once it's over; hit, it counts already.
            days = c["day"] if c["done"] >= c["target"] else c["day"] - 1
            sub = (f"Target hit {hit} of {days} days" + (f", {missed} missed" if missed else "") + "."
                   if days else "First day. Nothing to count yet.")
        else:
            title = f"The last {len(self.stats.get('days') or [])} days"
            days = self.stats.get("days") or []
            today = len(days) - 1
            cols = []
            for i, d in enumerate(days):
                day = dt.date.fromisoformat(d["date"])
                lab = "today" if i == today else day.strftime("%-d %b") if day.weekday() == 0 else ""
                cols.append({"value": d["sets"], "skips": d["skips"], "when": "today" if i == today else "past",
                             "label": lab})
            active = sum(1 for d in days if d["sets"])
            note = "Sets a day. Hollow red on top is that day's skips."
            sub = f"Active {active} of the last {len(days)} days."
        head = hbox(18, label(title, "panel-title", xalign=0), label(sub, "meta", xalign=0), center=False)
        foot = label(note, "legend", xalign=0)
        panel = vbox(10, head, MonthChart(cols, self.accent, 1140, 140), foot)
        panel.add_css_class("panel")
        return panel

    def timeline_panel(self):
        """The last four weeks by muscle group: what got worked, on which day, and what did it."""
        t = self.stats.get("timeline")
        if not t or not (t["sessions"] or any(any(t["sets"][g]) for g, _, _ in GROUP_ROWS)):
            panel = vbox(16, label("Muscle groups, last 4 weeks", "panel-title", xalign=0),
                         label("Nothing logged yet. Do a set and it shows up here.", "meta", xalign=0))
            panel.add_css_class("panel")
            return panel
        totals = {g: sum(t["sets"][g]) for g, _, _ in GROUP_ROWS}
        most = max(totals, key=totals.get)
        names = {g: name.lower() for g, name, _ in GROUP_ROWS}
        rows = (self.challenge or {}).get("rows") or {}
        behind = sorted((g for g in totals if totals[g] * 4 < totals[most]), key=totals.get)
        if totals[most] == 0:
            note = "No sets in four weeks yet."
        elif behind:
            note = "In four weeks: " + ", ".join([f"{names[most]} {totals[most]} sets",
                                                  *(f"{names[g]} {totals[g]}" for g in reversed(behind))]) + "."
            if "pull" in behind and rows.get("cap"):
                note += f" Rows come round in the challenge now, {rows['cap']} sets a day."
        else:
            note = f"Most for {names[most]}, and nothing far behind."
        legend = label("A column is a day, warmer is more work, a white ring is logged training. A set counts for what it "
                       "mainly works: push-ups are push, not core.", "legend", xalign=0)
        days = Gtk.Grid(column_spacing=24, row_spacing=6)
        for r, d in enumerate(t.get("recent") or []):
            when = dt.date.fromisoformat(d["date"])
            name = "Today" if r == 0 else when.strftime("%a %-d %b")
            days.attach(label(name, "row-title" if r == 0 else "row-body", xalign=0), 0, r, 1, 1)
            days.attach(label(", ".join(d["lines"]) if d["lines"] else "nothing", "row-body" + ("" if d["lines"] else " faint"),
                              xalign=0), 1, r, 1, 1)
        week = vbox(10, label("The last 7 days", "panel-title", xalign=0), days)
        week.set_margin_top(10)
        summary = label(note, "meta big", xalign=0, wrap=True, width=110)
        summary.set_width_chars(90)  # a wrapped label measures its height at its narrowest otherwise
        panel = vbox(14, label("Muscle groups, last 4 weeks", "panel-title", xalign=0), legend, TimelineChart(t),
                     summary, week)
        panel.add_css_class("panel")
        return panel

    def moves_panel(self):
        """Every move done in the last 30 days: this week, the best set, the last one, two weeks of bests."""
        rows = self.stats.get("moves") or []
        grid = Gtk.Grid(column_spacing=36, row_spacing=14)
        heads = ("Move", "This week", "30 days", "Best set", "Last set", "Best set a day, last 14 days")
        for i, text in enumerate(heads):
            grid.attach(label(text, "legend", xalign=0), i, 0, 1, 1)
        for r, m in enumerate(rows[:9], 1):
            name = m["exercise"]
            s = " s" if catalog.info(name).get("timed") or catalog.is_stretch(name) else ""
            dot = Gtk.Box()
            dot.add_css_class("swatch")
            dot.add_css_class("worked" if not catalog.is_stretch(name) else "stretched")
            dot.set_valign(Gtk.Align.CENTER)
            grid.attach(hbox(12, dot, label(title_case(name), "row-title", xalign=0), center=False), 0, r, 1, 1)
            week = f"{catalog.plural(m['sets7'], 'set')}, {m['total7']}{s}" if m["sets7"] else "none"
            grid.attach(label(week, "row-body" + ("" if m["sets7"] else " faint"), xalign=0), 1, r, 1, 1)
            grid.attach(label(catalog.plural(m["sets30"], "set"), "row-body", xalign=0), 2, r, 1, 1)
            grid.attach(label(f"{m['best']}{s}", "row-body", xalign=0), 3, r, 1, 1)
            grid.attach(label(f"{m['last']}{s}", "row-body", xalign=0), 4, r, 1, 1)
            grid.attach(Spark(m["trend"], tint_for(name), 330, 30), 5, r, 1, 1)
        if len(rows) > 9:
            more = ", ".join(title_case(m["exercise"]) for m in rows[9:])
            grid.attach(label(f"Fewer sets: {more}.", "meta", xalign=0, wrap=True, width=110), 0, 10, 6, 1)
        if not rows:
            grid.attach(label("Nothing in the last 30 days. Do a set and it shows up here.", "meta", xalign=0), 0, 1, 6, 1)
        panel = vbox(16, label("Every move, last 30 days", "panel-title", xalign=0), grid)
        panel.add_css_class("panel")
        return panel

    def body_panel(self):
        """The week on the body: what got worked, what got stretched, every muscle ranked, and
        the ones that got least."""
        m = json.loads(self.args.muscles) if self.args.muscles else {}
        work, stretch = m.get("work", {}), m.get("stretch", {})
        raw_w, raw_s = m.get("work_raw", {}), m.get("stretch_raw", {})

        def map_column(title, legend, view):
            sides = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
            sides.append(label("front", "legend"))
            sides.append(label("back", "legend"))
            return vbox(8, label(title, "panel-title", xalign=0), view, sides, label(legend, "legend", xalign=0))

        worked = map_column("Worked this week", "brighter is more reps", MuscleView(work, {}, scale=4))
        stretched = map_column("Stretched this week", "brighter is longer holds", MuscleView({}, stretch, scale=4))

        top_w = max([1.0, *raw_w.values()])
        top_s = max([1.0, *raw_s.values()])
        ranked = vbox(5, label("Every muscle, most worked first", "panel-title", xalign=0))
        for name in sorted(catalog.MUSCLES, key=lambda k: -raw_w.get(k, 0)):
            w, st = raw_w.get(name, 0), raw_s.get(name, 0)
            row = hbox(10, center=False)
            text = label(name, "muscle-name" + ("" if w or st else " low"), xalign=0)
            text.set_size_request(96, -1)
            meter = vbox(2)
            meter.set_valign(Gtk.Align.CENTER)
            for value, top, cls in ((w, top_w, "mbar"), (st, top_s, "sbar")):
                track = Gtk.Box()
                track.add_css_class("mtrack")
                track.set_size_request(170, -1)
                fill = Gtk.Box()
                fill.add_css_class(cls)
                fill.set_size_request(max(0, round(170 * value / top)), -1)
                fill.set_opacity(1 if value else 0)
                track.append(fill)
                meter.append(track)
            row.append(text)
            row.append(meter)
            ranked.append(row)
        least = m.get("neglected") or []
        if least:
            note = vbox(2, label(f"Least worked: {', '.join(least)}.", "meta", xalign=0),
                        label("The next cards lean their way.", "meta", xalign=0))
            note.set_margin_top(10)
            ranked.append(note)
        ranked.set_valign(Gtk.Align.START)
        panel = hbox(48, worked, stretched, ranked)
        panel.add_css_class("panel")
        return panel

    # The intro

    def intro(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT, transition_duration=220)
        self.stack.set_vhomogeneous(True)
        self.stack.set_hhomogeneous(True)

        self.stack.add_named(vbox(
            12,
            PixelText("PUSH-UP BREAKS", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText("Out of the chair", 36, SLATE["fg10"]),
            label("About once an hour, a random number of push-ups. Do them, log them.", "body"),
            self.preview_card("Floor. 14 push-ups. Let's go."),
        ), "1")

        rows = vbox(10)
        icon_colors = {"#": SLATE["fg8"], "o": SLATE["mid"]}
        for icon, title, body in (
            ("video", "Videos pause", "YouTube and anime stop while you're on the floor, then carry on."),
            ("music", "Music keeps playing", "Spotify and other music players are left alone."),
            ("call", "Discord calls come first", "In a voice call, nothing pops up. The set waits until you hang up."),
            ("grudge", "No snooze. It keeps count", "Skipping means holding Esc. Every skip in a row makes it rougher, "
                                                 "and one done set clears it."),
        ):
            colors = {"#": RED, "o": RED} if icon == "grudge" else icon_colors
            mark = pixel_icon(ICONS[icon], colors)
            mark.set_valign(Gtk.Align.CENTER)
            text = vbox(3, label(title, "row-title", xalign=0), label(body, "row-body", wrap=True, xalign=0, width=56))
            text.set_hexpand(True)
            row = hbox(18, mark, text, center=False)
            row.add_css_class("row")
            rows.append(row)
        rows.set_margin_top(18)
        self.stack.add_named(vbox(
            12,
            PixelText("AROUND YOU", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText("It knows what you're doing", 27, SLATE["fg10"]),
            rows,
        ), "2")

        self.level_buttons = self.choices(
            (("easy", "Easy", "5 to 12 reps a set"), ("normal", "Normal", "10 to 20 reps a set"),
             ("hard", "Hard", "15 to 30 reps a set")), self.pick)
        self.stack.add_named(vbox(
            12,
            PixelText("HOW MANY", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText("Reps per set", 36, SLATE["fg10"]),
            label("Where it starts. After that it follows what you log, plus one or two.", "body"),
            self.choice_list(self.level_buttons),
        ), "3")
        self.pick(self.args.level if self.args.level in self.level_buttons else None)

        self.spice_buttons = self.choices(
            (("mild", "Mild", "A coach with clean language."),
             ("spicy", "Spicy", "The same coach, swearing.")), self.pick_spice)
        self.spice_preview = self.preview_card(SAMPLES[self.spice])
        self.spice_preview.set_margin_top(18)
        self.stack.add_named(vbox(
            12,
            PixelText("THE COACH", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText("Pick the voice", 36, SLATE["fg10"]),
            label("Both cheer a run of sets and get rougher with every skip.", "body"),
            self.choice_list(self.spice_buttons),
            self.spice_preview,
        ), "4")
        self.pick_spice(self.spice)

        # The moves: every exercise as a tile, doing itself. Ticked ones are in the rotation.
        self.picked = [e for e in self.args.picked.split(",") if e in catalog.MIXED_EXERCISES] or list(catalog.DEFAULT_EXERCISES)
        # What you have: a move whose gear you lack sits greyed out, saying what it needs.
        self.gear = [t for t in self.args.gear.split(",") if t in catalog.TOOLS]
        self.gear_buttons = {}
        gear_row = hbox(6, label("You have", "meta"))
        for tool in catalog.TOOLS:
            b = clickable(Gtk.Button(label=GEAR_LABELS[tool]))
            b.add_css_class("tab")
            b.add_css_class("gear")
            b.connect("clicked", lambda _b, t=tool: self.toggle_gear(t))
            self.gear_buttons[tool] = b
            gear_row.append(b)
        gear_row.set_halign(Gtk.Align.CENTER)
        self.move_tiles = {}
        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        grid.set_halign(Gtk.Align.CENTER)
        for i, name in enumerate(catalog.MIXED_EXERCISES):
            t = self.tile(name, title_case(name), lambda n=name: self.toggle_move(n), WORKED, crop=SMALL_CROP)
            self.move_tiles[name] = t
            cols = max(7, -(-len(catalog.MIXED_EXERCISES) // 3))  # three rows, however many moves
            grid.attach(t, i % cols, i // cols, 1, 1)
        grid.set_margin_top(16)
        self.move_count = label("", "meta")
        self.toggle_move(None)
        self.stack.add_named(vbox(
            12,
            PixelText("THE MOVES", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText("More than push-ups", 36, SLATE["fg10"]),
            label("Most work on a floor, a wall or a chair. Tick what else you have: a move that needs "
                  "something you don't have never comes up.", "body", wrap=True, width=72),
            gear_row,
            self.move_count,
            grid,
        ), "5")

        # Training counts: what you did elsewhere buys you rest.
        rest_rows = vbox(10)
        for title, body in (
            ("Log it in a few clicks", "\"I trained today\" on the card, or Log training on the receipts. Climbing, weights, a run, "
                                       "anything, with how long and how hard."),
            ("Sore parts get a break", "Climbed yesterday? Back and core exercises sit out, and push-ups ask for less."),
            ("A heavy day means stretches only", "After two hard hours the cards turn into calm stretches, "
                                                 "and passing one costs nothing."),
        ):
            text = vbox(3, label(title, "row-title", xalign=0), label(body, "row-body", wrap=True, xalign=0, width=40))
            text.set_hexpand(True)
            row = vbox(0, text, cls="row")
            rest_rows.append(row)
        climb = vbox(0, FigureView(figure.TRAINING["climbing"], WORKED, scale=3, crop=(96, 0, 156, 192)), cls="stage")
        climb.set_valign(Gtk.Align.START)
        pill = label("Climbing 2h hard. Stretches only until tomorrow 14:20", "rest-pill", wrap=True, width=30)
        pill.set_margin_top(12)
        side = vbox(0, climb, pill)
        side.set_size_request(220, -1)
        body = hbox(18, rest_rows, side)
        body.set_margin_top(18)
        self.stack.add_named(vbox(
            12,
            PixelText("TRAINING COUNTS", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText("It knows when you're tired", 36, SLATE["fg10"]),
            body,
        ), "7")

        # Bendy: the stretch goals, each shown by the stretch that is the goal.
        self.goals = [g for g in self.args.goals.split(",") if g in catalog.GOALS] or list(catalog.GOALS)
        self.goal_tiles = {}
        goal_row = hbox(10)
        for goal, show, body in (("front split", "front split", "Hip flexors and hamstrings, one leg at a time"),
                                 ("middle split", "middle split", "Inner thighs: butterfly, side lunge, pancake"),
                                 ("pike", "seated pike", "Hamstrings and lower back, chest to legs"),
                                 ("hips", "pigeon", "Glutes and hips: pigeon, deep squat")):
            b = clickable(Gtk.Button())
            text = label(body, "choice-body", wrap=True, width=20)
            text.set_size_request(200, -1)
            b.set_child(vbox(6, FigureView(move_for(show), STRETCHED, scale=3, crop=(60, 48, 200, 132)),
                             PixelText(catalog.GOALS[goal].capitalize(), 18, SLATE["fg10"]), text,
                             MuscleView({}, muscles_of(show)[1], scale=2)))
            b.add_css_class("tile-btn")
            b.connect("clicked", lambda _b, g=goal: self.toggle_goal(g))
            self.goal_tiles[goal] = b
            goal_row.append(b)
        goal_row.set_margin_top(16)
        self.toggle_goal(None)
        self.stack.add_named(vbox(
            12,
            PixelText("BENDY", 18, STRETCHED, spacing=4),
            PixelText("Splits, pike, open hips", 36, SLATE["fg10"]),
            label("Every third card is a stretch with a timer, chosen for the goals you keep. It follows David "
                  "Thurin's split order: hamstrings first, then hip flexors, then both, then the split.",
                  "body", wrap=True, width=72),
            goal_row,
        ), "6")

        self.dots = [Gtk.Box() for _ in range(7)]
        for d in self.dots:
            d.add_css_class("dot")
            d.set_valign(Gtk.Align.CENTER)
        skip = button("Skip for now", "quiet", lambda: self.finish(self.intro_result()))
        top = Gtk.CenterBox()
        top.set_center_widget(hbox(10, *self.dots))
        top.set_end_widget(skip)
        top.set_margin_bottom(20)

        self.next_btn = button("Next", "primary huge", self.intro_next)
        self.next_btn.set_halign(Gtk.Align.CENTER)
        self.next_btn.set_margin_top(32)
        self.armed += [self.next_btn, skip]
        enter = hint(("Enter", "next"))
        enter.set_margin_top(14)
        self.enter_label = enter.get_first_child().get_last_child()

        card = vbox(0, top, self.stack, self.next_btn, enter)
        card.set_size_request(720, -1)
        for page in self.stack:
            page.set_valign(Gtk.Align.CENTER)
        self.stack.connect("notify::visible-child-name", lambda *_: self.sync_dots())
        self.stack.set_visible_child_name(str(min(7, max(1, self.args.page))))
        self.sync_dots()
        return card

    # The challenge, shown once before its first card.

    def challenge_intro(self):
        c = self.challenge
        rows = vbox(10)
        for move, title, body in (
            ("push-ups", "Push-ups, all day",
             f"A set every 10 to 25 minutes from when you're back at the desk, paced to be done an hour before "
             f"{dt.datetime.fromtimestamp(c['deadline']).strftime('%H:%M') if c.get('deadline') else '03:00'}. Still up and "
             "short after that, it keeps coming. Locked screen, no countdown."),
            ("decline push-ups", "Feet up, when you say so",
             "Push-ups with your feet on a chair. Nothing here pushes you into them: switch them on in the "
             "launcher, or with pushup challenge feet-up on."),
            ("bulgarian split squats", "Bulgarians, each leg",
             f"Taken easy: {c['bss']['cap']} sets a day to start, {c['bss']['goal']} on each leg. A leg number is always "
             "per leg, and the card says the total."),
            *([("body rows", "Rows at the table edge, the pull",
                f"{c['rows']['cap']} sets a day: lie under a sturdy table, knees bent and head past the edge, and pull your chest up to it. "
                "Push-ups all month need a pull to match.")] if (c.get("rows") or {}).get("cap") else []),
            *([("PNF pancake", "David's stretches, once the push-ups are in",
                "His split work, not the easy stuff: two 30 second sets you want to quit before the end, never "
                "into pain. Some squeeze halfway, then sink deeper.")] if c.get("stretching", True) else []),
        ):
            fig = FigureView(move_for(move), tint_for(move), scale=2, crop=SMALL_CROP)
            fig.set_valign(Gtk.Align.CENTER)
            text = vbox(3, label(title, "row-title", xalign=0), label(body, "row-body", wrap=True, xalign=0, width=58))
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            row = hbox(18, fig, text, center=False)
            row.add_css_class("row")
            rows.append(row)
        rows.set_margin_top(10)
        strip = challenge_strip(c, self.accent)
        strip.set_margin_top(14)
        go = button("Show me the first set", "primary huge", lambda: self.finish("start"))
        go.set_halign(Gtk.Align.CENTER)
        go.set_margin_top(22)
        self.armed.append(go)
        keys = hint(("Enter", "first set"), ("Esc", "close"))
        keys.set_margin_top(14)
        return vbox(
            12,
            PixelText(f"{c['days']} DAYS OF PUSH-UPS", 18, EYEBROW_COLOR[0], spacing=4),
            PixelText(f"Today: {c['target']} push-ups", 36, SLATE["fg10"]),
            label(f"The target climbs to {max(c.get('ramp') or [c['target']])} over two weeks and stays there. "
                  "Come up short and it waits for you instead of running off.", "body", wrap=True, width=72),
            strip, rows, go, keys,
        )

    def preview_card(self, text):
        self.preview_line = headline(text)
        card = vbox(10, self.preview_line, PixelText("14", 72, SLATE["fg10"]), PixelText("push-ups", 18, SLATE["fg9"]),
                    cls="preview")
        card.set_halign(Gtk.Align.CENTER)
        card.set_margin_top(22)
        return card

    def choices(self, options, on_pick):
        out = {}
        for i, (key, name, body) in enumerate(options):
            name_px = PixelText(name, 27, SLATE["fg10"])
            name_px.set_halign(Gtk.Align.START)
            text = vbox(6, name_px, label(body, "choice-body", xalign=0))
            text.set_hexpand(True)
            inner = hbox(18, keycap(str(i + 1)), text, center=False)
            b = clickable(Gtk.Button())
            b.set_child(inner)
            b.add_css_class("choice")
            b.connect("clicked", lambda _b, k=key: on_pick(k))
            out[key] = b
        return out

    def choice_list(self, buttons):
        col = vbox(10, *buttons.values())
        col.set_margin_top(18)
        return col

    def pick(self, name):
        self.level = name
        for n, b in self.level_buttons.items():
            (b.add_css_class if n == name else b.remove_css_class)("picked")

    def pick_spice(self, name):
        self.spice = name
        for n, b in self.spice_buttons.items():
            (b.add_css_class if n == name else b.remove_css_class)("picked")
        set_headline(self.preview_line, SAMPLES[name])

    def intro_result(self):
        return json.dumps({"level": self.level or "keep", "spice": self.spice, "exercises": self.picked,
                           "goals": self.goals, "gear": self.gear})

    def toggle_goal(self, goal):
        if goal:
            if goal in self.goals and len(self.goals) == 1:
                return  # one has to stay
            self.goals = [g for g in catalog.GOALS if (g in self.goals) != (g == goal)]
        for g, t in self.goal_tiles.items():
            (t.add_css_class if g in self.goals else t.remove_css_class)("picked")
            (t.remove_css_class if g in self.goals else t.add_css_class)("off")

    def tile(self, name, title, on_click, accent, crop=TILE_CROP):
        """A clickable tile with the move playing in it."""
        b = clickable(Gtk.Button())
        name_label = label(title, "tile-name", wrap=True, width=14)
        name_label.set_size_request(crop[2], -1)
        b.need_label = label("", "tile-need", wrap=True, width=16)
        b.need_label.set_visible(False)
        b.set_child(vbox(2, FigureView(move_for(name), accent, scale=2, crop=crop), name_label, b.need_label))
        b.add_css_class("tile-btn")
        b.connect("clicked", lambda *_: on_click())
        return b

    def toggle_gear(self, tool):
        self.gear = [t for t in catalog.TOOLS if (t in self.gear) != (t == tool)]
        if not any(catalog.can_do(e, self.gear) for e in self.picked):
            self.picked = ["push-ups", *self.picked]  # one has to stay
        self.toggle_move(None)

    def toggle_move(self, name):
        usable = [n for n in self.move_tiles if catalog.can_do(n, self.gear)]
        if name:
            on = name not in self.picked
            if not on and len([e for e in self.picked if e in usable]) == 1:
                return  # one has to stay
            self.picked = [e for e in catalog.MIXED_EXERCISES if (e in self.picked) != (e == name)]
        for t, b in self.gear_buttons.items():
            (b.add_css_class if t in self.gear else b.remove_css_class)("on")
        for n, t in self.move_tiles.items():
            can = n in usable
            t.set_sensitive(can)
            t.need_label.set_label("" if can else f"needs {catalog.needs(n)}")
            t.need_label.set_visible(not can)
            (t.add_css_class if n in self.picked and can else t.remove_css_class)("picked")
            (t.remove_css_class if n in self.picked and can else t.add_css_class)("off")
        on = len([e for e in self.picked if e in usable])
        self.move_count.set_label(f"{on} of {len(usable)} in the rotation. Click one to "
                                  f"{'leave it out' if on == len(usable) else 'switch it'}.")

    def sync_dots(self):
        page = int(self.stack.get_visible_child_name() or "1")
        for i, d in enumerate(self.dots, 1):
            (d.add_css_class if i == page else d.remove_css_class)("on")
        self.next_btn.set_label("Start" if page == 7 else "Next")
        self.enter_label.set_label("start" if page == 7 else "next")

    def intro_next(self):
        page = int(self.stack.get_visible_child_name() or "1")
        if page == 7:
            self.finish(self.intro_result())
        else:
            self.stack.set_visible_child_name(str(page + 1))

    # The logging card: what you did, how much, done.

    def add_card(self):
        self.add_kind = "training"
        self.add_name = None
        title = "Already trained? Log it" if self.args.from_card else "What did you do?"
        self.add_question = title
        self.add_head = PixelText(title, 36, SLATE["fg10"])
        head = vbox(10, self.add_head)

        self.tabs = {}
        for key, text in (("training", "Training"), ("home", "Home exercises"), ("stretch", "Stretches")):
            t = button(text, "tab", lambda k=key: self.add_tab(k))
            self.tabs[key] = t
        tabs = hbox(6, *self.tabs.values())
        tabs.set_margin_top(18)

        self.add_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.add_stack.set_vhomogeneous(False)
        self.add_stack.set_hhomogeneous(False)
        for key, names in (("training", catalog.ACTIVITIES), ("home", catalog.EXERCISES), ("stretch", catalog.STRETCHES)):
            grid = Gtk.Grid(column_spacing=8, row_spacing=8)
            grid.set_halign(Gtk.Align.CENTER)
            cols = max(7, -(-len(names) // 3))
            for i, name in enumerate(names):
                title = catalog.ACTIVITIES[name]["name"] if key == "training" else title_case(name)
                grid.attach(self.tile(name, title, lambda n=name, k=key: self.add_pick(k, n), tint_for(name),
                                      crop=SMALL_CROP), i % cols, i // cols, 1, 1)
            self.add_stack.add_named(grid, key)
        self.add_stack.set_margin_top(18)

        # The detail: the move big on the left, how much on the right.
        self.add_figure = FigureView(figure.TRAINING["other"], WORKED, crop=STAGE_CROP)
        self.add_muscles = MuscleView(scale=3)
        self.add_muscles.set_valign(Gtk.Align.CENTER)
        self.add_muscles.set_margin_end(10)
        self.add_figure.set_valign(Gtk.Align.END)
        self.add_amount = PixelText("60", 108, SLATE["fg10"])
        self.add_amount.set_halign(Gtk.Align.START)
        self.add_unit = label("minutes", "meta big", xalign=0)
        self.add_adj = Gtk.Adjustment(value=60, lower=0, upper=240, step_increment=5, page_increment=15)
        self.add_slider = RepSlider(self.add_adj, None, self.accent, step=5)
        self.add_adj.connect("value-changed", self.add_slide)
        self.effort_buttons = self.choices((("easy", "Easy", "a light one"), ("normal", "Normal", "a proper session"),
                                            ("hard", "Hard", "wrecked after")), self.pick_effort)
        efforts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        for b in self.effort_buttons.values():
            efforts.append(b)
        self.day_buttons = {d: button(text, "tab", lambda d=d: self.pick_day(d))
                            for d, text in ((0, "Today"), (1, "Yesterday"))}
        days = hbox(6, *self.day_buttons.values(), center=False)
        self.effort_box = vbox(12, efforts, days)
        self.effort_box.set_margin_top(18)
        # Home exercises and stretches: how many sets of that number, all logged in one go.
        self.sets_buttons = {n: button(f"{n} set{'s' if n > 1 else ''}", "tab", lambda n=n: self.pick_sets(n))
                             for n in range(1, 6)}
        self.sets_box = hbox(6, *self.sets_buttons.values(), center=False)
        self.sets_box.set_margin_top(18)
        self.recent = json.loads(self.args.recent or "{}")
        self.sets = 1
        info_col = vbox(4, hbox(14, self.add_amount, self.add_unit, center=False))
        self.add_unit.set_valign(Gtk.Align.END)
        self.add_unit.set_margin_bottom(14)
        info_col.set_valign(Gtk.Align.CENTER)
        info_col.set_hexpand(True)
        stage = hbox(0, self.add_figure, self.add_muscles)
        stage.add_css_class("stage")
        top = hbox(28, stage, info_col, center=False)
        self.log_button = button("Log it", "primary huge", self.add_log)
        log = self.log_button
        log.set_halign(Gtk.Align.CENTER)
        log.set_margin_top(24)
        self.sets_keys = hint(("← →", "reps"), ("1-5", "sets"), ("Enter", "log"), ("Esc", "back"))
        self.train_keys = hint(("← →", "adjust"), ("1-3", "effort"), ("Enter", "log"), ("Esc", "back"))
        for k in (self.sets_keys, self.train_keys):
            k.set_margin_top(16)
        self.add_slider.set_margin_top(18)
        detail = vbox(0, top, self.add_slider, self.effort_box, self.sets_box, log, self.sets_keys, self.train_keys)
        detail.set_size_request(880, -1)
        detail.set_halign(Gtk.Align.CENTER)
        self.add_stack.add_named(detail, "detail")

        self.add_keys = hint(("Tab", "next tab"), ("Esc", "close"))
        self.add_keys.set_margin_top(20)
        self.add_after = label("", "rest-pill")
        self.add_after.set_halign(Gtk.Align.CENTER)
        self.add_after.set_margin_top(20)
        self.add_after.set_visible(False)
        self.pick_effort("normal")
        self.pick_day(0)
        self.add_tab("training")
        return vbox(0, head, tabs, self.add_stack, self.add_keys, self.add_after)

    def add_tab(self, key):
        self.add_kind = key
        for k, t in self.tabs.items():
            (t.add_css_class if k == key else t.remove_css_class)("on")
        self.tabs[key].get_parent().set_visible(True)
        self.add_keys.set_visible(True)
        self.add_head.set_text(self.add_question)
        self.add_stack.set_visible_child_name(key)

    def add_pick(self, kind, name):
        self.add_kind, self.add_name = kind, name
        self.add_figure.set_move(move_for(name))
        self.add_figure.accent = tint_for(name)
        self.add_muscles.show(*muscles_of(name))
        training = kind == "training"
        self.add_head.set_text(catalog.ACTIVITIES[name]["name"] if training else title_case(name))
        if training:
            self.add_adj.configure(60, 0, 240, 5, 15, 0)
            self.add_slider.step = 5
        elif kind == "stretch":
            hold = catalog.STRETCHES[name]["hold"]
            longest = max(hold * 2, self.recent.get(name, [0])[0])
            self.add_adj.configure(hold, 0, catalog.slider_top(longest), 5, 15, 0)
            self.add_slider.step = 5
        else:
            scale = catalog.info(name)["scale"]
            reps = self.recent.get(name, [round(10 * scale)])[0]
            self.add_adj.configure(reps, 0, catalog.slider_top(max(reps, round(30 * scale))), 1, 5, 0)
            self.add_slider.step = 1
        if not training:
            # Starts where you left it last time: the same reps, the same number of sets.
            last = self.recent.get(name)
            if kind == "stretch" and last:
                self.add_adj.set_value(last[0])
            self.pick_sets(self.args.sets or (last[1] if last else 1))
        else:
            self.log_button.set_label("Log it")
        self.add_slide(self.add_adj)
        self.effort_box.set_visible(training)
        self.sets_box.set_visible(not training)
        self.sets_keys.set_visible(not training)
        self.train_keys.set_visible(training)
        self.tabs["training"].get_parent().set_visible(False)
        self.add_keys.set_visible(False)
        self.add_stack.set_visible_child_name("detail")

    def add_slide(self, adj):
        v = round(adj.get_value())
        self.add_amount.set_text(str(v))
        if self.add_kind == "training":
            h, m = divmod(v, 60)
            self.add_unit.set_label("minutes" if not h else f"minutes, {h} hour{'s' if h > 1 else ''}" if not m
                                    else f"minutes, {h}h {m:02d}m")
        elif self.add_name:
            if self.add_kind == "stretch":
                sided = catalog.STRETCHES[self.add_name].get("sided")
                self.add_unit.set_label(f"seconds held{', each side' if sided else ''}")
            else:
                # The name is already the headline; the unit only says what is counted.
                x = catalog.info(self.add_name)
                word = "seconds" if x.get("timed") else "rep" if v == 1 else "reps"
                word = f"{word} {x['per']}" if x.get("per") else word
                self.add_unit.set_label(f"{word}, a set" if x.get("per") and self.sets > 1 else
                                        f"{word} a set" if self.sets > 1 else word)
        self.add_slider.queue_draw()

    def pick_effort(self, name):
        self.effort = name
        for n, b in self.effort_buttons.items():
            (b.add_css_class if n == name else b.remove_css_class)("picked")

    def pick_sets(self, n):
        self.sets = max(1, min(5, int(n)))
        for k, b in self.sets_buttons.items():
            (b.add_css_class if k == self.sets else b.remove_css_class)("on")
        self.log_button.set_label(f"Log {self.sets} sets" if self.sets > 1 else "Log it")
        self.add_slide(self.add_adj)

    def pick_day(self, d):
        self.days_ago = d
        for n, b in self.day_buttons.items():
            (b.add_css_class if n == d else b.remove_css_class)("on")

    def add_log(self):
        v = round(self.add_adj.get_value())
        if v <= 0 or not self.add_name or self.closing:
            return
        if self.add_kind == "training":
            out = {"result": "session", "activity": self.add_name, "minutes": v, "effort": self.effort,
                   "days_ago": self.days_ago}
            what = f"{catalog.describe(out)}{', yesterday' if self.days_ago else ''}"
        else:
            out = {"result": "manual", "did": v, "exercise": self.add_name, "sets": self.sets}
            what = (f"{self.sets} sets of " if self.sets > 1 else "") + catalog.describe_set(self.add_name, v)
        self.closing = True
        self.add_head.set_text("Logged")
        self.add_after.set_label(f"{what[0].upper()}{what[1:]}. Good.")
        self.add_after.set_visible(True)
        self.pending = json.dumps(out)
        self.key_released = False
        GLib.timeout_add(1400, lambda: self.finish(self.pending))

    def add_key(self, name):
        detail = self.add_stack.get_visible_child_name() == "detail"
        if name == "Escape":
            if detail:
                self.add_tab(self.add_kind)
            else:
                self.finish(json.dumps({"result": "closed"}))
        elif name in ("Tab", "ISO_Left_Tab") and not detail:
            order = ["training", "home", "stretch"]
            step = -1 if name == "ISO_Left_Tab" else 1
            self.add_tab(order[(order.index(self.add_kind) + step) % len(order)])
        elif detail:
            step = {"Left": -1, "Right": 1, "Down": -3, "Up": 3, "Page_Down": -3, "Page_Up": 3}.get(name)
            if step:
                self.add_adj.set_value(self.add_adj.get_value() + step * self.add_slider.step)
            elif name in ("Return", "KP_Enter"):
                self.add_log()
            elif name in ("1", "2", "3") and self.add_kind == "training":
                self.pick_effort(("easy", "normal", "hard")[int(name) - 1])
            elif name in ("1", "2", "3", "4", "5") and self.add_kind != "training":
                self.pick_sets(int(name))
        return True


def show(args):
    return Card(args).run()
