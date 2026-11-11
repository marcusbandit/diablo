#!/usr/bin/env python3
"""Diablo's face sheet: one big grid to draw on, cut into one folder per mood.

    sheet.py make <guides-dir> <out-dir>
        Writes faces.ora (opens in Krita, layered) and faces-guide.png (flat, for any
        other program). 18 rows, one per mood in MOODS order, 4 columns: the mood's
        guide repeated four times, one cell per frame. Guides are <guides-dir>/<mood>.png.

    sheet.py cut <faces.ora | drawing.png> <assets-dir>
        Cuts the drawing into <assets-dir>/<mood>/<n>.png, one transparent PNG per drawn
        cell, and writes <mood>/animation.json for a mood that has none yet. From an .ora
        it takes every layer except paper, guide and grid. From a .png it takes the image
        as is, so export only your drawing layer, with transparency.

        Colour is thrown away: each frame is stored as a grey value plus alpha. The alpha
        is the mask, where ink is. The grey is shade, like Minecraft's tinted grass: the
        apps multiply it by the theme colour, so white shows the theme colour itself and
        darker strokes show it darker. Your typical solid ink becomes white (full theme
        colour), lighter ink stays at full colour too, so draw in any colour; only how light or dark it is matters.

    sheet.py mouths <mouths.ora> <assets-dir>
        Cuts the mouth sheet (8 rows, one per mouth shape in MOUTHS order, 3 takes each)
        into <assets-dir>/mouths/<shape>/<n>.png, shaded like the faces. Layer opacity is
        ignored, so a drawing layer faded down to see the guide still cuts at full strength.

    sheet.py rig <assets-dir>
        Splits the drawn moods into the parts the talking face is assembled from, by
        whole strokes, so nothing is clipped and the parts simply overlap when stacked:
        <assets-dir>/horns/<n>.png (the talking frames' horns) and
        <assets-dir>/eyes/<look>/<n>.png, the eyes and brows of the mood EYES_FROM
        names for each look. Looks drawn on their own (confused, bored, the glances)
        already live in eyes/ and are left alone.

    sheet.py preview <assets-dir> <out.png> [<hex colour>]
        A contact sheet of every cut frame tinted with a theme colour (default #2d81ff),
        on dark and light, to check the cut.

animation.json borrows from Minecraft's .png.mcmeta, with separate images instead of
one long strip:

    {"animation": {"frametime": 250, "order": "random", "frames": [1, 2, 3, 4]}}

frametime is milliseconds per frame. order "random" shuffles and never shows the same
frame twice in a row, which gives line boil; "sequence" plays frames in the listed order
and loops. frames lists image numbers; an entry may be {"index": 3, "time": 400} to hold
one frame longer. Leave frames out to use every numbered image in order.

Needs Pillow.
"""
import io
import json
import re
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

MOODS = [
    "resting", "asleep", "listening", "thinking", "talking", "working", "expectant",
    "sly", "delighted", "proud", "relieved", "smug", "wink", "surprised", "confused",
    "worried", "annoyed", "sorry",
]
COLS = 4
MOUTHS = ["rest", "open", "mid", "fv", "oo", "l", "ee", "h"]  # m b p / vowels / s t k n / f v / w r oo / l th / ee y / h
MOUTH_TAKES = 3
MOUTH_TOP = 300      # in a 512 cell a stroke whose middle sits below this line is mouth
HORNS_TOP = 160      # horns start above this line; so may a raised brow, which is thinner
# Talking eye looks cut from the drawn moods (look: mood). Sly and wink are left out on
# purpose: they only work with their own mouth.
EYES_FROM = {
    "neutral": "talking", "expectant": "expectant", "happy": "delighted", "proud": "proud",
    "wide": "listening", "surprised": "surprised", "angry": "working", "annoyed": "annoyed",
    "judging": "smug", "worried": "worried", "sorry": "sorry", "thinking": "thinking",
    "relaxed": "resting", "closed": "asleep", "relieved": "relieved",
}
CELL = 512
PAPER = (242, 241, 238, 255)
GUIDE_OPACITY = 0.30
GRID = (40, 44, 52, 90)
LABEL = (40, 44, 52, 150)
FONT = Path(__file__).resolve().parents[1] / "fonts" / "SourceSans3.ttf"
NOT_DRAWING = {"paper", "guide", "grid", "align"}
DEFAULT_ANIMATION = {"animation": {"frametime": 250, "order": "random"}}
SOLID = 200          # only near-opaque ink is measured, so soft stroke edges do not skew it
REFERENCE = 0.50     # the median solid ink value becomes full tint: the main stroke is the theme colour
SOFT = 0.08          # keeps an all-black drawing at full tint instead of dividing by zero


