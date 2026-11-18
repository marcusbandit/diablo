# His hands on the desktop

`diablo-hands` (voice/hands.py) lets the brain read and work other apps on the desktop box in one
call per task. The hub reaches it over ssh, the same way `focus_window` and `stage_window` reach
voice/window.py, through four MCP tools: `desktop_front`, `read_window`, `find_on_screen` and
`act_on_desktop`.

Install: `deploy/ears-install.sh` links `deploy/diablo-hands` into ~/.local/bin with its zsh and
bash completions. The launcher runs `/usr/bin/python3 -P -m voice.hands` with the repo on
`PYTHONPATH`: AT-SPI comes from python-gobject, which the system Python has and the voice venv
does not. The module imports nothing outside the standard library at load, so the healer's smoke
import under the venv still passes, and without gobject `ui` says so in `hint` while OCR keeps
working.

## The contract

One command per process. Exactly one JSON object on stdout, compact unless stdout is a terminal.
A failure is `{"error": "..."}` with exit 1; a refused risky click is `{"needs_confirm": "..."}`
with exit 3. Every box is in global logical screen pixels, the space `hyprctl` reports windows
and the cursor in. Each reply carries `ms`, the time the command took.

`--window` takes `front` (the default), a Hyprland address (`0x...`), or part of an app class or
title, matched class first, then initial class, then title, most recently focused first.

### front

The focused window: `address`, `class`, `title`, `pid`, `x`, `y`, `w`, `h`, `monitor` (its
name), `scale`, `workspace`, `visible`, and `locked` for the screen. With nothing focused,
`{"window": null, "monitor", "scale"}`. About 30 ms inside, 65 ms with process start.

### windows

`{"windows": [...]}`, the same short fields per window, front to back by focus history. A window
on a workspace no monitor shows has `visible: false`.

### ui [--window W] [--max 300]

The window's accessibility tree through AT-SPI:

    {"window": {...}, "coords": "window", "controls": [
       {"id": 1, "role": "button", "name": "Save", "states": ["focused"], "box": [x, y, w, h]},
       {"id": 2, "role": "field", "name": "Title", "value": "Draft", "box": [...]}],
     "text": "visible text, one piece a line", "ms": 340}

Controls are numbered in walk order, so the same window gives the same ids to the next process
as long as it has not changed; `act` re-reads the tree and checks a step's `name` against the id
when it carries one. `role` is short (`button`, `toggle`, `checkbox`, `radio`, `combo`, `field`,
`password`, `spin`, `slider`, `link`, `menuitem`, `menu`, `tab`, `item`, `switch`, `terminal`),
or AT-SPI's own role name for anything else that takes focus and has a name. `states` lists only
what is set: `focused`, `checked`, `selected`, `expanded`, `pressed`, `editable`, `readonly`,
`required`, `disabled`. `value` is a field's text or a slider's number. `more_controls` counts the
ones past `--max`; `truncated` means the walk hit its cap (3000 nodes, 2.5 s, depth 60, 400
children a node).

The walk only goes where something is on screen: invisible and defunct nodes are skipped with
their subtrees, and so are nodes whose box lies outside the window. Where an app sets SHOWING
properly (GTK, Qt) nodes without it are skipped too; Firefox on Wayland sets it on nothing, so
there closed popup menus at the top of the frame are dropped instead, and only the tab document
the frame's EMBEDS relation points at is walked, not every background tab.

`coords` says how the app reported positions and how they were mapped:

- `window`: relative to the window, as most Wayland clients do; the window's position is added.
- `global`: already global, as X11 clients do.
- `physical`: in device pixels on a scaled monitor; divided by the scale.
- `none`: the app gives no positions (all zeros); boxes are null and clicks go through AT-SPI
  actions or OCR instead.

The mapping fits the app's own frame box onto the window box Hyprland reports, so the three
spaces come out of one formula, and a frame larger than the window by a shadow margin is centred
on it. Boxes are clipped to the window.

A window with no tree gets `hint`, plus `a11y` with org.a11y.Status `IsEnabled` and
`ScreenReaderEnabled`. Chromium and Electron apps (T3 Code, Discord, VS Code) expose their
controls only when started with `--force-renderer-accessibility`, or when ScreenReaderEnabled is
on as they start. diablo-hands does not switch either on; find-text and click_text read those
windows by OCR.

### find-text TEXT [--window W] [--all]

    {"matches": [{"text": "Sign in", "x": 1010, "y": 90, "w": 34, "h": 7, "source": "ocr"},
                 {"text": "Save", "x": 10, "y": 10, "w": 40, "h": 20, "source": "atspi", "control_id": 4}],
     "window": {"address", "class"}, "ms": 650}

