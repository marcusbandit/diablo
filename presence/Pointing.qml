import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "marks.js" as Mk

Scope {
    id: pointing

    required property string stateDir
    required property var style
    property point cursor: Qt.point(-1, -1)
    property var locateOrb: function () {
        return null;
    }

    property var show: null
    property var play: ({ show: "", index: -1, over: true, need: -1 })
    property var resolved: ({})
    property int resolving: -1
    property bool loadedOnce: false
    readonly property bool running: !!pointing.show && !pointing.play.over
    readonly property var current: pointing.running && pointing.play.index >= 0 ? pointing.beatFor(pointing.play.index) : null
    readonly property bool wantsCursor: pointing.running && !!pointing.current && pointing.current.wait === "click"
    readonly property bool drawing: beatModel.count > 0
    readonly property string repo: `${Quickshell.shellDir}/..`

    function beatFor(n: int): var {
        const beat = Mk.beatAt(pointing.show, n);
        if (!beat)
            return null;
        return beat.lazy ? (pointing.resolved[n] ?? null) : beat;
    }

    function screens(): var {
        return Quickshell.screens.map(s => ({ x: s.x, y: s.y, w: s.width, h: s.height }));
    }

    function load(text: string) {
        let body = null;
        try {
            body = JSON.parse(text);
        } catch (e) {
            return;
        }
        if (!body || typeof body !== "object")
            return;
        const first = !pointing.loadedOnce;
        pointing.loadedOnce = true;
        const last = Math.max(body.at ?? 0, ...((body.beats ?? []).map(b => b.at ?? 0)));
        if (body.show !== (pointing.show?.show ?? "")) {
            pointing.resolved = {};
            pointing.resolving = -1;
            resolver.running = false;
        }
        pointing.show = body;
        if (body.cleared || (first && Date.now() / 1000 - last > 30)) {
            pointing.play = { show: body.show ?? "", index: -1, over: true, need: -1 };
            pointing.retire();
            return;
        }
        pointing.tick();
    }

    function tick() {
        const before = pointing.play;
        const next = Mk.advance(pointing.show, before, Date.now(), { x: pointing.cursor.x, y: pointing.cursor.y }, pointing.resolved,
            { cps: pointing.show?.cps ?? 15, lingerMs: pointing.show?.linger ? pointing.show.linger * 1000 : undefined });
        pointing.play = next;
        if (next.over) {
            pointing.retire();
            return;
        }
        if (next.index !== before.index || next.show !== before.show)
            pointing.present(next.show, next.index);
        if (next.need >= 0 && pointing.resolving < 0)
            pointing.resolve(next.need);
    }

    function present(showId: string, n: int) {
        const beat = pointing.beatFor(n);
        if (!beat)
            return;
        const keep = Mk.keepsEarlier(beat);
        pointing.retire(keep ? showId : "");
        const orb = pointing.locateOrb();
        const cursor = pointing.cursor.x >= 0 ? { x: pointing.cursor.x, y: pointing.cursor.y } : null;
        const marks = Mk.stage(Mk.withCaption(beat), pointing.screens(), orb, cursor);
        beatModel.append({ key: `${showId}:${n}`, show: showId, json: JSON.stringify(marks), live: true, keep: keep });
    }

    function retire(except) {
        let gone = false;
        for (let i = 0; i < beatModel.count; i++) {
            if (except && beatModel.get(i).show === except)
                continue;
            beatModel.setProperty(i, "live", false);
            gone = true;
        }
        if (gone)
            sweep.restart();
    }

    function resolve(n: int) {
        const beat = Mk.beatAt(pointing.show, n);
        if (!beat)
            return;
        pointing.resolving = n;
        resolver.showId = pointing.show.show;
        resolver.command = [`${pointing.repo}/.venv-voice/bin/python`, "-m", "hub.pointing", "resolve", JSON.stringify(beat)];
        resolver.running = true;
        resolveTimeout.restart();
    }

    function settle(n: int, beat: var) {
        if (n < 0 || resolver.showId !== (pointing.show?.show ?? ""))
            return;
        const next = Object.assign({}, pointing.resolved);
        next[n] = beat;
        pointing.resolved = next;
        pointing.resolving = -1;
        pointing.tick();
    }

    ListModel {
        id: beatModel
    }

    Timer {
        id: sweep

        interval: 600
        onTriggered: {
            for (let i = beatModel.count - 1; i >= 0; i--) {
                if (!beatModel.get(i).live)
                    beatModel.remove(i);
            }
        }
    }

    Timer {
        interval: 100
        repeat: true
        running: pointing.running
        onTriggered: pointing.tick()
    }

    Process {
        id: resolver

        property string showId: ""

        workingDirectory: pointing.repo
        stdout: SplitParser {
            onRead: line => {
                let got = null;
                try {
                    got = JSON.parse(line);
                } catch (e) {
                    return;
                }
                if (got && got.beat) {
                    resolveTimeout.stop();
                    pointing.settle(pointing.resolving, got.beat);
                }
            }
        }
        onExited: pointing.giveUp()
    }

    function giveUp() {
        resolveTimeout.stop();
        if (pointing.resolving < 0)
            return;
        const beat = Mk.beatAt(pointing.show, pointing.resolving) ?? {};
        pointing.settle(pointing.resolving, Object.assign({}, beat, { lazy: false, marks: [] }));
    }

    Timer {
        id: resolveTimeout

        interval: 12000
        onTriggered: {
            resolver.running = false;
            pointing.giveUp();
        }
    }

    FileView {
        id: marksFile

        property bool seen: false

        path: pointing.stateDir + "/marks.json"
        watchChanges: true
        printErrors: false
        onFileChanged: marksFile.reload()
        onLoaded: {
            marksFile.seen = true;
            pointing.load(marksFile.text());
        }
    }

    Timer {
        interval: 2000
        repeat: true
        running: !marksFile.seen
        onTriggered: marksFile.reload()
    }

    Variants {
        model: Quickshell.screens

        PanelWindow {
            id: overlay

            required property var modelData
            readonly property var area: ({ x: overlay.modelData.x, y: overlay.modelData.y, w: overlay.modelData.width, h: overlay.modelData.height })

            screen: overlay.modelData
            visible: pointing.drawing
            color: "transparent"
            exclusionMode: ExclusionMode.Ignore
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            WlrLayershell.namespace: "diablo-marks"
            mask: Region {}

            anchors {
                top: true
                bottom: true
                left: true
                right: true
            }

            Repeater {
                model: beatModel

                Marks {
                    required property string json
                    required property bool live
                    required property bool keep

                    anchors.fill: parent
                    style: pointing.style
                    shown: live
                    stagger: keep ? 300 : 140
                    marks: Mk.toLocal(JSON.parse(json), overlay.area)
                }
            }
        }
    }
}