def _png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _ink_only(path):
    """The guide's blue ink on transparency, so it lies on the paper like tracing paper."""
    im = Image.open(path).convert("RGBA").resize((CELL, CELL))
    r, _, b, _ = im.split()
    im.putalpha(ImageChops.subtract(b, r).point(lambda v: 255 if v > 60 else 0))
    return im


def make(guides_dir, out_dir):
    guides_dir, out_dir = Path(guides_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = COLS * CELL, len(MOODS) * CELL

    paper = Image.new("RGBA", (w, h), PAPER)
    guide = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grid)
    font = ImageFont.truetype(str(FONT), 30) if FONT.exists() else ImageFont.load_default()

    for row, mood in enumerate(MOODS):
        ink = _ink_only(guides_dir / f"{mood}.png")
        for col in range(COLS):
            guide.alpha_composite(ink, (col * CELL, row * CELL))
            draw.text((col * CELL + 14, row * CELL + 8), f"{mood} {col + 1}", fill=LABEL, font=font)
    for x in range(0, w + 1, CELL):
        draw.line([(x, 0), (x, h)], fill=GRID, width=3)
    for y in range(0, h + 1, CELL):
        draw.line([(0, y), (w, y)], fill=GRID, width=3)

    faded = guide.copy()
    faded.putalpha(faded.getchannel("A").point(lambda a: int(a * GUIDE_OPACITY)))
    flat = paper.copy()
    flat.alpha_composite(faded)
    flat.alpha_composite(grid)
    flat.save(out_dir / "faces-guide.png")

    empty = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layers = [("drawing", empty, 1.0), ("grid", grid, 1.0), ("guide", guide, GUIDE_OPACITY), ("paper", paper, 1.0)]
    stack = [f'<image version="0.0.5" w="{w}" h="{h}" xres="72" yres="72">', '<stack name="faces">']
    for i, (name, _, opacity) in enumerate(layers):
        selected = ' selected="true"' if name == "drawing" else ""
        stack.append(f'<layer name="{name}" src="data/{i}.png" x="0" y="0" opacity="{opacity}" visibility="visible"{selected}/>')
    stack += ["</stack>", "</image>"]
    with zipfile.ZipFile(out_dir / "faces.ora", "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "image/openraster", compress_type=zipfile.ZIP_STORED)
        z.writestr("stack.xml", "\n".join(stack), compress_type=zipfile.ZIP_DEFLATED)
        for i, (_, im, _) in enumerate(layers):
            z.writestr(f"data/{i}.png", _png(im), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("mergedimage.png", _png(flat), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("Thumbnails/thumbnail.png", _png(flat.resize((COLS * 64, len(MOODS) * 64))), compress_type=zipfile.ZIP_DEFLATED)
    print(f"made {out_dir / 'faces.ora'} and {out_dir / 'faces-guide.png'} ({w}x{h}, {len(MOODS)} moods x {COLS} frames)")


def _drawing(path, skip=NOT_DRAWING, keep_opacity=True):
    path = Path(path)
    if path.suffix.lower() != ".ora":
        return Image.open(path).convert("RGBA")
    with zipfile.ZipFile(path) as z:
        stack = z.read("stack.xml").decode()
        image = re.search(r"<image\b[^>]*>", stack).group(0)  # Krita writes h before w
        w, h = (int(re.search(rf'\b{k}="(\d+)"', image).group(1)) for k in ("w", "h"))
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        # stack.xml lists the top layer first; composite bottom up
        for tag in reversed(re.findall(r"<layer\b[^>]*>", stack)):
            attr = dict(re.findall(r'(\w+)="([^"]*)"', tag))
            if attr.get("name", "").strip().lower() in skip or attr.get("visibility") == "hidden":
                continue
            im = Image.open(io.BytesIO(z.read(attr["src"]))).convert("RGBA")
            opacity = float(attr.get("opacity", 1))
            if opacity < 1 and keep_opacity:
                im.putalpha(im.getchannel("A").point(lambda a: int(a * opacity)))
            out.alpha_composite(im, (int(attr.get("x", 0)), int(attr.get("y", 0))))
    return out


def shade(sheet):
    """RGBA drawing to LA: alpha stays the mask, value becomes shade relative to the
    typical solid ink, so the drawing's own colour does not matter."""
    alpha = sheet.getchannel("A")
    value = sheet.convert("RGB").convert("L")
    hist = value.histogram(mask=alpha.point(lambda a: 255 if a > SOLID else 0))
    total = sum(hist)
    ref, seen = 255, 0
    for v, count in enumerate(hist):
        seen += count
        if total and seen >= REFERENCE * total:
            ref = v
            break
    lut = [min(255, round(255 * (v / 255 + SOFT) / (ref / 255 + SOFT))) for v in range(256)]
    return Image.merge("LA", (value.point(lut), alpha))


def cut(source, assets_dir):
    sheet = shade(_drawing(source))
    assets = Path(assets_dir)
    for row, mood in enumerate(MOODS):
        frames = []
        for col in range(COLS):
            box = (col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL)
            cell = sheet.crop(box)
            frame = assets / mood / f"{col + 1}.png"
            if not cell.getchannel("A").getbbox():
                # an erased cell must not keep playing from an earlier cut
                frame.unlink(missing_ok=True)
                continue
            frame.parent.mkdir(parents=True, exist_ok=True)
            cell.save(frame)
            frames.append(col + 1)
        if not frames:
            print(f"{mood}: nothing drawn yet")
            continue
        anim = assets / mood / "animation.json"
        if not anim.exists():
            anim.write_text(json.dumps(DEFAULT_ANIMATION, indent=2) + "\n")
        print(f"{mood}: frames {frames}")


def mouths(source, assets_dir):
    sheet = shade(_drawing(source, skip=NOT_DRAWING | {"face"}, keep_opacity=False))
    out = Path(assets_dir) / "mouths"
    for row, shape in enumerate(MOUTHS):
        takes = []
        for col in range(MOUTH_TAKES):
            cell = sheet.crop((col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL))
            frame = out / shape / f"{col + 1}.png"
            if not cell.getchannel("A").getbbox():
                frame.unlink(missing_ok=True)
                continue
            frame.parent.mkdir(parents=True, exist_ok=True)
            cell.save(frame)
            takes.append(col + 1)
        print(f"{shape}: takes {takes or 'none drawn'}")


def _strokes(alpha):
    """Every connected brush stroke of a frame, as lists of (x, y)."""
    from collections import deque
    a = alpha.load()
    w, h = alpha.size
    seen, out = set(), []
    for y in range(h):
        for x in range(w):
            if a[x, y] <= 40 or (x, y) in seen:
                continue
            seen.add((x, y))
            queue, points = deque([(x, y)]), []
            while queue:
                px, py = queue.popleft()
                points.append((px, py))
                for nx in (px - 1, px, px + 1):
                    for ny in (py - 1, py, py + 1):
                        if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and a[nx, ny] > 40:
                            seen.add((nx, ny))
                            queue.append((nx, ny))
            out.append(points)
    return out


def _parts(la):
    """Splits a drawn face into horns, eyes and mouth by whole strokes, never by a line, so
    a raised brow or a low eye keeps every bit of ink. Horns are the strokes that start up
    top, the mouth is the strokes whose middle sits low (plus a low stroke hugging it, like
    the curl of smug's smile), and the eyes and brows are everything else."""
    value, alpha = la.split()
    w, h = alpha.size
    strokes = _strokes(alpha)
    kinds = {}
    # The horns are the two biggest strokes that start up top, plus any bits lying wholly up
    # there. A brow raised as high as the horns reaches down past the line, so it stays an eye.
    high = sorted((i for i, points in enumerate(strokes) if min(py for _, py in points) < HORNS_TOP),
                  key=lambda i: -len(strokes[i]))
    horns = set(high[:2]) | {i for i in high[2:] if max(py for _, py in strokes[i]) < HORNS_TOP}
    for i, points in enumerate(strokes):
        middle = sum(py for _, py in points) / len(points)
        kinds[i] = "horns" if i in horns else "mouth" if middle >= MOUTH_TOP else "eyes"
    mouth = Image.new("L", (w, h), 0)
    m = mouth.load()
    for i, points in enumerate(strokes):
        if kinds[i] == "mouth":
            for px, py in points:
                m[px, py] = 255
    near_mouth = mouth.filter(ImageFilter.MaxFilter(25)).load()
    for i, points in enumerate(strokes):
        if kinds[i] == "eyes" and min(py for _, py in points) >= 280 and any(near_mouth[px, py] for px, py in points):
            kinds[i] = "mouth"
    out = {}
    for kind in ("horns", "eyes", "mouth"):
        mask = Image.new("L", (w, h), 0)
        k = mask.load()
        for i, points in enumerate(strokes):
            if kinds[i] == kind:
                for px, py in points:
                    k[px, py] = 255
        # the soft edges round each kept stroke come along
        out[kind] = Image.merge("LA", (value, ImageChops.multiply(alpha, mask.filter(ImageFilter.MaxFilter(5)))))
    return out


def rig(assets_dir):
    assets = Path(assets_dir)
    for n in range(1, COLS + 1):
        frame = Image.open(assets / "talking" / f"{n}.png").convert("LA")
        (assets / "horns").mkdir(exist_ok=True)
        _parts(frame)["horns"].save(assets / "horns" / f"{n}.png")
    for look, mood in EYES_FROM.items():
        out = assets / "eyes" / look
        out.mkdir(parents=True, exist_ok=True)
        for n in range(1, COLS + 1):
            _parts(Image.open(assets / mood / f"{n}.png").convert("LA"))["eyes"].save(out / f"{n}.png")
        print(f"eyes/{look}: from {mood}")


def preview(assets_dir, out_path, colour="#2d81ff"):
    assets = Path(assets_dir)
    tint = tuple(int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    thumb = 160
    rows = [m for m in MOODS if (assets / m).is_dir()]
    if not rows:
        sys.exit(f"nothing cut in {assets} yet")
    sheet = Image.new("RGBA", (2 * COLS * thumb, len(rows) * thumb), (0, 0, 0, 0))
    for half, bg in enumerate([(30, 32, 36, 255), (242, 241, 238, 255)]):
        sheet.paste(Image.new("RGBA", (COLS * thumb, len(rows) * thumb), bg), (half * COLS * thumb, 0))
    for r, mood in enumerate(rows):
        for n in range(1, COLS + 1):
            f = assets / mood / f"{n}.png"
            if not f.exists():
                continue
            la = Image.open(f).convert("LA").resize((thumb, thumb))
            shade_, alpha = la.split()
            rgb = Image.merge("RGB", [shade_.point(lambda v, c=c: v * c // 255) for c in tint])
            frame = rgb.convert("RGBA")
            frame.putalpha(alpha)
            for half in (0, 1):
                sheet.alpha_composite(frame, ((half * COLS + n - 1) * thumb, r * thumb))
    sheet.save(out_path)
    print(f"preview of {len(rows)} mood(s) -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "make":
        make(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "cut":
        cut(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "rig":
        rig(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "mouths":
        mouths(sys.argv[2], sys.argv[3])
    elif len(sys.argv) in (4, 5) and sys.argv[1] == "preview":
        preview(*sys.argv[2:])
    else:
        sys.exit(__doc__)
