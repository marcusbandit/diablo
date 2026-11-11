"""Telling room's own commands apart from a person changing a lamp.

The bridge echoes a command at once with the target values, even during a fade. So an
update that matches something room sent in the last few seconds is room's own; anything
else came from the Hue app, a switch, Diablo or a routine, and that lamp becomes held:
room leaves it alone until the activity changes and at least `manual_min` has passed.
"""

from dataclasses import dataclass, field

ECHO_S = 0.5  # an update this soon after a command is its echo even if the bridge rounded it
# Past that, only an update that matches something room sent counts as room's own: the bridge
# echoes the target values within a few hundred milliseconds, even during a fade.
REMEMBER_S = 15.0  # earlier commands this recent still count when matching an update
# The last command always counts, however old: a 60 s bedtime fade or a 20 s night fade keeps
# reporting long after it was sent.


@dataclass
class Hold:
    since: float
    state: str  # the activity when the lamp was taken over
    what: str  # a short description of the change, for `room why`
    moved: bool = False  # the activity has changed at least once since the takeover


@dataclass
class Holds:
    sent: dict = field(default_factory=dict)  # lamp id -> [(at, Target)]
    held: dict = field(default_factory=dict)  # lamp id -> Hold

    def note_sent(self, lamp_id, target, now):
        recent = [(at, t) for at, t in self.sent.get(lamp_id, []) if now - at < REMEMBER_S]
        recent.append((now, target))  # the newest stays even once it is older than REMEMBER_S
        self.sent[lamp_id] = recent

    def on_update(self, lamp_id, data, now, state):
        """Feed one light update from the event stream. Returns a Hold when it is a takeover."""
        if lamp_id in self.held:
            return None
        recent = self.sent.get(lamp_id)
        if not recent:
            return None  # room has not driven this lamp yet, so there is nothing to defend
        last_at = recent[-1][0]
        if now - last_at < ECHO_S:
            return None
        last = recent[-1][1]
        if agrees(last, data) or any(agrees(t, data) for at, t in recent if now - at < REMEMBER_S):
            return None
        hold = Hold(since=now, state=state, what=describe(data))
        self.held[lamp_id] = hold
        return hold

    def release(self, state, now, manual_min):
        """Lamps whose hold has run out: the activity has changed since, and the minimum time passed.
        Switching away and back counts as a change, so a hold never outlives the activity it was for."""
        for h in self.held.values():
            if h.state != state:
                h.moved = True
        done = [lid for lid, h in self.held.items() if h.moved and now - h.since >= manual_min * 60]
        for lid in done:
            del self.held[lid]
        return done

    def clear(self):
        self.held.clear()


def agrees(target, data):
    """Does a v2 light update fit what room asked for? Only fields present in the update count."""
    on = (data.get("on") or {}).get("on")
    if on is not None and on != target.on:
        return False
    if not target.on:
        # Fading a lamp out, the bridge reports in-between brightness and then resets the stored
        # level while the lamp stays off. Nobody sees an off lamp's brightness; only on counts.
        return True
    bri = (data.get("dimming") or {}).get("brightness")
    if bri is not None and abs(bri - target.bri) > 2.5 and not (target.bri < 3 and bri < 3):
        return False
    ct = data.get("color_temperature") or {}
    xy = (data.get("color") or {}).get("xy")
    white = ct.get("mirek_valid")  # True: the lamp is in white mode; False: it is in colour mode
    if target.xy:
        if white:
            return False  # someone picked a white while room had a colour on
        if xy and (abs(xy["x"] - target.xy[0]) > 0.03 or abs(xy["y"] - target.xy[1]) > 0.03):
            return False
    elif target.mirek:
        if white is False:
            return False  # switched to a colour
        if white and ct.get("mirek") is not None and abs(ct["mirek"] - target.mirek) > 12:
            return False
        if xy and "color_temperature" not in data:
            return False  # a colour change: white light always comes with its mirek
    return True


def describe(data):
    on = (data.get("on") or {}).get("on")
    if on is False:
        return "turned off"
    if on is True and "dimming" not in data:
        return "turned on"
    if "color" in data:
        return "colour changed"
    if "color_temperature" in data:
        return "white changed"
    if "dimming" in data:
        return f"set to {round(data['dimming']['brightness'])} %"
    return "changed"
