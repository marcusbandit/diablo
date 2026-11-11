"""The first-run walkthrough: what room does, the bridge, the lamps, scenes on the real lamps, night.

Every step can be skipped. The last page saves the choices and starts the service.
"""

import math
import os
import subprocess
import threading
import time

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango, PangoCairo  # noqa: E402

from . import cli, color, config, hue, lamps  # noqa: E402

APP_ID = "com.marcusrosado.room"
PIXEL = "Monocraft"  # banditshell's face; crisp only at multiples of 9 px
SANS = "Adwaita Sans"
S = {  # banditshell slate
    "ink": "#08090b", "bg1": "#0f1114", "bg2": "#181b1f", "bg3": "#1d2126", "bg4": "#262b31",
    "bg5": "#3a4149", "fg6": "#545d67", "fg7": "#78838f", "fg8": "#a5aeb8", "fg9": "#d0d7dd",
    "paper": "#eef2f5", "dim": "#5b8fb0", "mid": "#7fb3d4", "bright": "#b8dcf0", "ok": "#7ee0a0",
    "brass": "#d8c48c", "error": "#f29ba8",
}
STILL = bool(os.environ.get("ROOM_STILL"))  # screenshots: no cycling, no fades

TOUR = [  # page 1 cycles through these: what you are doing, what the room does
    ("focus", "Coding in T3 Code", "bright and cool, 4300 K"),
    ("watching", "YouTube in a window", "down to 40 %"),
    ("cinema", "Anime fullscreen", "ceiling off, a low amber glow"),
    ("intermission", "You paused it", "the house lights come up a little"),
    ("call", "A Discord call", "an even light on your face"),
    ("game", "A game fullscreen", "dim and warm"),
]
WHEN = {
    "focus": "T3 Code, a terminal, an editor, GitHub or docs",
    "everyday": "anything else, Discord without a call",
    "watching": "a video playing in a window",
    "cinema": "fullscreen video playing",
    "intermission": "fullscreen video paused",
    "game": "a fullscreen game",
    "call": "a Discord call",
}
DEMO_LAMPS = [
    lamps.Lamp("demo-p", "Ceiling panel", "ceiling_square", True, 153, 500, 0.1),
    lamps.Lamp("demo-c", "Desk lamp", "flexible_lamp", True, 153, 500, 0.2),
    lamps.Lamp("demo-f", "Corner bulb", "vintage_bulb", False, 222, 454, 2.0),
]


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def pointer(widget):
    widget.set_cursor(Gdk.Cursor.new_from_name("pointer"))
    return widget


def css():
    s = S
    return f"""
* {{ font-family: "{SANS}", "Noto Sans", sans-serif; }}
window.room {{ background-color: {s['bg1']}; }}
.page {{ padding: 36px 56px 12px 56px; }}
.lead {{ color: alpha({s['paper']}, 0.92); font-size: 18px; }}
.body {{ color: alpha({s['paper']}, 0.62); font-size: 16px; }}
.small {{ color: alpha({s['paper']}, 0.5); font-size: 14px; }}
.caption {{ color: alpha({s['paper']}, 0.92); font-size: 18px; font-weight: 600; }}
.card {{
    background-color: {s['bg3']}; border: 1px solid {s['bg4']}; border-radius: 15px; padding: 14px 18px;
}}
.card.hero {{ padding: 0; background-color: {s['ink']}; border-color: {s['bg4']}; }}
button.card {{ background-image: none; box-shadow: none; transition: background-color 120ms, border-color 120ms; }}
button.card:hover {{ background-color: {s['bg4']}; border-color: {s['bg5']}; }}
button.card:active {{ background-color: mix({s['bg4']}, {s['mid']}, 0.12); }}
button.card.live {{ border-color: {s['mid']}; background-color: mix({s['bg3']}, {s['mid']}, 0.10); }}
button.card:disabled {{ opacity: 0.45; }}
button.primary {{
    background-image: none; background-color: {s['mid']}; color: {s['ink']}; border: none;
    border-radius: 12px; padding: 10px 26px; font-size: 16px; font-weight: 700; box-shadow: none;
}}
button.primary:hover {{ background-color: mix({s['mid']}, white, 0.18); }}
button.primary:active {{ background-color: {s['dim']}; }}
button.ghost {{
    background-image: none; background-color: transparent; color: alpha({s['paper']}, 0.78);
    border: 1px solid {s['bg5']}; border-radius: 12px; padding: 9px 20px; font-size: 16px; box-shadow: none;
}}
button.ghost:hover {{ background-color: {s['bg4']}; color: {s['paper']}; }}
button.ghost.small {{ padding: 5px 14px; font-size: 14px; }}
.dot {{ min-width: 8px; min-height: 8px; border-radius: 99px; background-color: {s['bg5']}; }}
.dot.on {{ background-color: {s['mid']}; min-width: 22px; }}
.ok {{ color: {s['ok']}; }}
.warn {{ color: {s['brass']}; }}
.err {{ color: {s['error']}; }}
.mark {{ font-size: 16px; font-weight: 800; min-width: 22px; }}
switch {{ background-color: {s['bg5']}; border: none; border-radius: 99px; }}
switch:checked {{ background-color: {s['mid']}; }}
switch slider {{ background-color: {s['paper']}; border-radius: 99px; min-width: 22px; min-height: 22px; }}
dropdown > button {{
    background-image: none; background-color: {s['bg4']}; color: {s['paper']}; border: 1px solid {s['bg5']};
    border-radius: 10px; box-shadow: none; padding: 4px 10px; font-size: 16px;
}}
popover contents {{ background-color: {s['bg3']}; color: {s['paper']}; border-radius: 12px; }}
.swatch {{ border-radius: 99px; min-width: 14px; min-height: 14px; }}
"""


