import logging
import socket
import subprocess

log = logging.getLogger("diablo.fleet")

FLEET = ["banditbox", "homehub", "diablo-pi", "kangaeru", "nemurenai"]
LOCAL = {socket.gethostname().split(".")[0]}

def _local_hosts():
    """Hosts beyond the house fleet and a note for each, from this box's local.env:
    DIABLO_FLEET_EXTRA=host1,host2 and DIABLO_FLEET_NOTES=host1=a note;host2=another."""
    from hub import localenv

    extra = [h.strip() for h in localenv.get("DIABLO_FLEET_EXTRA").split(",") if h.strip()]
    notes = {}
    for item in localenv.get("DIABLO_FLEET_NOTES").split(";"):
        host, _, note = item.partition("=")
        if host.strip() and note.strip():
            notes[host.strip()] = note.strip()
    return extra, notes


EXTRA, NOTES = _local_hosts()
FLEET += [h for h in EXTRA if h not in FLEET]


def _check(host):
    if host not in FLEET:
        return f"unknown host {host}; fleet is {', '.join(FLEET)}"
    if host in LOCAL:
        return "that host runs this very agent, use run_shell instead"
    return None


def fleet_run(host, command, timeout=60):
    reject = _check(host)
    if reject:
        return reject
    try:
        done = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", host, command],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    except OSError as error:
        return f"ssh failed: {error}"
    out = (done.stdout + done.stderr).strip()
    if NOTES.get(host):
        out += f"\n(note: {NOTES[host]})"
    return f"exit {done.returncode}\n{out[:4000] or '(no output)'}"


def fleet_fetch(host, remote_path, local_path="/tmp/opencode"):
    reject = _check(host)
    if reject:
        return reject
    try:
        done = subprocess.run(
            ["scp", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", f"{host}:{remote_path}", local_path],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "scp timed out after 60s"
    except OSError as error:
        return f"scp failed: {error}"
    if done.returncode != 0:
        return f"scp failed: {done.stderr.strip()[:300]}"
    return f"fetched {host}:{remote_path} to {local_path}"


def fleet_put(host, local_path, remote_path):
    reject = _check(host)
    if reject:
        return reject
    try:
        done = subprocess.run(
            ["scp", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", local_path, f"{host}:{remote_path}"],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "scp timed out after 60s"
    except OSError as error:
        return f"scp failed: {error}"
    if done.returncode != 0:
        return f"scp failed: {done.stderr.strip()[:300]}"
    return f"put {local_path} to {host}:{remote_path}"
