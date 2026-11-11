# Diablo's faces

One brush face, eighteen moods. Marcus draws each mood four times by hand; the apps
cycle the four in shuffled order about 4 times a second, so the line boils like a
hand-drawn cartoon.

| What he is doing | | How he feels about it | |
|---|---|---|---|
| resting | app open, nothing going on | sly | the logo: a clever answer |
| asleep | voice waiting for "Diablo" | delighted | done, it went well |
| listening | after "Diablo", until you stop | proud | you hit a goal |
| thinking | working out the answer | relieved | trouble sorted |
| talking | his voice is speaking | smug | told you so |
| working | a task is running | wink | a playful yes |
| expectant | waiting on your answer | surprised | something unexpected |
| | | confused | did not catch that |
| | | worried | a warning |
| | | annoyed | you skipped push-ups |
| | | sorry | something failed |

## Drawing them

Like a sprite sheet: one big grid, one row per mood in the order above (resting first,
sorry last), four columns, one frame per cell. Every cell starts with that mood's guide.

    sheet.py make <guides-dir> <out-dir>

writes `faces.ora` (Krita: paper, guide at 30%, grid with labels, and a `drawing`
layer on top) and `faces-guide.png` (the same flat, for any other program). The
working copy lives in `~/Pictures/diablo-faces/` on banditbox.

Draw on the `drawing` layer (add more layers if you like) and save. Then

    sheet.py cut ~/Pictures/diablo-faces/faces.ora assets/faces

cuts it into one folder per mood, Minecraft style: `assets/faces/resting/1.png` to
`4.png`, one transparent PNG per drawn cell, plus `animation.json` the first time.
Empty cells are skipped. From another program, export just the drawing layer as a
transparent PNG and cut that instead.

## Colour is the theme's, shade is yours

Draw in any colour; only how light or dark the ink is survives. `cut` stores every
frame as grey plus alpha, like Minecraft's tinted grass textures. The alpha is the
mask, where there is ink. The grey is shade: the apps multiply it by the theme colour,
so your typical solid ink shows as the theme colour itself and darker
strokes as darker shades of it. Swap the theme colour and every face follows.

    sheet.py preview assets/faces /tmp/check.png "#ff6a00"

tints every cut frame with a colour, on dark and light, to check a cut.

## animation.json

Borrowed from Minecraft's `.png.mcmeta`, but with one image per frame instead of one
long strip.

```json
{"animation": {"frametime": 250, "order": "random", "frames": [1, 2, {"index": 3, "time": 400}, 4]}}
```

- `frametime`: milliseconds each frame shows. 250 is 4 frames a second, the speed Marcus picked.
- `order`: `random` shuffles and never repeats a frame back to back, which gives the
  line boil; `sequence` plays `frames` in order and loops.
- `frames`: optional. Image numbers, or `{"index": n, "time": ms}` to hold one longer.
  Left out, every numbered image plays.

The single `*.svg` files in this folder are traced from generated concepts and stand
in until the drawn frames exist. The guides came from Qwen-Image, whose licence is
non-commercial; the hand-drawn frames replace them.
