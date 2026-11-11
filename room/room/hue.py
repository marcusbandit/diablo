"""A small Hue bridge client: CLIP v2 over HTTPS for lights and the event stream, v1 for pairing."""

import http.client
import json
import socket
import ssl
import time
import urllib.request

# The bridge serves a certificate from Signify's own CA under its bridge id. Inside the home
# network room talks to the address it was paired with, so the certificate is not checked.
_TLS = ssl.create_default_context()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE


class BridgeError(Exception):
    pass


class LinkButtonNotPressed(BridgeError):
    pass


class Bridge:
    def __init__(self, host, key, timeout=5.0):
        self.host, self.key, self.timeout = host, key, timeout

    def _request(self, method, path, body=None, timeout=None):
        conn = http.client.HTTPSConnection(self.host, timeout=timeout or self.timeout, context=_TLS)
        try:
            headers = {"hue-application-key": self.key}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise BridgeError(f"bridge at {self.host} did not answer: {exc}") from exc
        finally:
            conn.close()
        try:
            out = json.loads(raw or b"{}")
        except ValueError as exc:
            raise BridgeError(f"bridge sent something that is not JSON (HTTP {resp.status})") from exc
        if resp.status == 403 or resp.status == 401:
            raise BridgeError("the bridge refused room's key; run `room setup` to pair again")
        errors = out.get("errors") if isinstance(out, dict) else None
        if errors:
            raise BridgeError("; ".join(e.get("description", str(e)) for e in errors))
        return out

    def get(self, resource):
        return self._request("GET", f"/clip/v2/resource/{resource}").get("data", [])

    def lights(self):
        return self.get("light")

    def put_light(self, light_id, body):
        return self._request("PUT", f"/clip/v2/resource/light/{light_id}", body)

    def identify(self, light_id):
        """The lamp blinks a few times, so you can tell which one a name means."""
        return self.put_light(light_id, {"identify": {"action": "identify"}})

    def bridge_name(self):
        return bridge_config(self.host).get("name") or "Hue bridge"

    def rooms(self):
        """[(room name, {light ids})]. A room lists devices; each device lists its light services."""
        devices = {d["id"]: d for d in self.get("device")}
        out = []
        for room in self.get("room"):
            ids = set()
            for child in room.get("children", []):
                dev = devices.get(child.get("rid")) if child.get("rtype") == "device" else None
                for svc in (dev or {}).get("services", []):
                    if svc.get("rtype") == "light":
                        ids.add(svc["rid"])
            out.append((room.get("metadata", {}).get("name", room["id"]), ids))
        return out

    def events(self, stop=None):
        """Yield each event from /eventstream/clip/v2 until the connection drops."""
        # A quiet room sends nothing for long stretches; TCP keepalive notices a dead link and the
        # long timeout only ends a silent stream, after which the daemon reconnects at once.
        conn = http.client.HTTPSConnection(self.host, timeout=900, context=_TLS)
        try:
            conn.connect()
            conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
                if hasattr(socket, opt):
                    conn.sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), value)
            conn.request("GET", "/eventstream/clip/v2",
                         headers={"hue-application-key": self.key, "Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise BridgeError(f"event stream answered HTTP {resp.status}")
            data = []
            while stop is None or not stop.is_set():
                line = resp.fp.readline()
                if not line:
                    return
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    try:
                        for event in json.loads("".join(data)):
                            yield event
                    except ValueError:
                        pass
                    data = []
        except (OSError, http.client.HTTPException) as exc:
            raise BridgeError(f"event stream dropped: {exc}") from exc
        finally:
            conn.close()


def bridge_config(host, timeout=3.0):
    """The bridge's public config (name, model) from v1, which needs no key."""
    try:
        with urllib.request.urlopen(f"http://{host}/api/0/config", timeout=timeout) as resp:
            return json.loads(resp.read())
    except (OSError, ValueError):
        return {}


def discover(timeout=4.0):
    """Bridge addresses from Philips' discovery service, each one checked by asking for its config."""
    found = []
    try:
        req = urllib.request.Request("https://discovery.meethue.com/", headers={"User-Agent": "room"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for entry in json.loads(resp.read()):
                if entry.get("internalipaddress"):
                    found.append(entry["internalipaddress"])
    except (OSError, ValueError):
        pass
    return [h for h in dict.fromkeys(found) if bridge_config(h)]


def pair(host, devicetype=None):
    """Ask the bridge for a key. Raises LinkButtonNotPressed until the round button was pressed."""
    devicetype = devicetype or f"room#{socket.gethostname()[:19]}"
    body = json.dumps({"devicetype": devicetype, "generateclientkey": True}).encode()
    req = urllib.request.Request(f"http://{host}/api", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            answer = json.loads(resp.read())
    except (OSError, ValueError) as exc:
        raise BridgeError(f"bridge at {host} did not answer: {exc}") from exc
    for item in answer:
        if "success" in item:
            return item["success"]["username"]
        err = item.get("error", {})
        if err.get("type") == 101:
            raise LinkButtonNotPressed("press the round button on top of the bridge")
        raise BridgeError(err.get("description", "pairing failed"))
    raise BridgeError("pairing failed: empty answer")


def wait_for(fn, seconds, every=1.0):
    """Call fn until it stops raising LinkButtonNotPressed or time runs out."""
    end = time.time() + seconds
    while True:
        try:
            return fn()
        except LinkButtonNotPressed:
            if time.time() >= end:
                raise
            time.sleep(every)
