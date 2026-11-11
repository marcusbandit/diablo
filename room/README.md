# room

The Hue lights in the room follow what the desktop is doing. Put an episode on fullscreen and the
ceiling goes dark while the desk lamp drops to a low amber glow. Pause it and the house lights come
up a little. Switch to T3 Code and the room turns bright and cool. After 23:00 the white light
starts warming and dimming, and by 02:00 it sits at candle colour.

Nothing to press. A user service reads the focused window, fullscreen state, MPRIS players, Discord
calls and the screen lock, picks a scene and fades the lamps to it over the Hue bridge.

## Scenes

| scene | when | default |
| --- | --- | --- |
| focus | T3 Code, a terminal, an editor, GitHub or docs | 100 %, 4300 K |
| everyday | anything else, Discord without a call | 70 %, 3000 K |
| watching | a video playing in a window | 40 %, 2700 K |
| cinema | fullscreen video playing | ceiling off, colour lamps amber at 9 %, whites 4 % at 2200 K |
| intermission | fullscreen video paused | 30 %, 2600 K |
| game | a fullscreen game, or a known game window | 25 %, 2700 K |
| call | a Discord client holding a microphone | 75 %, 3500 K |

A locked screen leaves the lamps alone. With bedtime switched on, a screen locked for five minutes
after 01:00 fades the lamps out over a minute.

A new activity has to last three seconds before the lights move, so alt-tab never flickers the
room. Cinema starts after one second and ends after six, so a glance at Discord mid-episode keeps
the dark.

## Hands off

Change a lamp yourself, in the Hue app, on a switch or through Diablo, and room leaves that lamp
alone until you switch to another activity, ten minutes at least. It tells the difference from the
bridge's event stream: the bridge echoes each of room's commands with the target values within a
few hundred milliseconds, so any update that matches nothing room sent came from somewhere else.

`room pause 1h` gives you every lamp for an hour. `room resume` takes them back, including any you
changed by hand.

## Install

    ./install.sh

That links `room` into `~/.local/bin`, installs zsh and bash completion, the banditshell launcher
plugin, a "Room setup" launcher entry, and starts `room.service`. `room setup` opens the walkthrough:
what room does, the bridge, which lamps, each scene on the real lamps, night and bedtime.

room finds the bridge from its own pairing (`~/.config/room/bridge.toml`), from `HUE_BRIDGE` and
`HUE_TOKEN` in the environment, or from `~/.config/marcusbandit-api-keys.env`. Without any of those
the walkthrough pairs it: press the button on the bridge, then Link.

## Commands

    room                 what the lights are doing, and why
    room why             every signal it reads right now, and what it makes of them
    room pause [1h]      leave the lamps alone; 30m, 2h30m and morning work too
    room resume          take the lamps back
    room try cinema [8]  show a scene on the lamps for a few seconds, then hand them back
    room scenes          the scenes with a colour swatch per lamp
    room lamps           every lamp on the bridge and which ones room uses
    room log [20]        recent changes, holds and releases
    room setup           the walkthrough again

## Settings

`~/.config/room/config.toml` only holds what you change; everything else falls back to the defaults
in `room/config.py`. Restart the service after editing (`systemctl --user restart room`).

```toml
lamps = ["Wood lamp", "The corner"]   # empty means every lamp in `room`, or every lamp

[night]
start = "22:30"
cap = 45

[scenes.cinema]
color = "#5a3cff"                     # colour lamps go violet instead of amber

[scenes.cinema.lamps."The corner"]
on = false                            # one lamp's own settings beat the scene's

[apps]
work = ["com.t3tools.T3Code", "foot", "dev.zed.Zed"]  # window classes that count as focus
```

`off` in a scene takes lamp names or Hue archetypes as globs, so `ceiling_*` means any ceiling lamp.

## Development

    python3 -m pytest -q        decision logic, scenes, night curve, hold detection
    python3 scripts/e2e.py      40 s on the real lamps: drives the daemon from a fake desktop,
                                reads the bridge back, then restores every lamp
    scripts/shot.sh NAME PAGE   screenshots a walkthrough page in a headless Broadway display

`ROOM_FAKE_SNAPSHOT=file.json` makes the daemon read the desktop from a file and `ROOM_DEBUG=1`
logs every command and bridge update to `~/.local/state/room/log.jsonl`.