This is the contract the on-screen pointing overlay in presence/ reads; keep it stable. Matches
are best first. AT-SPI comes first: the window's control names, values and text, case and
punctuation ignored, an exact name above a phrase inside a longer one, controls above plain
text, with the exact range box when the text interface gives one. Only when nothing matches
does OCR run: `grim` captures the window clipped to its monitor (the focused monitor when no
window has focus) as PPM on a pipe, `tesseract --psm 11` reads it as TSV, the words are grouped
into lines by position, and the phrase is matched across consecutive words with light fuzz
(a similarity of 0.8, exact for three letters or fewer). OCR boxes are mapped from image pixels
back through the capture's own size, so a scaled monitor comes out right. OCR matches carry a
`score`. `--all` runs OCR even when AT-SPI found something.

OCR is skipped, with the reason in `ocr`, while the screen is locked (a capture would only show
the lock screen) and when the window is not on a visible workspace. A window takes about 0.6 s
to read, a whole monitor about 1 s.

### act [--window W] --steps JSON

Runs a whole plan, stops at the first failing step, and reports:

    {"ok": true, "steps": [{"do": "click_text", "ok": true, "detail": "clicked \"Save\" at 1030,20 (atspi)"}],
     "front": {...}, "controls": [...], "text": "...", "ms": 1400}

`controls` and `text` are a fresh `ui` of the front window after acting (60 controls, 800
characters), so the caller never reads again. A failure adds `failed_step` and `error`.

Steps:

| do | fields | what it does |
|---|---|---|
| focus | | brings the window forward (window.bring) |
| click_text | text, near?, button?, double? | find-text, then a pointer click on the centre; near picks the match closest to another text |
| click | id, name?, button?, double? | a control from ui: its AT-SPI press action, or grab_focus for a field, else a pointer click |
| click_at | x, y, button?, double? | a pointer click at a global point |
| type | text | wtype; the voice loop's paste (commands.type_text) when wtype fails |
| key | key | a combo such as `ctrl+l`, `Return`, `ctrl+shift+t`, or a voice loop key name such as `select_all`; ydotool first, wtype for keys it lacks |
| fill | text, field? | focuses the field by name (or uses the focused one), ctrl+a, types, then reads it back through AT-SPI |
| scroll | direction, amount? | moves the pointer over the window (or x, y) and turns the wheel, 1 to 15 notches, 5 by default |
| open | target | an http, https, file or mailto address with xdg-open, an existing path, or an app by name with gtk-launch |
| wait | seconds | up to 5 |

The pointer moves with `hyprctl dispatch hl.dsp.cursor.move` (legacy `movecursor` as the
fallback) and clicks with ydotool. Where it was before the plan, it goes back after. When the
target window is not the focused one and the plan types or presses keys, a focus step goes first.
Each acting step waits a moment for the app to draw (0.1 to 0.5 s), and click_text tries once
more 0.6 s later when its text is not there yet.

A plan that only puts an address in the address bar and presses Return (`ctrl+l`, `alt+d` or
`f6`, then type or fill a URL, then Return) is turned into one `open` of that URL; the reply says
so in `rewritten`.

Risky clicks: a click whose text, control name or the control under the point matches delete,
remove, pay, buy, purchase, send, submit order, place order, sign out, log out, uninstall,
format or transfer, or the Danish slet, fjern, betal, køb, indsend, bestil, afgiv ordre, log ud,
afinstaller, formater or overfør, is refused unless that step carries `confirmed: true`. Words
known before the plan runs are checked before any step runs; a match found while acting stops
the plan at that step. The reply is `{"needs_confirm": "..."}` with exit 3, and the brain asks
Marcus first.

Nothing runs while the screen is locked: `act` refuses at once, whoever asked.

## The tools

hub/mcp_server.py runs `./deploy/diablo-hands` in the desktop checkout over ssh
(`desktop.host`, else the lock host; the checkout is `DIABLO_DESKTOP_REPO`, default
`Projects/diablo`) and hands the JSON line back as the tool result.

- `desktop_front`: front.
- `read_window` (window?, max?): ui.
- `find_on_screen` (text, window?, all?): find-text.
- `act_on_desktop` (steps, window?): act.

Their descriptions teach the fast way: act on visible words with click_text instead of a
screenshot first, put the whole task in one act_on_desktop call, and trust the controls that
come back instead of reading again. None of them is on lockguard's allowlist, so a turn from a
locked desktop is refused before ssh is touched. They are MCP tools only, like focus_window;
the api fallback agent does not have them.

Tests: tests/test_hands.py (OCR parsing and matching, coordinate mapping, steps, the URL rewrite,
risky clicks, the JSON contract) and tests/test_hands_mcp.py (the tool wiring with ssh mocked).
