"""Diagrams drawn on the real screen piece by piece while he talks: the brain's draw_diagram tool.

The brain gives beats, each a line he says and the boxes and arrows that line is about. plan()
cleans them, lays every node out at once in a layered layout inside about 70% of the focused
monitor, and turns each beat into a marks beat with `keep`, so the overlay adds each beat's pieces
to the ones already drawn. Boxes come out in global logical pixels, edges as points on the node
borders with a control point for the curve. page() turns the same laid-out marks into one inline SVG
page for the desktop chat, so the picture there matches the one drawn on the screen.

Standard library only, so the desktop and the preview can import it without the hub's packages.
"""

import html
import math
import uuid

MAX_BEATS = 8
MAX_NODES = 14
MAX_EDGES = 20
LABEL = 28
TITLE = 60
SHAPES = ("box", "round", "diamond")
DIRECTIONS = ("down", "right")
LINGER = 45
DEFAULT_SCREEN = {"x": 0, "y": 0, "w": 1920, "h": 1080}
SHARE = 0.7
FONT = 17
CHAR = 0.6
NODE_H = 54
MIN_W = 140
RANK_GAP = 72
CROSS_GAP = 56
MAX_RANK_GAP = 150
MAX_CROSS_GAP = 120
TITLE_FONT = 26


def words(value, limit):
    return " ".join(str(value or "").split())[:limit]


