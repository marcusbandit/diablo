"""Commands run in Diablo's checkout on the desktop box: over ssh, or through bash when this is that box.

The window verbs, the hands tools and pointing all reach the desktop this way. Standard library
only at the top, so hub/pointing.py can use it on the desktop without the hub's packages.
"""

import shlex
import socket
import subprocess

DEFAULT_REPO = "Projects/diablo"


class Unreachable(Exception):
    pass


def desktop_repo():
    """The checkout on the desktop box, relative to its home: DIABLO_DESKTOP_REPO, else Projects/diablo."""
    from . import localenv

    return (localenv.get("DIABLO_DESKTOP_REPO") or DEFAULT_REPO).removeprefix("~/")


def desktop_host():
    """The desktop box: the desktop.host setting, else the host the screen lock is read on."""
    from . import lockguard, settings

    return str(settings.get("desktop.host") or "").strip() or lockguard.host()


def desktop_run(argv, timeout, host=None, repo=None, connect=8, run=None):
    """argv run in the desktop checkout, as a CompletedProcess with text output. It goes through
    bash when this machine is the desktop box and over ssh otherwise. Raises Unreachable, naming
    the box, when the command cannot start or runs past timeout seconds."""
    host = host or desktop_host()
    command = f"cd {shlex.quote(repo or desktop_repo())} && " + " ".join(shlex.quote(str(a)) for a in argv)
    if socket.gethostname() == host:
        full = ["bash", "-c", command]
    else:
        full = ["ssh", "-o", f"ConnectTimeout={connect}", "-o", "BatchMode=yes", host, command]
    try:
        return (run or subprocess.run)(full, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unreachable(f"could not reach {host}: {error}") from error


def last_json(text):
    """The last line of text that holds a JSON object, or "" when none does."""
    lines = [line for line in str(text or "").splitlines() if line.strip().startswith("{")]
    return lines[-1] if lines else ""
