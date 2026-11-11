"""What he hears when Diablo does something without speaking: short sounds, never notifications.

Each kind of thing has its own sound, so he can tell what happened without looking: a mode
going on or off, dictation starting, each dictated piece landing, an act done, going back, an
answer left thinking. Diablo only speaks when something failed, and then says why. Anything to
look at belongs on screen in banditshell, later, not in the notification centre.
"""

from . import chime

# The on-screen face shows a short feeling after each of these (presence/Orb.qml). The ears
# set this to a function taking the mood's name.
on_moment = None


def _moment(mood):
    if on_moment is not None:
        try:
            on_moment(mood)
        except Exception:
            pass


def done(what=""):
    """A thing he asked for was done."""
    chime.play("ok")
    _moment("delighted")


def failed(what=""):
    """It did not work; the caller also says why."""
    chime.play("never")
    _moment("sorry")


def back(what=""):
    """Something was walked back: undo, go back, reopen."""
    chime.play("back")
    _moment("relieved")


def mode(on):
    """Speech control on (rising) or off (falling)."""
    chime.play("mode_on" if on else "mode_off")


def dictation(on):
    """Dictation is listening (on), or its text went in (off)."""
    chime.play("dictate_on" if on else "dictate_off")
    if not on:
        _moment("delighted")


def piece():
    """A dictated piece was heard and turned to text: keep going."""
    chime.play("piece")


def thinking():
    """An answer went to the background; he can talk again."""
    chime.play("thinking")