def clean_screen(raw):
    if not isinstance(raw, dict):
        return None
    try:
        x, y, w, h = (float(raw[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w < 320 or h < 240 or w > 20000 or h > 20000:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def region_of(screen):
    """About 70% of the monitor's width and height, centered on it."""
    screen = clean_screen(screen) or DEFAULT_SCREEN
    w, h = screen["w"] * SHARE, screen["h"] * SHARE
    return {"x": screen["x"] + (screen["w"] - w) / 2, "y": screen["y"] + (screen["h"] - h) / 2, "w": w, "h": h}


def clean(arguments):
    """(title, direction, beats, notes) with nodes and edges checked against the caps, or the reason
    nothing can be drawn. Each beat holds the nodes it adds and the edges it adds; an edge whose
    end only comes in a later beat moves to that beat."""
    if not isinstance(arguments, dict):
        return "give a title and beats"
    title = words(arguments.get("title"), TITLE)
    direction = arguments.get("direction") if arguments.get("direction") in DIRECTIONS else "down"
    raw = arguments.get("beats")
    if isinstance(raw, dict):
        raw = [raw]
    raw = [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
    notes = []
    if len(raw) > MAX_BEATS:
        notes.append(f"only the first {MAX_BEATS} beats are drawn")
        raw = raw[:MAX_BEATS]
    nodes, where, by_label, beats = {}, {}, {}, []
    for n, item in enumerate(raw):
        added = []
        for node in item.get("nodes") or []:
            if isinstance(node, str):
                node = {"label": node}
            if not isinstance(node, dict):
                continue
            label = words(node.get("label") or node.get("id"), LABEL)
            key = words(node.get("id") or label, 40)
            if not label or key in nodes:
                continue
            if len(nodes) >= MAX_NODES:
                notes.append(f"\"{label}\" left out, {MAX_NODES} boxes at most")
                continue
            shape = node.get("shape") if node.get("shape") in SHAPES else "box"
            nodes[key] = {"id": key, "label": label, "shape": shape, "order": len(nodes)}
            where[key] = n
            by_label.setdefault(label.casefold(), key)
            added.append(key)
        beats.append({"say": words(item.get("say"), 300), "nodes": added, "edges": [], "circle": []})
    find = lambda ref: (lambda k: k if k in nodes else by_label.get(k.casefold()))(words(ref, 40))
    edges, seen = [], set()
    for n, item in enumerate(raw):
        for edge in item.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            a, b = find(edge.get("from")), find(edge.get("to"))
            if not a or not b:
                missing = edge.get("from") if not a else edge.get("to")
                notes.append(f"arrow to or from \"{words(missing, 40)}\" left out, no box has that id")
                continue
            if a == b or (a, b) in seen:
                continue
            if len(edges) >= MAX_EDGES:
                notes.append(f"arrows past {MAX_EDGES} left out")
                break
            seen.add((a, b))
            at = max(n, where[a], where[b])
            edges.append({"from": a, "to": b, "label": words(edge.get("label"), LABEL), "beat": at})
            beats[at]["edges"].append(len(edges) - 1)
    for n, item in enumerate(raw):
        circle = item.get("circle") or []
        for ref in [circle] if isinstance(circle, str) else circle if isinstance(circle, list) else []:
            key = find(ref) if isinstance(ref, str) else None
            if not key:
                continue
            at = max(n, where[key])
            if key not in beats[at]["circle"]:
                beats[at]["circle"].append(key)
    beats = [b for b in beats if b["say"] or b["nodes"] or b["edges"]]
    if not nodes:
        return "nothing to draw: give beats with nodes, each {id, label}"
    return title, direction, beats, list(nodes.values()), edges, notes


def ranks_of(nodes, edges):
    """Each node's rank: the longest path to it from a source, with edges that close a cycle left out."""
    order = [n["id"] for n in nodes]
    out = {k: [] for k in order}
    for e in edges:
        out[e["from"]].append(e["to"])
    state, back = {}, set()

    def visit(k):
        state[k] = 1
        for nxt in out[k]:
            if state.get(nxt) == 1:
                back.add((k, nxt))
            elif not state.get(nxt):
                visit(nxt)
        state[k] = 2

    for k in order:
        if not state.get(k):
            visit(k)
    rank = {k: 0 for k in order}
    for _ in range(len(order)):
        moved = False
        for e in edges:
            a, b = e["from"], e["to"]
            if (a, b) not in back and rank[b] < rank[a] + 1:
                rank[b] = rank[a] + 1
                moved = True
        if not moved:
            break
    return rank


def size_of(node, scale):
    font = FONT * scale
    text = len(node["label"]) * font * CHAR
    if node["shape"] == "diamond":
        return max(MIN_W * 1.2 * scale, text * 1.5 + 56 * scale), NODE_H * 1.6 * scale
    w = max(MIN_W * scale, text + 44 * scale)
    return (w + 14 * scale if node["shape"] == "round" else w), NODE_H * scale


def place(nodes, edges, region, direction="down"):
    """Every node's box in global logical pixels, the font size that fits them in the region, and
    the ranks. A rank too wide for the region wraps onto a second row."""
    rank = ranks_of(nodes, edges)
    layers = {}
    for node in sorted(nodes, key=lambda n: n["order"]):
        layers.setdefault(rank[node["id"]], []).append(node)
    down = direction == "down"
    along_room, cross_room = (region["h"], region["w"]) if down else (region["w"], region["h"])

    def needs(scale):
        sizes = {n["id"]: size_of(n, scale) for n in nodes}
        cross_of = lambda n: sizes[n["id"]][0 if down else 1]
        levels = []
        for r in sorted(layers):
            row, used = [], 0.0
            for node in layers[r]:
                extra = cross_of(node) + (CROSS_GAP * scale if row else 0)
                if row and used + extra > cross_room:
                    levels.append(row)
                    row, used = [], 0.0
                    extra = cross_of(node)
                row.append(node)
                used += extra
            levels.append(row)
        thick = [max(sizes[n["id"]][1 if down else 0] for n in level) for level in levels]
        cross = [sum(cross_of(n) for n in level) + CROSS_GAP * scale * (len(level) - 1) for level in levels]
        return sizes, levels, thick, sum(thick) + RANK_GAP * scale * (len(levels) - 1), max(cross)

    scale = 1.0
    for _ in range(16):
        sizes, levels, thick, along, cross = needs(scale)
        if along <= along_room and cross <= cross_room or scale <= 0.4:
            break
        scale = max(0.4, scale * min(0.95, along_room / along, cross_room / cross))
    sizes, levels, thick, along, cross = needs(scale)
    spare = (along_room - sum(thick)) / max(1, len(levels) - 1)
    gap = min(max(RANK_GAP * scale, spare), MAX_RANK_GAP * scale)
    total = sum(thick) + gap * (len(levels) - 1)
    start_along = (region["y"] if down else region["x"]) + (along_room - total) / 2
    mid_cross = (region["x"] + region["w"] / 2) if down else (region["y"] + region["h"] / 2)
    boxes, at = {}, start_along
    for level, depth in zip(levels, thick):
        widths = [sizes[n["id"]][0 if down else 1] for n in level]
        room = cross_room - sum(widths)
        cgap = min(max(CROSS_GAP * scale, room / (len(level) + 1)), MAX_CROSS_GAP * scale) if len(level) > 1 else 0
        c = mid_cross - (sum(widths) + cgap * (len(level) - 1)) / 2
        centre = at + depth / 2
        for node, span in zip(level, widths):
            w, h = sizes[node["id"]]
            if down:
                boxes[node["id"]] = {"x": c, "y": centre - h / 2, "w": w, "h": h}
            else:
                boxes[node["id"]] = {"x": centre - w / 2, "y": c, "w": w, "h": h}
            c += span + cgap
        at += depth + gap
    return {k: {key: round(v, 1) for key, v in b.items()} for k, b in boxes.items()}, round(FONT * scale, 1), rank


def middle(box):
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def border(box, shape, towards):
    """Where the line from the box's middle towards a point leaves the node's outline."""
    cx, cy = middle(box)
    dx, dy = towards[0] - cx, towards[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = box["w"] / 2, box["h"] / 2
    if shape == "diamond":
        t = 1 / (abs(dx) / hw + abs(dy) / hh)
    else:
        t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def quad(p0, c, p1, t):
    u = 1 - t
    return u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]


def hits(p0, c, p1, boxes, pad=10):
    for box in boxes:
        x0, y0, x1, y1 = box["x"] - pad, box["y"] - pad, box["x"] + box["w"] + pad, box["y"] + box["h"] + pad
        for i in range(1, 24):
            x, y = quad(p0, c, p1, i / 24)
            if x0 < x < x1 and y0 < y < y1:
                return True
    return False


def route(a, b, shapes, boxes):
    """An edge from node a to node b: start and end on their borders, and the curve's control
    point, bent only as far as it takes to miss the other boxes."""
    pa, pb = middle(boxes[a]), middle(boxes[b])
    others = [box for k, box in boxes.items() if k not in (a, b)]
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    length = math.hypot(dx, dy) or 1
    nx, ny = -dy / length, dx / length
    mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    via = mid
    for bend in (0, 50, -50, 100, -100, 160, -160, 240, -240):
        c = (mid[0] + nx * bend, mid[1] + ny * bend)
        s, e = border(boxes[a], shapes[a], c), border(boxes[b], shapes[b], c)
        if not hits(s, c, e, others):
            via = c
            break
    start, end = border(boxes[a], shapes[a], via), border(boxes[b], shapes[b], via)
    if via == mid:
        via = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    pt = lambda p: {"x": round(p[0], 1), "y": round(p[1], 1)}
    return {"start": pt(start), "end": pt(end), "via": pt(via)}


def title_box(title, region, scale):
    size = round(TITLE_FONT * max(0.7, scale), 1)
    w = len(title) * size * 0.56 + 40
    return {"x": round(region["x"] + region["w"] / 2 - w / 2, 1), "y": round(region["y"] - size - 44, 1),
            "w": round(w, 1), "h": round(size + 20, 1)}, size


def plan(arguments, screen=None, show=None):
    """The beats to POST to /v1/screen/marks, in order, and the notes on what was left out; or why
    nothing can be drawn."""
    cleaned = clean(arguments)
    if isinstance(cleaned, str):
        return cleaned
    title, direction, beats, nodes, edges, notes = cleaned
    region = region_of(screen)
    boxes, font, _ = place(nodes, edges, region, direction)
    shapes = {n["id"]: n["shape"] for n in nodes}
    labels = {n["id"]: n["label"] for n in nodes}
    show = show or "dg" + uuid.uuid4().hex[:10]
    out = []
    for n, beat in enumerate(beats):
        marks = []
        if n == 0 and title:
            box, size = title_box(title, region, font / FONT)
            marks.append({"kind": "title", "text": title, "box": box, "size": size})
        for key in beat["nodes"]:
            marks.append({"kind": "node", "text": labels[key], "shape": shapes[key], "box": boxes[key], "size": font})
        for i in beat["edges"]:
            e = edges[i]
            mark = {"kind": "edge", **route(e["from"], e["to"], shapes, boxes), "size": font}
            if e["label"]:
                mark["text"] = e["label"]
            marks.append(mark)
        for key in beat.get("circle", []):
            marks.append({"kind": "ring", "box": boxes[key]})
        out.append({"show": show, "n": n, "count": len(beats), "say": beat["say"], "marks": marks, "keep": True,
                    "linger": LINGER})
    return out, notes


def summary(sent, beats, notes, failed=None):
    if failed and not sent:
        return f"not drawn: {failed}"
    pieces = sum(1 for b in beats for m in b["marks"] if m["kind"] == "node")
    arrows = sum(1 for b in beats for m in b["marks"] if m["kind"] == "edge")
    parts = [f"Drawing: {len(sent)} beat{'s' if len(sent) != 1 else ''} going up, {pieces} boxes and {arrows} arrows"]
    if failed:
        parts.append(f"stopped after {len(sent)} of {len(beats)}: {failed}")
    if notes:
        parts.append("Left out: " + "; ".join(notes[:6]))
    parts.append("Each beat draws while its line is said, so now say the beats' lines in order and nothing more")
    return ". ".join(parts) + "."


PAD = 28
CHAT_W = 760
STYLE = (
    "html,body{margin:0;background:var(--bg,#1e2024);color:var(--ink,#f2f1ee);"
    "font-family:var(--font-sans,system-ui,sans-serif)}"
    "svg{display:block;margin:0 auto;max-width:100%;height:auto}"
    ".node{fill:var(--raised,#1a1c20);stroke:var(--line,rgba(242,241,238,.18));stroke-width:1.5}"
    ".label{fill:var(--ink,#f2f1ee);text-anchor:middle;dominant-baseline:central}"
    ".edge{fill:none;stroke:var(--muted,rgba(242,241,238,.66));stroke-width:2;stroke-linecap:round}"
    ".head{fill:var(--muted,rgba(242,241,238,.66))}"
    ".note{fill:var(--muted,rgba(242,241,238,.66));text-anchor:middle;dominant-baseline:central;"
    "stroke:var(--bg,#1e2024);stroke-width:5;stroke-linejoin:round;paint-order:stroke}"
    ".ring{fill:none;stroke:var(--accent,#2d81ff);stroke-width:3}"
    ".title{fill:var(--ink,#f2f1ee);text-anchor:middle;dominant-baseline:central;font-weight:600}"
)


def _num(value):
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _text(value):
    return html.escape(str(value or ""), quote=True)


def _reach(mark):
    """The corners a mark can reach, so the picture is cropped to what is drawn."""
    kind = mark.get("kind")
    if kind == "edge":
        points = [mark["start"], mark["end"], mark["via"]]
        out = [(p["x"], p["y"]) for p in points]
        if mark.get("text"):
            x, y = quad(*[(p["x"], p["y"]) for p in (mark["start"], mark["via"], mark["end"])], 0.5)
            half = len(mark["text"]) * mark["size"] * 0.8 * CHAR / 2 + 4
            out += [(x - half, y - mark["size"]), (x + half, y + mark["size"])]
        return out
    box = mark.get("box")
    if not box:
        return []
    grow = 16 if kind == "ring" else 0
    return [(box["x"] - grow, box["y"] - grow), (box["x"] + box["w"] + grow, box["y"] + box["h"] + grow)]


def _shape(mark):
    box, shape = mark["box"], mark.get("shape") or "box"
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    if shape == "diamond":
        points = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
        return '<polygon class="node" points="' + " ".join(f"{_num(a)},{_num(b)}" for a, b in points) + '"/>'
    radius = h / 2 if shape == "round" else 8
    return (f'<rect class="node" x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
            f'rx="{_num(radius)}"/>')


def _label(mark, cls="label"):
    cx, cy = middle(mark["box"])
    return f'<text class="{cls}" x="{_num(cx)}" y="{_num(cy)}" font-size="{_num(mark["size"])}">{_text(mark["text"])}</text>'


def _arrow(mark):
    s, c, e = ((p["x"], p["y"]) for p in (mark["start"], mark["via"], mark["end"]))
    size = max(7.0, mark["size"] * 0.6)
    dx, dy = e[0] - c[0], e[1] - c[1]
    length = math.hypot(dx, dy) or 1
    ux, uy = dx / length, dy / length
    back = (e[0] - ux * size * 1.4, e[1] - uy * size * 1.4)
    wing = [(back[0] - uy * size * 0.6, back[1] + ux * size * 0.6), (back[0] + uy * size * 0.6, back[1] - ux * size * 0.6)]
    path = f"M{_num(s[0])} {_num(s[1])} Q{_num(c[0])} {_num(c[1])} {_num(back[0])} {_num(back[1])}"
    head = " ".join(f"{_num(a)},{_num(b)}" for a, b in [e, *wing])
    out = [f'<path class="edge" d="{path}"/>', f'<polygon class="head" points="{head}"/>']
    if mark.get("text"):
        x, y = quad(s, c, e, 0.5)
        out.append(f'<text class="note" x="{_num(x)}" y="{_num(y)}" font-size="{_num(mark["size"] * 0.8)}">'
                   f'{_text(mark["text"])}</text>')
    return out


def frame(beats):
    """The drawing's bounds with a margin, (x, y, w, h), or None when nothing is drawn."""
    corners = [p for b in beats or [] for m in b.get("marks") or [] for p in _reach(m)]
    if not corners:
        return None
    x0, y0 = min(p[0] for p in corners) - PAD, min(p[1] for p in corners) - PAD
    return x0, y0, max(p[0] for p in corners) + PAD - x0, max(p[1] for p in corners) + PAD - y0


def svg(beats):
    """Every beat's marks as one SVG, cropped to the drawing: arrows under the boxes, rings and the
    title on top, coloured by the app's theme variables. An empty string when nothing is drawn."""
    marks = [m for b in beats or [] for m in b.get("marks") or []]
    framed = frame(beats)
    if not framed:
        return ""
    x0, y0, w, h = framed
    title = next((m["text"] for m in marks if m.get("kind") == "title"), "")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_num(x0)} {_num(y0)} {_num(w)} {_num(h)}" '
           f'width="{_num(w)}" height="{_num(h)}" role="img" aria-label="{_text(title or "Diagram")}">']
    for mark in marks:
        if mark.get("kind") == "edge":
            out += _arrow(mark)
    for mark in marks:
        if mark.get("kind") == "node":
            out += [_shape(mark), _label(mark)]
    for mark in marks:
        if mark.get("kind") == "ring":
            cx, cy = middle(mark["box"])
            out.append(f'<ellipse class="ring" cx="{_num(cx)}" cy="{_num(cy)}" rx="{_num(mark["box"]["w"] / 2 + 14)}" '
                       f'ry="{_num(mark["box"]["h"] / 2 + 12)}"/>')
        elif mark.get("kind") == "title":
            out.append(_label(mark, "title"))
    out.append("</svg>")
    return "".join(out)


def page(arguments, screen=None):
    """The diagram as an emit_object html page for the desktop chat, laid out by plan() exactly as
    the overlay gets it; None when there is nothing to draw."""
    planned = plan(arguments, screen, show="chat")
    if isinstance(planned, str):
        return None
    beats, _ = planned
    picture = svg(beats)
    if not picture:
        return None
    title = next((m["text"] for b in beats for m in b["marks"] if m["kind"] == "title"), "") or "Diagram"
    _, _, w, h = frame(beats)
    document = (f'<!doctype html><html><head><meta charset="utf-8"><title>{_text(title)}</title>'
                f"<style>{STYLE}</style></head><body>{picture}</body></html>")
    return {"kind": "html", "title": title, "html": document, "height": round(h * min(1.0, CHAT_W / w)) + 8}


SAMPLE = {
    "title": "How the Linux kernel runs a program",
    "beats": [
        {"say": "A program you run lives in user space.", "nodes": [{"id": "app", "label": "User program", "shape": "round"}]},
        {"say": "When it needs anything real, it makes a system call.",
         "nodes": [{"id": "sys", "label": "System call"}], "edges": [{"from": "app", "to": "sys", "label": "read, write, fork"}]},
        {"say": "That traps into the kernel, which checks and takes over.",
         "nodes": [{"id": "kernel", "label": "Kernel", "shape": "diamond"}], "edges": [{"from": "sys", "to": "kernel", "label": "trap"}],
         "circle": ["kernel"]},
        {"say": "The kernel hands CPU time out through the scheduler, and devices through drivers.",
         "nodes": [{"id": "sched", "label": "Scheduler"}, {"id": "drv", "label": "Device drivers"}],
         "edges": [{"from": "kernel", "to": "sched"}, {"from": "kernel", "to": "drv"}]},
        {"say": "Both end at the hardware, the CPU, memory and disks.",
         "nodes": [{"id": "hw", "label": "Hardware"}],
         "edges": [{"from": "sched", "to": "hw", "label": "CPU time"}, {"from": "drv", "to": "hw", "label": "I/O"}]},
    ],
}
