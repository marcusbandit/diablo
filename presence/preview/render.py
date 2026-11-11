#!/usr/bin/env python3
"""Renders presence/preview/Preview.qml offscreen, one PNG per scene: no compositor, no window,
nothing real read. Panel scenes get a fake ears API (scripts/fakeears.py) to talk to.

    python3 presence/preview/render.py OUT_DIR [scene...]

Needs PySide6 (Qt 6) and prints every QML warning, so a broken component fails loudly.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENES = ["orb-listening", "orb-thinking", "orb-speaking", "card-below", "card-above", "card-right-edge",
          "two-cards", "panel-settings", "panel-training", "panel-now"]
EARS = {"panel-settings": "trained", "panel-training": "speech", "panel-now": "trained"}
PORT = 18758


def render(scene, out):
    code = (
        "import sys\n"
        "from PySide6.QtGui import QGuiApplication\n"
        "from PySide6.QtQml import QQmlApplicationEngine\n"
        "app = QGuiApplication(sys.argv)\n"
        "engine = QQmlApplicationEngine()\n"
        "bad = []\n"
        "engine.warnings.connect(lambda ws: [bad.append(w.toString()) or print('QML', w.toString(), file=sys.stderr) for w in ws])\n"
        f"engine.load({str(ROOT / 'presence/preview/Preview.qml')!r})\n"
        "if not engine.rootObjects(): sys.exit(2)\n"
        "code = app.exec()\n"
        "sys.exit(3 if bad else code)\n"
    )
    argv = [sys.executable, "-c", code, "--", scene, str(out), str(ROOT / "assets/faces"), f"http://127.0.0.1:{PORT}"]
    # The rhi backend, or the MultiEffect that tints his face draws nothing offscreen.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="rhi")
    return subprocess.run(argv, env=env, timeout=40).returncode


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for scene in sys.argv[2:] or SCENES:
        ears = None
        if scene in EARS:
            ears = subprocess.Popen([sys.executable, str(ROOT / "scripts/fakeears.py"), str(PORT), EARS[scene]],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.4)
        try:
            path = out / f"{scene}.png"
            code = render(scene, path)
        finally:
            if ears:
                ears.terminate()
                ears.wait()
        ok = code == 0 and path.exists()
        failed += not ok
        print(("ok   " if ok else "FAIL ") + str(path))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
