---
name: explain-3d
description: Load this when Marcus asks how something works, what something looks like inside, or to see something in 3D, and a picture he can turn would explain it better than words, such as an engine, an atom, a planet's orbit, a molecule, a bridge, a network, a data structure or a machine. Builds one small interactive three.js page and shows it big in the central card on his desktop.
---

# Explaining in 3D

An interactive model he can turn says more than a paragraph. Build one page, show it, and keep
the reply itself to a line ("Here is how a four-stroke engine moves, sir.").

## When

- "How does X work", "show me X", "what does X look like", "explain X in 3D".
- Something with parts in space or motion: mechanisms, structures, orbits, molecules, layouts.
- Not for plain facts, numbers or lists: those are a small card (`show_on_screen` with title,
  big and lines) or words.

## How

1. Start from `example.html` next to this file: an atom with a nucleus, two shells, moving
   electrons, labels that follow the parts, a legend, and orbit controls. Copy its structure:
   the importmap, the scene setup, `resize`, the animation loop, the label projection.
2. Build the model from simple shapes (`SphereGeometry`, `BoxGeometry`, `CylinderGeometry`,
   `TorusGeometry`, `TubeGeometry`, groups). Animate what moves; a slow turn of the whole model
   when nothing else moves. Five to fifteen parts is plenty.
3. Label the parts that matter (three to six), with the legend for colours and a two line
   explanation top left: what it is, then the one thing to notice.
4. Show it: `show_on_screen` with `size: "large"`, a short `title`, and the page in `html`.

## Rules for the page

- One self-contained HTML document, at most 512 KB. three.js comes from jsdelivr, pinned:
  `https://cdn.jsdelivr.net/npm/three@0.170.0/...`. No other network, no local files (the card
  cannot read them), no `alert`, no new windows.
- Dark like his desktop: background `#0f1114`, text `#d0d7dd`, quiet text `#78838f`, lines
  `#3a4149`, and his colours for parts: `#7fb3d4` blue, `#e0915a` orange, `#6fc49a` green,
  `#a98fe0` violet, `#d8c48c` sand, `#f29ba8` pink.
- It fills its box (`html, body { height: 100% }`, renderer sized from the container on
  `resize`), because the card can be any size.
- Plain English labels, no dashes in text.
- A wrong model is worse than none. If unsure how the thing is built, say so in the
  explanation, or build the simpler model you are sure of.
