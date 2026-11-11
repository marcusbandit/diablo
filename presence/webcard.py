#!/usr/bin/env python3
"""The web view for HTML cards: a layer-shell window with WebKit in it, drawn over the spot
shell.qml keeps free in a card or the central card. Quickshell cannot host Qt WebEngine (it
crashes on start), so the page lives in this small GTK process instead.

shell.qml runs it and writes one JSON line per change on stdin:
    {"page": "/path/to/page.html", "x": 0, "y": 0, "w": 300, "h": 200, "monitor": "HDMI-A-1"}
    {"page": ""}                                   hide it
x and y are in the monitor's own coordinates. The page loads as a string with no base URL, so
it reaches no local file; scripts and https resources work.

Needs gtk4, webkitgtk-6.0 and gtk4-layer-shell, which must load before libwayland:
    LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 webcard.py
With --snapshot PAGE OUT.png it renders PAGE once at 900x600 and saves a PNG, for tests.
"""

import json
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell as Layer, WebKit  # noqa: E402

def settings():
    s = WebKit.Settings()
    s.set_enable_javascript(True)
    s.set_allow_file_access_from_file_urls(False)
    s.set_allow_universal_access_from_file_urls(False)
    s.set_enable_developer_extras(False)
    s.set_enable_webgl(True)
    s.set_hardware_acceleration_policy(WebKit.HardwareAccelerationPolicy.ALWAYS)
    s.set_javascript_can_open_windows_automatically(False)
    return s


def read_page(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as error:
        return f"<body style='font:14px sans-serif;color:#ddd'>The page could not be read: {error}</body>"


class Card:
    def __init__(self, app):
        self.app = app
        self.window = Gtk.Window(application=app)
        Layer.init_for_window(self.window)
        Layer.set_layer(self.window, Layer.Layer.OVERLAY)
        Layer.set_namespace(self.window, "diablo-web")
        Layer.set_anchor(self.window, Layer.Edge.TOP, True)
        Layer.set_anchor(self.window, Layer.Edge.LEFT, True)
        Layer.set_exclusive_zone(self.window, -1)
        Layer.set_keyboard_mode(self.window, Layer.KeyboardMode.ON_DEMAND)
        self.view = WebKit.WebView(settings=settings())
        clear = Gdk.RGBA()
        clear.parse("rgba(0,0,0,0)")
        self.view.set_background_color(clear)
        self.view.set_overflow(Gtk.Overflow.HIDDEN)
        # Links open in the browser, never in the card.
        self.view.connect("decide-policy", self.decide)
        # No CSS provider and no fade from opacity 0: either keeps the layer surface from ever
        # mapping on Hyprland. The view's own clear background is enough.
        self.window.set_child(self.view)
        self.page = ""
        self.geometry = None

    def decide(self, view, decision, kind):
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri() or ""
            if action.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED and uri.startswith("https://"):
                Gtk.UriLauncher.new(uri).launch(None, None, None, None)
                decision.ignore()
                return True
        if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    def monitor(self, name):
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            m = monitors.get_item(i)
            if m.get_connector() == name:
                return m
        return None

    def apply(self, msg):
        page = str(msg.get("page") or "")
        if not page:
            self.page = ""
            self.window.set_visible(False)
            return
        geometry = tuple(int(round(float(msg.get(k) or 0))) for k in ("x", "y", "w", "h"))
        if geometry[2] < 8 or geometry[3] < 8:
            return
        if geometry != self.geometry:
            self.geometry = geometry
            x, y, w, h = geometry
            Layer.set_margin(self.window, Layer.Edge.LEFT, x)
            Layer.set_margin(self.window, Layer.Edge.TOP, y)
            self.view.set_size_request(w, h)
            self.window.set_default_size(w, h)
        monitor = self.monitor(str(msg.get("monitor") or ""))
        if monitor is not None:
            Layer.set_monitor(self.window, monitor)
        if page != self.page:
            self.page = page
            self.view.load_html(read_page(page), None)
        if not self.window.get_visible():
            self.window.present()


def listen(card):
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict):
            GLib.idle_add(card.apply, msg)
    GLib.idle_add(card.app.quit)


def snapshot(page, out):
    """Renders a page once and saves what WebKit drew: the page itself, no compositor needed."""

    def activate(app):
        window = Gtk.Window(application=app)
        Layer.init_for_window(window)
        Layer.set_layer(window, Layer.Layer.BACKGROUND)
        Layer.set_namespace(window, "diablo-web-test")
        view = WebKit.WebView(settings=settings())
        view.set_size_request(900, 600)
        window.set_child(view)
        window.present()

        def save(*_):
            def done(v, result):
                try:
                    texture = v.get_snapshot_finish(result)
                    texture.save_to_png(out)
                    print("saved", out)
                except GLib.Error as error:
                    print("snapshot failed:", error.message, file=sys.stderr)
                app.quit()

            view.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, done)
            return False

        def loaded(v, event):
            if event == WebKit.LoadEvent.FINISHED:
                GLib.timeout_add(2500, save)

        view.connect("load-changed", loaded)
        view.load_html(read_page(page), None)
        GLib.timeout_add(20000, app.quit)

    app = Gtk.Application(application_id="dev.diablo.webcard.snapshot", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate)
    app.run([])


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--snapshot":
        snapshot(sys.argv[2], sys.argv[3])
        return

    def activate(app):
        app.hold()
        card = Card(app)
        threading.Thread(target=listen, args=(card,), daemon=True).start()

    app = Gtk.Application(application_id="dev.diablo.webcard", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate)
    app.run([])


if __name__ == "__main__":
    main()
