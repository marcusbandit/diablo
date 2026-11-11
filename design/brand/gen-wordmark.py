#!/usr/bin/env python3
"""Build Diablo's wordmark as outlined SVG from Bodoni Moda.

Usage: gen-wordmark.py <BodoniModa[opsz,wght].ttf> <out-dir>

Bodoni Moda is OFL, from github.com/google/fonts (ofl/bodonimoda). The output has
no font dependency: every letter is a path, so the SVG renders the same everywhere.
"""
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.pointInsidePen import PointInsidePen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

WORD = "DIABLO"
TRACKING = 0.20      # letter spacing, in em
WEIGHT = 600
OPSZ = 72            # optical size the approved look was drawn at
BLUE = "#2d81ff"
INKS = {"dark": "#f2f1ee", "light": "#0b0b0c"}  # dark = for dark backgrounds


def main(font_path, out_dir):
    font = instantiateVariableFont(TTFont(font_path), {"wght": WEIGHT, "opsz": OPSZ})
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    upm = font["head"].unitsPerEm
    cap = font["OS/2"].sCapHeight
    track = TRACKING * upm

    paths, x = [], 0.0
    for i, ch in enumerate(WORD):
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        # font units are y-up; flip into SVG's y-down with the baseline at y = cap
        gs[name].draw(TransformPen(pen, (1, 0, 0, -1, x, cap)))
        paths.append(pen.getCommands())
        if ch == "A":
            a_at, a_glyph = x, gs[name]
        x += gs[name].width + (track if i < len(WORD) - 1 else 0)

    # the blue diamond sits threaded on the A's hairline crossbar, centred in the counter
    def inside(px, py):
        pen = PointInsidePen(gs, (px, py))
        a_glyph.draw(pen)
        return pen.getResult()

    def runs(py):
        out, prev, start = [], False, 0
        for px in range(0, a_glyph.width + 1, 2):
            now = inside(px, py)
            if now and not prev:
                start = px
            if not now and prev:
                out.append((start, px))
            prev = now
        return out

    # crossbar: the lowest row in the lower half where the legs join into one wide run
    bar_y = next(py for py in range(int(cap * 0.2), int(cap * 0.6), 2)
                 if len(r := runs(py)) == 1 and r[0][1] - r[0][0] > a_glyph.width * 0.4)
    # counter edges just above the bar: inner edge of the left leg, inner edge of the right stem
    legs = runs(bar_y + 40)
    cx = a_at + (legs[0][1] + legs[1][0]) / 2
    cy = cap - bar_y
    r = 0.075 * cap
    diamond = f"M{cx:.1f} {cy - r:.1f}L{cx + r:.1f} {cy:.1f}L{cx:.1f} {cy + r:.1f}L{cx - r:.1f} {cy:.1f}Z"

    width = x
    out = Path(out_dir)
    for mode, ink in INKS.items():
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {cap}" role="img" aria-label="Diablo">\n'
            f'  <path fill="{ink}" d="{" ".join(paths)}"/>\n'
            f'  <path fill="{BLUE}" d="{diamond}"/>\n'
            "</svg>\n"
        )
        (out / f"wordmark-{mode}.svg").write_text(svg)
        print(f"wrote {out / f'wordmark-{mode}.svg'}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