class PixelText(Gtk.DrawingArea):
    """Monocraft without antialiasing, so each font pixel is a whole block (a Label smears it)."""

    def __init__(self, text, size, color_hex, align=Pango.Alignment.LEFT, spacing=0):
        super().__init__()
        assert size % 9 == 0, "Monocraft is only crisp at multiples of 9px"
        self.size, self.color, self.align, self.spacing = size, color_hex, align, spacing
        self.set_halign(Gtk.Align.START if align == Pango.Alignment.LEFT else Gtk.Align.CENTER)
        self.set_draw_func(self.draw)
        self.set_text(text)

    def layout(self, layout):
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
        layout.set_alignment(self.align)
        layout.set_text(self.text, -1)
        return layout

    def set_text(self, text):
        self.text = text
        w, h = self.layout(self.create_pango_layout(None)).get_pixel_size()
        self.set_content_width(w)
        self.set_content_height(h)
        self.queue_draw()

    def draw(self, _area, cr, width, height):
        layout = self.layout(PangoCairo.create_layout(cr))
        w, h = layout.get_pixel_size()
        x = 0 if self.align == Pango.Alignment.LEFT else (width - w) // 2
        cr.set_source_rgb(*rgb(self.color))
        cr.move_to(x, (height - h) // 2)
        PangoCairo.show_layout(cr, layout)


# The pixel room

W, H = 160, 90
SPOTS = {  # where a lamp sits in the drawing: bulb x, y, glow radius
    "ceiling": (80, 6, 74),
    "desk": (133, 47, 44),
    "corner": (17, 31, 52),
    "floor": (150, 38, 46),
}
ARCH = {
    "ceiling": ("ceiling_", "pendant_", "recessed_", "spot_", "single_spot"),
    "desk": ("flexible_lamp", "table_", "desk_", "candle", "flood_"),
    "corner": ("vintage_bulb", "classic_bulb", "sultan_bulb", "edison", "filament"),
}


def place(lamp_list):
    """Give each lamp a spot in the drawing from its Hue archetype."""
    out, free = {}, list(SPOTS)
    for lamp in lamp_list:
        spot = next((k for k, prefixes in ARCH.items() if k in free and lamp.archetype.startswith(prefixes)), None)
        spot = spot or next((k for k in ("corner", "floor", "desk", "ceiling") if k in free), None)
        if spot:
            out[lamp.id] = spot
            free.remove(spot)
    return out


def glow_of(target):
    """(rgb, level) for drawing a lamp target."""
    if not target or not target.on:
        return ((0, 0, 0), 0.0)
    level = max(0.0, min(1.0, target.bri / 100))
    if target.xy:
        return (color.xy_to_rgb(target.xy), level)
    if target.mirek:
        return (color.white_rgb(color.kelvin(target.mirek)), level)
    return ((1.0, 0.86, 0.68), level)


def lerp(a, b, t):
    return a + (b - a) * t


class RoomView(Gtk.DrawingArea):
    """Marcus's room in pixels: ultrawide on the desk, a ceiling panel, a desk lamp, a corner bulb.
    Each lamp glows in its scene colour; changes fade like the real lamps do."""

    def __init__(self, scale_min=3):
        super().__init__()
        self.scale_min = scale_min
        self.set_draw_func(self.draw)
        self.spots = {}
        self.glows = {}  # spot -> (rgb, level) now
        self.start = {}
        self.goal = {}
        self.screen = "everyday"
        self.next_screen = None
        self.t0 = 0.0
        self.fade_s = 0.0 if STILL else 0.9
        self.night = True
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        self.frame = 0

    def show(self, lamp_list, scene_targets, screen, animate=True):
        self.spots = place(lamp_list)
        goal = {spot: glow_of(scene_targets.get(lid)) for lid, spot in self.spots.items()}
        if not animate or not self.glows or self.fade_s == 0:
            self.glows, self.goal, self.screen, self.next_screen = goal, goal, screen, None
            self.queue_draw()
            return
        self.start, self.goal = dict(self.glows), goal
        self.next_screen = screen
        self.t0 = time.monotonic()
        self.add_tick_callback(self.tick)

    def tick(self, _widget, _clock):
        t = min(1.0, (time.monotonic() - self.t0) / self.fade_s)
        e = t * t * (3 - 2 * t)
        out = {}
        for spot, (grgb, glevel) in self.goal.items():
            srgb, slevel = self.start.get(spot, ((0, 0, 0), 0.0))
            if slevel == 0:
                srgb = grgb
            if glevel == 0:
                grgb = srgb
            out[spot] = (tuple(lerp(a, b, e) for a, b in zip(srgb, grgb)), lerp(slevel, glevel, e))
        self.glows = out
        if self.next_screen and t >= 0.5:
            self.screen, self.next_screen = self.next_screen, None
        self.queue_draw()
        return t < 1.0

    # drawing helpers on the small surface

    @staticmethod
    def rect(cr, x, y, w, h, c, a=1.0):
        cr.set_source_rgba(*c, a)
        cr.rectangle(x, y, w, h)
        cr.fill()

    def ambient(self):
        r = g = b = 0.0
        for c, level in self.glows.values():
            r, g, b = r + c[0] * level, g + c[1] * level, b + c[2] * level
        return (r, g, b)

    def lit(self, base, amount=0.22):
        """A surface colour lifted by the room's light."""
        r, g, b = self.ambient()
        sr, sg, sb = self.screen_light()
        return (min(1, base[0] + amount * (r * 0.4 + sr * 0.5)), min(1, base[1] + amount * (g * 0.4 + sg * 0.5)),
                min(1, base[2] + amount * (b * 0.4 + sb * 0.5)))

    def screen_light(self):
        return {
            "focus": (0.10, 0.12, 0.16), "everyday": (0.22, 0.24, 0.27), "watching": (0.25, 0.22, 0.22),
            "cinema": (0.55, 0.35, 0.30), "intermission": (0.22, 0.16, 0.15), "game": (0.30, 0.45, 0.60),
            "call": (0.10, 0.11, 0.14), "away": (0, 0, 0),
        }.get(self.screen, (0.2, 0.2, 0.2))

    def draw_screen(self, cr, x, y, w, h):
        R = self.rect
        s = self.screen
        if s == "away":
            R(cr, x, y, w, h, (0.02, 0.02, 0.03))
            return
        if s == "focus":
            R(cr, x, y, w, h, rgb("#0f1114"))
            R(cr, x, y, 7, h, rgb("#181b1f"))
            colours = [rgb(S["mid"]), rgb(S["brass"]), rgb(S["error"]), rgb(S["ok"]), rgb(S["fg8"])]
            widths = [18, 26, 12, 30, 22, 8, 34, 16, 24, 10, 28, 20]
            for i in range(min(8, (h - 2) // 2)):
                indent = (i % 4) * 3
                R(cr, x + 9 + indent, y + 2 + i * 2, widths[i % len(widths)] * 0.9, 1, colours[i % len(colours)], 0.85)
                R(cr, x + 2, y + 2 + i * 2, 3, 1, rgb(S["fg6"]), 0.8)
            R(cr, x + w - 18, y + 1, 17, h - 2, rgb("#181b1f"))
            for i in range(5):
                R(cr, x + w - 16, y + 3 + i * 3, 12 - (i % 3) * 3, 1, rgb(S["fg7"]), 0.8)
            return
        if s in ("cinema", "intermission", "watching"):
            if s == "watching":
                R(cr, x, y, w, h, rgb("#181b1f"))
                vx, vy, vw, vh = x + 3, y + 2, 34, h - 4
                for i in range(6):
                    R(cr, x + 41, y + 2 + i * 2.6, 26, 1.6, rgb(S["fg6"]), 0.8)
            else:
                vx, vy, vw, vh = x, y, w, h
            # an anime sunset: sky bands, sun, hills, two figures
            bands = ["#3b2a5c", "#7a3d6b", "#d0607a", "#f2955e", "#ffc26b"]
            for i, c in enumerate(bands):
                R(cr, vx, vy + i * vh * 0.13, vw, vh * 0.14, rgb(c))
            R(cr, vx + vw * 0.62, vy + vh * 0.42, vw * 0.1, vh * 0.16, rgb("#fff1c4"))
            R(cr, vx, vy + vh * 0.66, vw, vh * 0.34, rgb("#2a1d3a"))
            R(cr, vx + vw * 0.15, vy + vh * 0.56, vw * 0.3, vh * 0.12, rgb("#2a1d3a"))
            R(cr, vx + vw * 0.36, vy + vh * 0.48, max(1, vw * 0.025), vh * 0.2, rgb("#120c1a"))
            R(cr, vx + vw * 0.41, vy + vh * 0.5, max(1, vw * 0.025), vh * 0.18, rgb("#120c1a"))
            if s == "intermission":
                R(cr, vx, vy, vw, vh, (0, 0, 0), 0.5)
                R(cr, vx + vw / 2 - 3, vy + vh / 2 - 4, 2, 8, (1, 1, 1))
                R(cr, vx + vw / 2 + 1, vy + vh / 2 - 4, 2, 8, (1, 1, 1))
                R(cr, vx + 2, vy + vh - 2, vw - 4, 1, (1, 1, 1), 0.3)
                R(cr, vx + 2, vy + vh - 2, (vw - 4) * 0.4, 1, rgb("#ff4545"))
            return
        if s == "game":
            R(cr, x, y, w, h, rgb("#5aa7e0"))
            R(cr, x, y + h - 4, w, 4, rgb("#3f8f3f"))
            R(cr, x, y + h - 2, w, 2, rgb("#7a5230"))
            for i in range(4):
                R(cr, x + 10 + i * 15, y + h - 8 - (i % 2) * 3, 6, 2, rgb("#c98a4a"))
            R(cr, x + 22, y + h - 8, 3, 4, rgb("#ff4545"))
            for i in range(3):
                R(cr, x + 44 + i * 5, y + 5, 2, 2, rgb("#ffd34a"))
            return
        if s == "call":
            R(cr, x, y, w, h, rgb("#1e1f24"))
            for i, c in enumerate(("#7fb3d4", "#d8c48c", "#f29ba8")):
                cx = x + 14 + i * 21
                R(cr, cx - 1, y + 4, 12, 10, rgb(S["ok"]) if i == 1 else rgb("#1e1f24"))
                R(cr, cx, y + 5, 10, 8, rgb(c))
            return
        # everyday: a browser with tiles
        R(cr, x, y, w, h, rgb("#1d2126"))
        R(cr, x, y, w, 3, rgb("#262b31"))
        for i in range(4):
            R(cr, x + 3 + i * 17, y + 6, 14, 9, rgb(["#3a4149", "#545d67", "#5b8fb0", "#3a4149"][i]))

    def paint(self):
        cr = cairo.Context(self.surface)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        night = self.night
        R = self.rect
        # wall and floor, lifted by the light in the room
        R(cr, 0, 0, W, H, self.lit((0.055, 0.06, 0.07), 0.3))
        R(cr, 0, 66, W, H - 66, self.lit((0.035, 0.037, 0.045), 0.2))
        cr.set_operator(cairo.OPERATOR_OVER)
        R(cr, 0, 66, W, 1, self.lit((0.09, 0.1, 0.11), 0.3))
        # a window: night sky with a moon, or a grey day
        R(cr, 30, 12, 22, 20, self.lit((0.12, 0.13, 0.15), 0.3))
        R(cr, 31, 13, 20, 18, (0.05, 0.07, 0.14) if night else (0.55, 0.62, 0.7))
        if night:
            R(cr, 45, 15, 3, 3, (0.95, 0.93, 0.8))
            for sx, sy in ((34, 16), (39, 25), (49, 27), (36, 21)):
                R(cr, sx, sy, 1, 1, (0.8, 0.85, 1.0), 0.8)
        R(cr, 40.5, 13, 1, 18, self.lit((0.12, 0.13, 0.15), 0.3))
        # light: each lamp's glow, added
        cr.set_operator(cairo.OPERATOR_ADD)
        for spot, (c, level) in self.glows.items():
            if level <= 0:
                continue
            gx, gy, radius = SPOTS[spot]
            strength = 0.25 + 0.75 * level ** 0.6
            cr.save()
            if spot == "ceiling":
                cr.translate(gx, gy)
                cr.scale(1.3, 1.0)
                gx, gy = 0, 0
            pat = cairo.RadialGradient(gx, gy, 1, gx, gy, radius * (0.55 + 0.45 * level ** 0.5))
            pat.add_color_stop_rgba(0, *c, 0.55 * strength)
            pat.add_color_stop_rgba(0.45, *c, 0.18 * strength)
            pat.add_color_stop_rgba(1, *c, 0)
            cr.set_source(pat)
            cr.paint()
            cr.restore()
        # the monitor's own light on the desk and wall
        sr, sg, sb = self.screen_light()
        pat = cairo.RadialGradient(80, 46, 2, 80, 46, 46)
        pat.add_color_stop_rgba(0, sr, sg, sb, 0.5)
        pat.add_color_stop_rgba(1, sr, sg, sb, 0)
        cr.set_source(pat)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        # furniture
        R(cr, 22, 59, 120, 3, self.lit((0.13, 0.11, 0.1), 0.35))
        R(cr, 26, 62, 2, 18, self.lit((0.08, 0.07, 0.07), 0.25))
        R(cr, 136, 62, 2, 18, self.lit((0.08, 0.07, 0.07), 0.25))
        R(cr, 77, 55, 6, 4, (0.05, 0.05, 0.06))
        R(cr, 72, 58, 16, 1, (0.07, 0.07, 0.08))
        R(cr, 43, 35, 74, 21, (0.03, 0.03, 0.035))
        self.draw_screen(cr, 44, 36, 72, 19)
        # an office chair from behind: rounded back with a lit rim, the lift, a star base
        chair = (0.035, 0.037, 0.045)
        rim = self.lit((0.07, 0.075, 0.09), 0.5)
        R(cr, 71, 62, 18, 11, chair)
        R(cr, 72, 61, 16, 1, rim)
        R(cr, 70, 63, 1, 9, rim)
        R(cr, 89, 63, 1, 9, rim)
        R(cr, 73, 73, 14, 2, (0.05, 0.05, 0.06))
        R(cr, 79, 75, 2, 6, (0.06, 0.06, 0.07))
        R(cr, 72, 81, 16, 1, (0.06, 0.06, 0.07))
        R(cr, 72, 82, 2, 1, rim)
        R(cr, 86, 82, 2, 1, rim)
        # the lamps themselves
        for spot, (c, level) in self.glows.items():
            gx, gy, _ = SPOTS[spot]
            body = tuple(min(1.0, 0.22 + v * (0.62 + 0.38 * level ** 0.3)) for v in c) if level > 0 else (0.16, 0.17, 0.19)
            if spot == "ceiling":
                R(cr, gx - 18, 2, 36, 1, (0.1, 0.1, 0.11))
                R(cr, gx - 17, 3, 34, 2, body)
            elif spot == "desk":
                R(cr, gx - 1, gy + 3, 2, 9, self.lit((0.32, 0.22, 0.14), 0.3))
                R(cr, gx - 3, gy + 11, 6, 1, self.lit((0.32, 0.22, 0.14), 0.3))
                R(cr, gx - 3, gy - 2, 6, 5, body)
                R(cr, gx - 2, gy - 3, 4, 1, body)
            elif spot == "corner":
                R(cr, gx, 0, 1, gy - 3, (0.1, 0.1, 0.11))
                R(cr, gx - 1, gy - 3, 3, 2, (0.15, 0.13, 0.1))
                R(cr, gx - 2, gy - 1, 5, 5, body)
                R(cr, gx - 1, gy + 4, 3, 1, body)
            else:
                R(cr, gx, gy + 4, 1, 62 - gy + 18, (0.12, 0.12, 0.13))
                R(cr, gx - 4, gy - 2, 9, 6, body)

    def draw(self, _area, cr, width, height):
        self.paint()
        scale = max(self.scale_min, min(width // W, height // H)) if width >= W and height >= H else 1
        dw, dh = W * scale, H * scale
        ox, oy = (width - dw) // 2, (height - dh) // 2
        radius = 15
        cr.new_sub_path()
        cr.arc(ox + dw - radius, oy + radius, radius, -math.pi / 2, 0)
        cr.arc(ox + dw - radius, oy + dh - radius, radius, 0, math.pi / 2)
        cr.arc(ox + radius, oy + dh - radius, radius, math.pi / 2, math.pi)
        cr.arc(ox + radius, oy + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.clip()
        cr.translate(ox, oy)
        cr.scale(scale, scale)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()


# The window

def label(text, *classes, wrap=True, xalign=0.0, selectable=False):
    lab = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in classes:
        lab.add_css_class(c)
    return lab


def vbox(spacing=12, *children):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for c in children:
        box.append(c)
    return box


def hbox(spacing=12, *children):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    for c in children:
        box.append(c)
    return box


def swatch(hex_color):
    area = Gtk.DrawingArea()
    area.set_content_width(14)
    area.set_content_height(14)

    def draw(_a, cr, w, h):
        cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb(hex_color))
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1)
        cr.stroke()

    area.set_draw_func(draw)
    area.set_valign(Gtk.Align.CENTER)
    return area


def in_thread(fn, done=None):
    def work():
        try:
            result, error = fn(), None
        except Exception as exc:  # shown on the page, never a crash
            result, error = None, exc
        if os.environ.get("ROOM_DEBUG"):
            print(f"room setup: {getattr(fn, '__name__', fn)} -> {error or 'ok'}", flush=True)
        if done:
            GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=work, daemon=True).start()


class Setup(Gtk.ApplicationWindow):
    PAGES = ("welcome", "bridge", "lamps", "try", "night", "ready")

    def __init__(self, app, page=1):
        super().__init__(application=app, title="room setup")
        self.add_css_class("room")
        self.set_default_size(1120, 800)
        self.cfg = config.load()
        self.bridge = None
        self.all_lamps = []
        self.rooms = {}
        self.raw = {}
        self.chosen = {}  # lamp id -> bool
        self.default_ids = set()  # the lamps an empty `lamps` list means
        self.bridge_state = "checking"
        self.bridge_note = ""
        self.playing = None
        self.started = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=220)
        self.stack.set_vexpand(True)
        self.builders = {
            "welcome": self.page_welcome, "bridge": self.page_bridge, "lamps": self.page_lamps,
            "try": self.page_try, "night": self.page_night, "ready": self.page_ready,
        }
        self.holders = {}
        for name in self.PAGES:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            holder.add_css_class("page")
            self.holders[name] = holder
            self.stack.add_named(holder, name)

        self.back = pointer(Gtk.Button(label="Back"))
        self.back.add_css_class("ghost")
        self.back.connect("clicked", lambda *_: self.go(-1))
        self.next = pointer(Gtk.Button(label="Next"))
        self.next.add_css_class("primary")
        self.next.connect("clicked", lambda *_: self.go(1))
        self.dots = hbox(6)
        self.dots.set_halign(Gtk.Align.CENTER)
        self.dots.set_valign(Gtk.Align.CENTER)
        self.dots.set_hexpand(True)
        footer = hbox(12, self.back, self.dots, self.next)
        footer.set_margin_start(56)
        footer.set_margin_end(56)
        footer.set_margin_bottom(28)
        footer.set_margin_top(8)
        self.set_child(vbox(0, self.stack, footer))

        self.index = max(0, min(len(self.PAGES) - 1, page - 1))
        shot = os.environ.get("ROOM_SHOT_SCENE")  # screenshots pick the scene on show
        self.tour_i = next((i for i, t in enumerate(TOUR) if t[0] == shot), 0)
        self.load_bridge()
        self.render()
        if not STILL:
            GLib.timeout_add(2600, self.tour_step)

    # data

    def load_bridge(self):
        host, key = config.bridge_credentials()
        if not host:
            self.bridge_state = "unpaired"
            self.shot_ready()
            return
        bridge = hue.Bridge(host, key)

        def work():
            raw = bridge.lights()
            rooms = bridge.rooms()
            return raw, rooms, bridge.bridge_name()

        def done(result, error):
            if error:
                self.bridge_state = "rejected" if "refused" in str(error) else "unreachable"
                self.bridge_note = str(error)
            else:
                raw, rooms, name = result
                self.bridge = bridge
                self.bridge_name = name
                self.raw = {d["id"]: d for d in raw}
                self.all_lamps, chosen = lamps.choose(raw, rooms, self.cfg)
                self.rooms = {lid: rname for rname, ids in rooms for lid in ids}
                # What `lamps = []` would mean, so saving can tell "the default" from a hand-picked set.
                _, default = lamps.choose(raw, rooms, config.merge(self.cfg, {"lamps": []}))
                self.default_ids = {lamp.id for lamp in default}
                picked = {lamp.id for lamp in chosen}
                self.chosen = {lamp.id: lamp.id in picked for lamp in self.all_lamps}
                self.bridge_state = "ok"
            self.render(keep_page=True)
            self.shot_ready()

        in_thread(work, done)

    def shot_ready(self):
        """Screenshots (scripts/shot.sh): once the bridge has answered, render the window's own
        widgets to ROOM_SHOT_PNG and quit. No browser, so no stale Broadway frame."""
        path = os.environ.get("ROOM_SHOT_PNG")
        if path:
            GLib.timeout_add(900, self.capture, path)

    def capture(self, path):
        child = self.get_child()
        w, h = child.get_width(), child.get_height()
        page = self.holders[self.PAGES[self.index]].get_first_child()
        if not w or not h or page is None or not page.get_width():
            return True  # not laid out yet: Broadway only ticks once the viewer has connected
        if not getattr(self, "settled", False):
            self.settled = True  # one more frame after layout, so drawing areas have drawn
            return True
        snap = Gtk.Snapshot()
        bg = Gdk.RGBA()
        bg.parse(S["bg1"])
        snap.append_color(bg, Graphene.Rect().init(0, 0, w, h))
        Gtk.WidgetPaintable.new(child).snapshot(snap, w, h)
        node = snap.to_node()
        texture = self.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(path)
        self.get_application().quit()
        return False

    def lamp_list(self):
        chosen = [lamp for lamp in self.all_lamps if self.chosen.get(lamp.id)]
        return chosen or DEMO_LAMPS

    def scene_targets(self, scene, lamp_list=None, factor=0.0):
        return lamps.targets(scene, self.cfg, lamp_list or self.lamp_list(), factor)

    # navigation

    def go(self, step):
        if self.PAGES[self.index] == "ready" and step > 0:
            self.close()
            return
        self.index = max(0, min(len(self.PAGES) - 1, self.index + step))
        self.render()

    def render(self, keep_page=False):
        name = self.PAGES[self.index]
        for pname in self.PAGES if keep_page else (name,):
            holder = self.holders[pname]
            while (child := holder.get_first_child()) is not None:
                holder.remove(child)
            holder.append(self.builders[pname]())
        self.stack.set_visible_child_name(name)
        self.back.set_visible(self.index > 0)
        nxt = {"welcome": "Show me", "bridge": "Next" if self.bridge_state == "ok" else "Skip for now",
               "ready": "Close" if self.started else "Skip, start later"}.get(name, "Next")
        self.next.set_label(nxt)
        if name == "ready" and not self.started:
            self.next.remove_css_class("primary")
            self.next.add_css_class("ghost")
        else:
            self.next.remove_css_class("ghost")
            self.next.add_css_class("primary")
        while (dot := self.dots.get_first_child()) is not None:
            self.dots.remove(dot)
        for i in range(len(self.PAGES)):
            d = Gtk.Box()
            d.add_css_class("dot")
            if i == self.index:
                d.add_css_class("on")
            d.set_valign(Gtk.Align.CENTER)
            self.dots.append(d)

    def head(self, eyebrow, title):
        return vbox(14, PixelText(eyebrow, 18, S["mid"], spacing=2), PixelText(title, 36, S["paper"]))

    # pages

    def page_welcome(self):
        scene, doing, does = TOUR[self.tour_i % len(TOUR)]
        self.hero = RoomView()
        self.hero.set_size_request(800, 450)
        self.hero.set_hexpand(True)
        self.hero.show(self.lamp_list(), self.scene_targets(scene), scene, animate=False)
        hero_card = vbox(0, self.hero)
        hero_card.add_css_class("card")
        hero_card.add_css_class("hero")
        hero_card.set_halign(Gtk.Align.CENTER)
        self.caption = label(f"{doing}: {does}", "caption", xalign=0.5)
        self.caption.set_halign(Gtk.Align.CENTER)
        text = vbox(
            18,
            self.head("ROOM", "The lights follow you"),
            label("room watches what's on your screen and sets your Hue lamps to match. "
                  "You never pick a scene.", "lead"),
        )
        page = vbox(22, text, hero_card, self.caption)
        return page

    def tour_step(self):
        if self.PAGES[self.index] != "welcome" or not hasattr(self, "hero"):
            return True
        self.tour_i += 1
        scene, doing, does = TOUR[self.tour_i % len(TOUR)]
        self.hero.show(self.lamp_list(), self.scene_targets(scene), scene)
        self.caption.set_label(f"{doing}: {does}")
        return True

    def check_row(self, ok, text, detail=""):
        mark = label("✓" if ok is True else "!" if ok is False else "…", "mark",
                     "ok" if ok is True else "err" if ok is False else "small", wrap=False)
        mark.set_valign(Gtk.Align.START)
        body = vbox(2, label(text, "lead"))
        if detail:
            body.append(label(detail, "small"))
        row = hbox(12, mark, body)
        row.add_css_class("card")
        return row

    def page_bridge(self):
        page = vbox(20, self.head("STEP 1 OF 4", "Your bridge"))
        state = self.bridge_state
        if state == "checking":
            page.append(label("Looking for your Hue bridge...", "body"))
            return page
        if state == "ok":
            host, _ = config.bridge_credentials()
            page.append(label("room found everything it needs. Nothing to do here.", "body"))
            page.append(self.check_row(True, f"{self.bridge_name} answers at {host}"))
            page.append(self.check_row(True, f"room's key works: it can read {len(self.all_lamps)} lamps"))
            by_room = {}
            for lamp in self.all_lamps:
                by_room.setdefault(self.rooms.get(lamp.id, "No room"), []).append(lamp.name)
            for rname, names in by_room.items():
                page.append(self.check_row(True, rname, ", ".join(names)))
            return page
        if state in ("unreachable",):
            page.append(label("room has a bridge address but the bridge didn't answer. Is it plugged in, "
                              "and is this computer on the same network?", "body"))
            page.append(self.check_row(False, "No answer from the bridge", self.bridge_note))
            again = pointer(Gtk.Button(label="Try again"))
            again.add_css_class("ghost")
            again.set_halign(Gtk.Align.START)
            again.connect("clicked", lambda *_: (setattr(self, "bridge_state", "checking"), self.render(), self.load_bridge()))
            page.append(again)
            return page
        # unpaired or rejected: walk the link button
        page.append(label("room needs its own key from the bridge. The bridge hands one out for 30 seconds "
                          "after you press the round button on top of it.", "body"))
        steps = vbox(10,
                     self.check_row(None, "1. Walk over to the Hue bridge", "the white square box by the router"),
                     self.check_row(None, "2. Press the big round button on top", "once is enough"),
                     self.check_row(None, "3. Come back and press Link", "room keeps asking for 30 seconds"))
        page.append(steps)
        self.link_note = label(self.bridge_note if state == "rejected" else "", "small")
        link = pointer(Gtk.Button(label="Link"))
        link.add_css_class("primary")
        link.set_halign(Gtk.Align.START)
        link.connect("clicked", self.on_link)
        page.append(hbox(14, link, self.link_note))
        return page

    def on_link(self, button):
        button.set_sensitive(False)
        self.link_note.set_label("Looking for the bridge...")

        def work():
            hosts = [config.bridge_credentials()[0]] if config.bridge_credentials()[0] else hue.discover()
            hosts = [h for h in hosts if h]
            if not hosts:
                raise hue.BridgeError("no bridge answered on this network")
            GLib.idle_add(self.link_note.set_label, f"Found one at {hosts[0]}. Press its button now...")
            key = hue.wait_for(lambda: hue.pair(hosts[0]), 30)
            config.save_bridge(hosts[0], key)
            return hosts[0]

        def done(host, error):
            button.set_sensitive(True)
            if error:
                msg = "The button wasn't pressed in time. Press it, then Link again." if isinstance(
                    error, hue.LinkButtonNotPressed) else str(error)
                self.link_note.set_label(msg)
                return
            self.bridge_state = "checking"
            self.render()
            self.load_bridge()

        in_thread(work, done)

    def page_lamps(self):
        page = vbox(18, self.head("STEP 2 OF 4", "Which lamps"))
        if not self.all_lamps:
            page.append(label("Once the bridge is linked, its lamps show up here. You can do this later "
                              "with room setup.", "body"))
            return page
        page.append(label("room only touches the lamps switched on here. Blink makes one flash so you "
                          "know which is which.", "body"))
        rows = vbox(10)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      max_content_height=430)
        scroller.set_child(rows)
        page.append(scroller)
        for lamp in self.all_lamps:
            d = self.raw.get(lamp.id, {})
            on = (d.get("on") or {}).get("on")
            now = lamps.Target(on=bool(on), bri=(d.get("dimming") or {}).get("brightness", 0),
                               mirek=(d.get("color_temperature") or {}).get("mirek"),
                               xy=tuple((d.get("color") or {}).get("xy", {}).values()) or None
                               if not (d.get("color_temperature") or {}).get("mirek_valid") else None)
            kind = "colour" if lamp.has_color else "white" if lamp.mirek_min else "dimmable"
            sub = f"{kind} lamp, {self.rooms.get(lamp.id, 'no room')}, now {'on ' + str(round(now.bri)) + ' %' if on else 'off'}"
            sw = pointer(Gtk.Switch(active=self.chosen.get(lamp.id, False)))
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", lambda s, _p, lid=lamp.id: self.chosen.__setitem__(lid, s.get_active()))
            blink = pointer(Gtk.Button(label="Blink"))
            blink.add_css_class("ghost")
            blink.add_css_class("small")
            blink.set_valign(Gtk.Align.CENTER)
            blink.connect("clicked", lambda _b, lid=lamp.id: in_thread(lambda: self.bridge.identify(lid)))
            text = vbox(2, label(lamp.name, "lead"), label(sub, "small"))
            text.set_hexpand(True)
            row = hbox(14, swatch(color.preview_hex(now)), text, blink, sw)
            row.add_css_class("card")
            rows.append(row)
        return page

    def page_try(self):
        page = vbox(18, self.head("STEP 3 OF 4", "Try a scene"))
        live = self.bridge is not None
        page.append(label("Press one and your lamps show it for six seconds, then go back the way they were."
                          if live else "With the bridge linked, these play on your lamps. For now the room "
                          "below shows them.", "body"))
        self.try_view = RoomView(scale_min=2)
        self.try_view.set_size_request(480, 270)
        first = self.playing or os.environ.get("ROOM_SHOT_SCENE") or "focus"
        self.try_view.show(self.lamp_list(), self.scene_targets(first), first, animate=False)
        view_card = vbox(0, self.try_view)
        view_card.add_css_class("card")
        view_card.add_css_class("hero")
        view_card.set_valign(Gtk.Align.START)
        self.try_note = label("", "small")
        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        grid.set_hexpand(True)
        self.try_buttons = {}
        for i, name in enumerate(self.cfg["scenes"]):
            targets = self.scene_targets(name)
            swatches = hbox(4, *[swatch(color.preview_hex(targets[lamp.id])) for lamp in self.lamp_list()])
            title = hbox(10, swatches, label(cli.LABELS.get(name, name), "lead", wrap=False))
            values = label(self.values_of(name), "small")
            values.set_opacity(0.8)
            inner = vbox(3, title, label(WHEN.get(name, ""), "small"), values)
            btn = pointer(Gtk.Button(child=inner))
            btn.add_css_class("card")
            if name == first and (self.playing or os.environ.get("ROOM_SHOT_SCENE")):
                btn.add_css_class("live")
            btn.set_hexpand(True)
            btn.connect("clicked", lambda _b, n=name: self.on_try(n))
            grid.attach(btn, i % 2, i // 2, 1, 1)
            self.try_buttons[name] = btn
        page.append(hbox(18, view_card, vbox(10, grid, self.try_note)))
        return page

    def values_of(self, name):
        sc = self.cfg["scenes"][name]
        nb = "\u00a0"  # keep a number and its unit on one line
        if sc.get("color"):
            return f"colour {sc.get('color_bri', sc['bri'])}{nb}%, white {sc['bri']}{nb}%"
        return f"{sc['bri']}{nb}%, {sc['k']}{nb}K"

    def on_try(self, name):
        self.try_view.show(self.lamp_list(), self.scene_targets(name), name)
        for n, b in self.try_buttons.items():
            (b.add_css_class if n == name else b.remove_css_class)("live")
        if self.bridge is None or self.playing:
            return
        self.playing = name
        for b in self.try_buttons.values():
            b.set_sensitive(False)
        self.try_note.set_label(f"{cli.LABELS[name]} is on your lamps now...")
        chosen = [lamp for lamp in self.all_lamps if self.chosen.get(lamp.id)]

        def done(_r, error):
            self.playing = None
            for b in self.try_buttons.values():
                b.set_sensitive(True)
            self.try_note.set_label(f"Couldn't reach the lamps: {error}" if error else "Back the way they were. Try another.")

        in_thread(lambda: cli.play_scene(self.bridge, name, self.cfg, chosen, 6, out=lambda *_: None), done)

    def night_row(self, title, key, options):
        current = self.cfg["night"][key]
        if current not in options:
            options = sorted(options + [current], key=lambda t: lamps.on_night_axis(lamps.minutes(t)))
        dd = pointer(Gtk.DropDown.new_from_strings(options))
        dd.set_selected(options.index(current))
        dd.set_valign(Gtk.Align.CENTER)

        def changed(d, _p):
            self.cfg["night"][key] = options[d.get_selected()]
            if hasattr(self, "curve"):
                self.curve.queue_draw()

        dd.connect("notify::selected", changed)
        t = label(title, "lead")
        t.set_hexpand(True)
        return hbox(12, t, dd)

    def page_night(self):
        page = vbox(16, self.head("STEP 4 OF 4", "Night and bed"))
        page.append(label("Late at night the white light warms toward candlelight and comes down, so a "
                          "2 am session doesn't feel like an office. Colours keep their colour.", "body"))
        self.curve = Gtk.DrawingArea()
        self.curve.set_content_height(92)
        self.curve.set_hexpand(True)
        self.curve.set_draw_func(self.draw_curve)
        curve_card = vbox(8, self.curve)
        curve_card.add_css_class("card")
        page.append(curve_card)
        rows = vbox(
            10,
            self.night_row("Start warming at", "start", ["21:00", "22:00", "23:00", "00:00", "01:00"]),
            self.night_row("Fully warm by", "full", ["00:00", "01:00", "02:00", "03:00"]),
            self.night_row("Day again at", "end", ["06:00", "07:00", "08:00", "09:00", "10:00", "11:00"]),
        )
        rows.add_css_class("card")
        page.append(rows)
        bed = self.cfg["bedtime"]
        sw = pointer(Gtk.Switch(active=bed["enabled"]))
        sw.set_valign(Gtk.Align.CENTER)
        sw.connect("notify::active", lambda s, _p: bed.__setitem__("enabled", s.get_active()))
        text = vbox(2, label("Fade out when I go to bed", "lead"),
                    label(f"After {bed['after']}, once the screen has been locked {bed['locked_min']} minutes, "
                          "the lamps fade out over a minute. Off unless you switch it on.", "small"))
        text.set_hexpand(True)
        bed_row = hbox(14, text, sw)
        bed_row.add_css_class("card")
        page.append(bed_row)
        return page

    def draw_curve(self, area, cr, width, height):
        """Hourly bars from 18:00 to 12:00, each the colour Focus would be at that hour."""
        hours = list(range(18, 24)) + list(range(0, 13))
        n = len(hours)
        gap = 6
        bw = (width - gap * (n - 1)) / n
        lamp = lamps.Lamp("x", "x", "", False, 153, 500)
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string(f"{SANS} 10")
        layout.set_font_description(desc)
        for i, hour in enumerate(hours):
            when = time.localtime()
            stamp = time.mktime((when.tm_year, when.tm_mon, when.tm_mday, hour, 0, 0, 0, 0, -1))
            f = lamps.night_factor(stamp, self.cfg["night"])
            t = lamps.targets("focus", self.cfg, [lamp], f)["x"]
            c, level = glow_of(t)
            bar_h = (height - 26) * (0.25 + 0.75 * level)
            x = i * (bw + gap)
            cr.set_source_rgba(*c, 0.95)
            cr.rectangle(x, height - 22 - bar_h, bw, bar_h)
            cr.fill()
            if hour % 3 == 0:
                layout.set_text(f"{hour:02d}", -1)
                cr.set_source_rgba(*rgb(S["fg7"]), 1)
                cr.move_to(x, height - 16)
                PangoCairo.show_layout(cr, layout)

    def command_row(self, cmd, what):
        t = label(what, "body")
        t.set_hexpand(True)
        code = PixelText(cmd, 18, S["bright"])
        code.set_size_request(250, -1)
        return hbox(18, code, t)

    def page_ready(self):
        page = vbox(18, self.head("READY", "Hands off when you want"))
        page.append(label(f"Change a lamp in the Hue app, on a switch or through Diablo and room leaves that "
                          f"lamp alone until you switch to something else, {self.cfg['hold']['manual_min']} "
                          "minutes at least.", "lead"))
        cmds = vbox(12,
                    self.command_row("room", "what the lights are doing, and why"),
                    self.command_row("room why", "every signal it reads right now"),
                    self.command_row("room pause 1h", "the lamps are yours for an hour"),
                    self.command_row("room try cinema", "show a scene for eight seconds"))
        cmds.add_css_class("card")
        page.append(cmds)
        page.append(label("In the banditshell launcher, type room for the same: the scene now, pause, "
                          "resume and every scene to try.", "body"))
        self.start_note = label("", "lead")
        start = pointer(Gtk.Button(label="Start room" if not self.started else "Running"))
        start.add_css_class("primary")
        start.set_sensitive(not self.started)
        start.set_halign(Gtk.Align.START)
        start.connect("clicked", self.on_start)
        page.append(hbox(16, start, self.start_note))
        return page

    def on_start(self, button):
        button.set_sensitive(False)
        self.start_note.set_label("Saving and starting...")
        chosen_ids = {lamp.id for lamp in self.all_lamps if self.chosen.get(lamp.id)}
        if self.all_lamps and not chosen_ids:
            # An empty list in the config means "every lamp", the opposite of what was asked.
            self.start_note.set_label("Switch on at least one lamp on the Lamps page, or skip and start later.")
            self.start_note.add_css_class("warn")
            button.set_sensitive(True)
            return
        chosen_names = [lamp.name for lamp in self.all_lamps if lamp.id in chosen_ids]
        choices = {
            "night.start": self.cfg["night"]["start"], "night.full": self.cfg["night"]["full"],
            "night.end": self.cfg["night"]["end"], "bedtime.enabled": self.cfg["bedtime"]["enabled"],
        }
        if self.all_lamps:
            choices["lamps"] = [] if chosen_ids == self.default_ids else chosen_names

        def work():
            config.save_choices(choices)
            config.ONBOARDED.write_text(f"{time.time():.0f}\n")
            active = subprocess.run(["systemctl", "--user", "is-active", "room.service"],
                                    capture_output=True, text=True).stdout.strip() == "active"
            verb = ["restart"] if active else ["enable", "--now"]
            subprocess.run(["systemctl", "--user", *verb, "room.service"], capture_output=True, timeout=15)
            for _ in range(16):
                time.sleep(0.5)
                s = cli.read_status()
                if s and s.get("running") and s.get("state") and s.get("pid") and s["updated"] > time.time() - 3:
                    return s
            return cli.read_status()

        def done(s, error):
            if error or not s or not s.get("running"):
                self.start_note.set_label(f"It didn't start: {error or 'no status yet'}. "
                                          "journalctl --user -u room says why.")
                self.start_note.add_css_class("err")
                button.set_sensitive(True)
                return
            self.started = True
            self.start_note.set_label(f"room is running. Right now: {cli.LABELS.get(s['state'], s['state'])}, "
                                      f"because {s.get('reason', '')}.")
            self.start_note.add_css_class("ok")
            button.set_label("Running")
            self.next.set_label("Close")
            self.next.remove_css_class("ghost")
            self.next.add_css_class("primary")

        in_thread(work, done)


class App(Gtk.Application):
    def __init__(self, page):
        super().__init__(application_id=APP_ID)
        self.page = page

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_string(css())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = self.props.active_window or Setup(self, self.page)
        win.present()


def main(page=1):
    return App(page).run([])
