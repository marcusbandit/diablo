// Diablo on the screen: an orb that shows what he is doing, and cards for what he answers.
// A card grows out of the orb and stays with it (presence/place.js works out where): the orb is
// up whenever a card is, and the two hold still while the cursor is near them, so a card can be
// read and clicked, then catch up once the cursor moves well away.
// While Marcus is talking to him (listening, thinking, speaking, a voice loop,
// dictation) the orb sits beside the cursor and follows it from monitor to monitor; while he
// sleeps it fades out and its surface goes away, so nothing polls and nothing is drawn. Its own Quickshell instance, so a crash here never touches banditshell:
//     qs -p ~/Projects/diablo/presence      (diablo-presence.service runs this)
// Quickshell reloads these files on save, so Diablo changes his own look by editing them.
//
// It reads two files the ears write:
//     ~/.local/state/diablo/ears.json      his state: asleep, listening, thinking, speaking...
//     ~/.local/state/diablo/presence.json  {"cards": [{id, kind, title, big, lines, at, ttl}]}
// and the hub's running turns, which busy.py next to this file follows on /v1/busy and
// /v1/events. While any turn runs, typed, spoken, from Telegram or a watcher, a smaller orb sits
// by the cursor with what he is doing in a word or two, until the hub says the turn is over.
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "place.js" as Place

ShellRoot {
    id: root

    readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || `${Quickshell.env("HOME")}/.local/state`) + "/diablo"
    property var ears: ({ state: "asleep" })
    property var mouthTrack: ({ start: 0, duration: 0, cues: [] })
    // How far his mouth runs behind his voice, tuned by eye in presence/tuner.qml.
    readonly property string configDir: (Quickshell.env("XDG_CONFIG_HOME") || `${Quickshell.env("HOME")}/.config`) + "/diablo"
    property int mouthDelayMs: 0
    property var cards: []
    property real now: Date.now() / 1000
    property var dismissed: ({})
    // A feeling after something he did, shown for a moment on his face.
    property string moment: ""

    // The setting: false keeps the orb in the bottom right corner of the cursor's monitor.
    // Settings in the central card flip it; it is kept in presence-settings.json.
    property bool followCursor: true

    // The central card (Panel.qml): Diablo opened up in the middle of the screen. panel.json
    // holds {open, view, card}; the ears write it for "open up", "enlarge this" and "close",
    // and this file writes it when the card is closed here.
    property var panelState: ({ open: false, view: "settings", card: null })
    readonly property bool panelOpen: !!root.panelState.open

    function setPanel(changes: var) {
        const next = Object.assign({}, root.panelState, changes, { at: Date.now() / 1000 });
        root.panelState = next;
        panelFile.setText(JSON.stringify(next));
    }

    function setFollow(on: bool) {
        root.followCursor = on;
        settingsFile.setText(JSON.stringify({ follow: on }));
    }

    // For scripts/presence-shots.sh: a fixed cursor ("x,y" in global coordinates) instead of
    // asking Hyprland, and no hub feed, so a test instance never moves or reads anything real.
    readonly property string fixedCursor: Quickshell.env("DIABLO_PRESENCE_CURSOR") ?? ""
    readonly property bool testRun: (Quickshell.env("DIABLO_PRESENCE_TEST") ?? "") !== ""

    // Awake is only what Marcus started by talking to him: a turn he is listening to, thinking
    // on or answering, a voice loop, dictation. Everything else stays hidden: "waking" comes
    // before the wake is checked, so a refused one would flash him up, and answers still
    // thinking in the background or a passing moment are not Marcus talking to him.
    readonly property bool awake: ["listening", "thinking", "speaking"].includes(root.ears.state ?? "asleep")
        || !!root.ears.loop || !!root.ears.dictation

    // The hub's running turns, oldest first, as busy.py last printed them. A line comes at least
    // every ten seconds while the link holds; a feed silent for hubStaleMs counts as gone.
    property var hubTurns: []
    property real hubSeen: 0
    property real lastBusyAt: 0
    property real clock: Date.now()
    readonly property int hubStaleMs: 35000
    readonly property bool hubBusy: root.hubTurns.length > 0
    // An answer the ears hold in the background keeps him busy only while the hub was busy a
    // moment ago, so a count left behind (#215) can never hold the orb up on its own.
    readonly property bool earsBehind: (root.ears.thinking ?? 0) > 0 && root.clock - root.lastBusyAt < 15000
    readonly property bool busy: root.hubBusy || root.earsBehind
    readonly property bool up: root.awake || root.busy || root.cardsUp || root.panelOpen
    readonly property string busyWords: {
        const turns = root.hubTurns;
        if (turns.length === 0)
            return "working";
        const words = turns[turns.length - 1].words || "thinking";
        return turns.length > 1 ? `${words} +${turns.length - 1}` : words;
    }

    function hubLine(line: string) {
        const got = root.parse(line, null);
        if (!got)
            return;
        root.hubSeen = Date.now();
        root.clock = root.hubSeen;
        if (got.turns === undefined)
            return;
        if (root.hubTurns.length > 0 || got.turns.length > 0)
            root.lastBusyAt = root.hubSeen;
        root.hubTurns = got.turns;
    }

    Process {
        id: hubFeed

        command: ["python3", "-I", Quickshell.shellPath("busy.py")]
        running: !root.testRun
        stdout: SplitParser {
            onRead: line => root.hubLine(line)
        }
        onExited: {
            root.hubTurns = [];
            if (!root.testRun)
                feedAgain.restart();
        }
    }

    Timer {
        id: feedAgain

        interval: 3000
        onTriggered: hubFeed.running = true
    }

    // The failsafe: while he looks busy the clock ticks, and a feed that went quiet clears it.
    Timer {
        interval: 2000
        repeat: true
        running: root.hubBusy || root.earsBehind
        onTriggered: {
            root.clock = Date.now();
            if (root.hubBusy && root.clock - root.hubSeen > root.hubStaleMs)
                root.hubTurns = [];
        }
    }

    // The cursor in Hyprland's global layout coordinates, the same space the screens' x and y use.
    property point cursor: Qt.point(-1, -1)
    // Set on waking: the cursor kept above is from the last wake until Hyprland answers again.
    property bool freshWake: false
    readonly property var cursorScreen: root.screenAt(root.cursor)

    function screenAt(p: point): var {
        const all = Quickshell.screens;
        for (const s of all) {
            if (p.x >= s.x && p.x < s.x + s.width && p.y >= s.y && p.y < s.y + s.height)
                return s;
        }
        return all.length > 0 ? all[0] : null;
    }

    onEarsChanged: {
        const m = root.ears.moment;
        if (m && m.mood && Date.now() / 1000 - (m.at ?? 0) < 2.5) {
            root.moment = m.mood;
            momentTimer.restart();
        }
    }

    Timer {
        id: momentTimer

        interval: 1800
        onTriggered: root.moment = ""
    }

    readonly property var shown: root.cards.filter(c => c && !root.dismissed[c.id] && root.now < (c.at ?? 0) + (c.ttl ?? 10))

    // The cards on screen, kept by id: `shown` is a new array on every clock tick, and a
    // Repeater over it would rebuild every card each time, restarting a playing video.
    ListModel {
        id: cardModel
    }

    onShownChanged: {
        const ids = root.shown.map(c => c.id);
        for (let i = cardModel.count - 1; i >= 0; i--) {
            if (!ids.includes(cardModel.get(i).cid))
                cardModel.remove(i);
        }
        const have = [];
        for (let i = 0; i < cardModel.count; i++)
            have.push(cardModel.get(i).cid);
        for (const c of root.shown) {
            if (!have.includes(c.id))
                cardModel.append({ cid: c.id, json: JSON.stringify(c) });
        }
    }

    // The orb's geometry beside the cursor.
    readonly property real orbGap: 26
    readonly property real orbEdge: 12
    readonly property real orbHeight: 100

    readonly property bool cardsUp: root.shown.length > 0

    function parse(text: string, fallback: var): var {
        try {
            return JSON.parse(text);
        } catch (e) {
            return fallback;
        }
    }

    Style {
        id: theme
    }

    // The web view for HTML pages (webcard.py, its own GTK process: Quickshell cannot host
    // Qt WebEngine). It starts with the first page and stays; it gets one JSON line per change:
    // which page, and where the spot kept free for it is, in the monitor's coordinates.
    Process {
        id: webview

        command: ["env", "LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so", "python3", Quickshell.shellPath("webcard.py")]
        stdinEnabled: true
        onExited: root.webSent = ""
    }

    property string webSent: ""

    // The spot for the page that should show now: the central card's, else the newest card
    // with a page, and only once it has finished growing, so the view never chases a scaling card.
    function webTarget(): var {
        if (!follow.visible)
            return null;
        if (root.panelOpen) {
            const card = root.panelState.card;
            if (root.panelState.view !== "now" || !card?.page || panelGrow.xScale < 0.999)
                return null;
            return { page: card.page, item: panel.pageArea };
        }
        if (!cards.showing || grow.xScale < 0.999)
            return null;
        for (let i = cardColumn.children.length - 1; i >= 0; i--) {
            const c = cardColumn.children[i];
            if (c && c.page && c.visible && c.scale > 0.999)
                return { page: c.page, item: c.pageSlot };
        }
        return null;
    }

    Timer {
        interval: 50
        repeat: true
        running: root.up || root.webSent !== ""
        onTriggered: {
            const t = root.webTarget();
            let msg = { page: "" };
            if (t && t.item && t.item.width > 0) {
                const p = t.item.mapToItem(null, 0, 0);
                msg = { page: t.page, x: Math.round(p.x), y: Math.round(p.y), w: Math.round(t.item.width), h: Math.round(t.item.height),
                    monitor: follow.screen?.name ?? "" };
            }
            const line = JSON.stringify(msg);
            if (line === root.webSent || (msg.page === "" && root.webSent === ""))
                return;
            // Started on this tick, written on the next one, once it reads its stdin.
            if (msg.page !== "" && !webview.running) {
                webview.running = true;
                return;
            }
            if (webview.running)
                webview.write(line + "\n");
            root.webSent = msg.page === "" ? "" : line;
        }
    }

    FileView {
        id: earsFile

        property bool seen: false

        path: root.stateDir + "/ears.json"
        watchChanges: true
        printErrors: false
        onFileChanged: earsFile.reload()
        onLoaded: {
            earsFile.seen = true;
            root.ears = root.parse(earsFile.text(), root.ears);
        }
    }

    // The mouth while he speaks: voice/mouth.py writes when the sound starts and which shape
    // shows at each moment, measured from the clip itself.
    FileView {
        id: mouthFile

        property bool seen: false

        path: root.stateDir + "/mouth.json"
        watchChanges: true
        printErrors: false
        onFileChanged: mouthFile.reload()
        onLoaded: {
            mouthFile.seen = true;
            root.mouthTrack = root.parse(mouthFile.text(), root.mouthTrack);
        }
    }

    // The saved mouth delay, ~/.config/diablo/lipsync.json, read again whenever the tuner saves.
    FileView {
        id: lipsyncFile

        property bool seen: false

        path: root.configDir + "/lipsync.json"
        watchChanges: true
        printErrors: false
        onFileChanged: lipsyncFile.reload()
        onLoaded: {
            lipsyncFile.seen = true;
            const ms = Number(root.parse(lipsyncFile.text(), { delay_ms: 0 }).delay_ms);
            root.mouthDelayMs = isFinite(ms) ? Math.max(-400, Math.min(400, Math.round(ms))) : 0;
        }
    }

    FileView {
        id: panelFile

        property bool seen: false

        path: root.stateDir + "/panel.json"
        watchChanges: true
        printErrors: false
        onFileChanged: panelFile.reload()
        onLoaded: {
            panelFile.seen = true;
            root.panelState = root.parse(panelFile.text(), root.panelState);
        }
    }

    FileView {
        id: settingsFile

        property bool seen: false

        path: root.stateDir + "/presence-settings.json"
        printErrors: false
        onLoaded: {
            settingsFile.seen = true;
            root.followCursor = root.parse(settingsFile.text(), { follow: true }).follow !== false;
        }
    }

    FileView {
        id: presenceFile

        property bool seen: false

        path: root.stateDir + "/presence.json"
        watchChanges: true
        printErrors: false
        onFileChanged: presenceFile.reload()
        onLoaded: {
            presenceFile.seen = true;
            root.now = Date.now() / 1000;
            root.cards = root.parse(presenceFile.text(), { cards: [] }).cards ?? [];
        }
    }

    // A state file that is not there when the shell starts gets no watch, so on a fresh box the
    // ears' first state, a card or the central card would never be read. Ask again until each loads.
    readonly property var stateFiles: [earsFile, mouthFile, lipsyncFile, panelFile, settingsFile, presenceFile]

    Timer {
        interval: 2000
        repeat: true
        running: root.stateFiles.some(f => !f.seen)
        onTriggered: root.stateFiles.forEach(f => {
            if (!f.seen)
                f.reload();
        })
    }

    // Cards fade on their own; the clock only ticks while one is up.
    Timer {
        interval: 500
        repeat: true
        running: root.shown.length > 0
        onTriggered: {
            if (!cardArea.containsMouse)
                root.now = Date.now() / 1000;
        }
    }

    // Where the cursor is, asked over Hyprland's own socket: one short request per tick, no
    // process spawned. Hyprland answers "x, y" and hangs up, so each tick connects again. The
    // ticks only run while the orb is up; following asks 30 times a second, the corner only
    // needs the monitor and asks twice.
    Socket {
        id: hypr

        path: `${Quickshell.env("XDG_RUNTIME_DIR")}/hypr/${Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE")}/.socket.sock`
        onConnectedChanged: {
            if (hypr.connected) {
                hypr.write("cursorpos");
                hypr.flush();
            }
        }
        parser: SplitParser {
            splitMarker: ""
            onRead: data => {
                const m = /(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/.exec(data);
                if (m) {
                    root.cursor = Qt.point(Number(m[1]), Number(m[2]));
                    // The first answer after waking is where he is now; start there.
                    if (root.freshWake) {
                        root.freshWake = false;
                        follow.snap();
                    }
                }
                // Hang up first, before Hyprland does, which the socket would log as an error.
                hypr.connected = false;
            }
        }
    }

    Timer {
        interval: root.followCursor ? 33 : 500
        repeat: true
        triggeredOnStart: true
        running: root.up
        onTriggered: {
            if (root.fixedCursor !== "") {
                const xy = root.fixedCursor.split(",");
                root.cursor = Qt.point(Number(xy[0]), Number(xy[1]));
                if (root.freshWake) {
                    root.freshWake = false;
                    follow.snap();
                }
                return;
            }
            if (!hypr.connected)
                hypr.connected = true;
        }
    }

    // The orb's own layer: the whole of the cursor's monitor, drawn on but never touched. An
    // empty mask hands every click and hover through to whatever is under it, and it never
    // takes the keyboard. It exists only while the orb is awake or still fading out.
    PanelWindow {
        id: follow

        // The point the orb sits beside: the cursor, except while cards are up, when it holds
        // where it was until the cursor is well clear of the orb and its cards.
        property point anchor: root.cursor

        screen: root.screenAt(follow.anchor)
        visible: root.up || group.opacity > 0.01
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.panelOpen ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        WlrLayershell.namespace: "diablo-orb"
        // Only the cards take the pointer; the orb and the rest of the screen pass it through.
        // With the central card open the whole screen takes it, so a click outside closes it.
        mask: Region {
            item: root.panelOpen ? outside : cards
        }

        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }

        // Beside the cursor, below and to the right so it never covers what he points at; near
        // a right or bottom edge it swaps to the other side, and it always stays on the screen.
        readonly property real gap: root.orbGap
        readonly property real edge: root.orbEdge
        readonly property real localX: follow.anchor.x - (follow.screen?.x ?? 0)
        readonly property real localY: follow.anchor.y - (follow.screen?.y ?? 0)
        readonly property bool flipX: root.followCursor && follow.localX + follow.gap + group.width > follow.width - follow.edge
        readonly property bool flipY: root.followCursor && follow.localY + follow.gap + group.height > follow.height - follow.edge
        readonly property var panelSpot: Place.panelRect({ w: follow.width, h: follow.height }, follow.edge)
        readonly property real targetX: root.panelOpen ? follow.panelSpot.x + 18 : Math.max(follow.edge, Math.min(follow.width - group.width - follow.edge,
            !root.followCursor ? follow.width - group.width - 22
            : follow.flipX ? follow.localX - follow.gap - group.width : follow.localX + follow.gap))
        readonly property real targetY: root.panelOpen ? follow.panelSpot.y + 4 : Math.max(follow.edge, Math.min(follow.height - group.height - follow.edge,
            !root.followCursor ? follow.height - group.height - 22
            : follow.flipY ? follow.localY - follow.gap - group.height : follow.localY + follow.gap))

        // A fresh appearance, or a jump to another monitor, starts where he is, not where the
        // orb last was.
        function snap() {
            follow.anchor = root.cursor;
            group.x = follow.targetX;
            group.y = follow.targetY;
        }

        Connections {
            target: root

            // The last card gone: back beside the cursor, even if it has not moved since.
            function onCardsUpChanged() {
                if (!root.cardsUp)
                    follow.anchor = root.cursor;
            }

            function onCursorChanged() {
                if (root.panelOpen)
                    return;
                if (!root.cardsUp) {
                    follow.anchor = root.cursor;
                    return;
                }
                const sx = follow.screen?.x ?? 0, sy = follow.screen?.y ?? 0;
                const here = { x: root.cursor.x - sx, y: root.cursor.y - sy };
                const rects = [{ x: group.x, y: group.y, w: group.width, h: group.height },
                    { x: cards.x, y: cards.y, w: cards.width, h: cards.height }];
                if (Place.shouldRejoin(here, rects))
                    follow.anchor = root.cursor;
            }
        }

        onScreenChanged: follow.snap()
        onWidthChanged: if (!root.up || group.opacity < 0.05) follow.snap()

        Connections {
            target: root

            function onUpChanged() {
                if (root.up && group.opacity < 0.05) {
                    root.freshWake = true;
                    follow.snap();
                }
            }
        }

        // The glide: each frame closes a share of the distance, so the orb trails the cursor
        // smoothly and settles in about a fifth of a second. It stops once there.
        FrameAnimation {
            running: follow.visible && (Math.abs(group.x - follow.targetX) > 0.5 || Math.abs(group.y - follow.targetY) > 0.5)
            onTriggered: {
                const k = 1 - Math.exp(-frameTime * 16);
                group.x += (follow.targetX - group.x) * k;
                group.y += (follow.targetY - group.y) * k;
            }
        }

        Item {
            id: group

            width: orbBox.width + (pill.visible ? 8 + pill.width : 0)
            height: orbBox.height
            opacity: root.up ? 1 : 0
            scale: root.up ? 1 : 0.6
            transformOrigin: follow.flipX ? Item.Right : Item.Left

            Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutBack } }

            // The orb sits nearest the cursor, its words on the far side.
            Item {
                id: orbBox

                x: follow.flipX ? group.width - width : 0
                // Talking to him gets the big orb; busy on his own, a smaller one.
                width: root.awake ? 100 : 66
                height: root.awake ? root.orbHeight : 66

                Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

                Orb {
                    id: orb

                    anchors.centerIn: parent
                    scale: root.awake ? 1.35 : 0.9
                    visible: follow.visible
                    style: theme
                    mood: root.awake ? (root.ears.state ?? "asleep") : "asleep"
                    control: !!root.ears.loop
                    dictating: !!root.ears.dictation
                    thinkingBehind: root.busy
                    presenting: (root.cardsUp || root.panelOpen) && !root.awake && !root.busy
                    // He looks at the card he holds out: which side of him its middle is on.
                    cardSide: !cards.showing ? "" : (cards.x + cards.width / 2 < group.x + orbBox.width / 2 ? "left" : "right")
                    moment: root.moment
                    mouthTrack: root.mouthTrack
                    mouthDelay: root.mouthDelayMs / 1000
                    facesDir: `${Quickshell.shellDir}/../assets/faces`

                    Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                }
            }

            // What he is doing, in words, so the colours never need learning.
            Rectangle {
                id: pill

                // Words only while he is doing something; holding out a card says it already.
                visible: (root.awake || root.busy || root.moment !== "") && !root.panelOpen
                x: follow.flipX ? 0 : orbBox.width + 8
                anchors.verticalCenter: orbBox.verticalCenter
                width: label.implicitWidth + 24 + (dots.visible ? dots.width + 6 : 0)
                height: 30
                radius: 15
                color: Qt.rgba(0.06, 0.07, 0.08, 0.92)
                border.width: 1
                border.color: Qt.rgba(orb.hue.r, orb.hue.g, orb.hue.b, 0.6)

                Behavior on border.color { ColorAnimation { duration: 200 } }

                // Busy: thinking, a tool step, an answer held back, until the turn is over.
                readonly property bool working: root.moment === "" && !orb.dictating
                    && (orb.mood === "thinking" || (orb.mood !== "speaking" && orb.mood !== "listening" && root.busy))

                Text {
                    id: label

                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    color: theme.text
                    font.family: theme.family
                    font.pixelSize: theme.normal
                    text: root.moment !== "" ? root.moment
                        : orb.dictating ? "dictating"
                        : orb.mood === "thinking" && root.ears.step ? root.ears.step
                        : orb.mood === "thinking" && root.hubBusy ? root.busyWords
                        : orb.mood === "thinking" ? "thinking"
                        : orb.control && (orb.mood === "listening" || orb.mood === "asleep") && !root.busy ? "speech control"
                        : orb.mood === "asleep" && root.busy ? root.busyWords
                        : orb.mood === "waking" ? "listening"
                        : orb.mood
                }

                // Three dots that take turns, so a long step never looks like a stall.
                Row {
                    id: dots

                    property int lit: 0

                    x: label.x + label.implicitWidth + 6
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 3
                    visible: pill.working

                    Repeater {
                        model: 3

                        Rectangle {
                            required property int index

                            width: 4
                            height: 4
                            radius: 2
                            color: orb.hue
                            opacity: dots.lit === index ? 1 : 0.3

                            Behavior on opacity { NumberAnimation { duration: 160 } }
                        }
                    }

                    Timer {
                        interval: 320
                        repeat: true
                        running: dots.visible && follow.visible
                        onTriggered: dots.lit = (dots.lit + 1) % 3
                    }
                }
            }
        }

        // Behind the central card: a click anywhere else closes it.
        MouseArea {
            id: outside

            anchors.fill: parent
            z: -2
            enabled: root.panelOpen
            onClicked: root.setPanel({ open: false })
        }

        // The central card, grown out of the orb: it scales up from where he was and he moves
        // into its top left corner. Escape closes it.
        Panel {
            id: panel

            readonly property var spot: follow.panelSpot

            x: panel.spot.x
            y: panel.spot.y
            width: panel.spot.w
            height: panel.spot.h
            z: -1
            style: theme
            visible: root.panelOpen || panelGrow.xScale > 0.05
            opacity: root.panelOpen ? 1 : 0
            view: root.panelState.view ?? "settings"
            card: root.panelState.card ?? null
            followCursor: root.followCursor
            earsUrl: Quickshell.env("DIABLO_EARS_URL") || "http://127.0.0.1:8758"
            focus: root.panelOpen
            Keys.onEscapePressed: root.setPanel({ open: false })
            onCloseRequested: root.setPanel({ open: false })
            onViewRequested: view => root.setPanel({ view: view })
            onFollowChanged: on => root.setFollow(on)
            mouthDelayMs: root.mouthDelayMs
            onTuneMouthRequested: {
                root.setPanel({ open: false });
                Quickshell.execDetached(["qs", "-n", "-p", `${Quickshell.shellDir}/tuner.qml`]);
            }

            Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

            transform: Scale {
                id: panelGrow

                origin.x: group.x + orbBox.width / 2 - panel.x
                origin.y: group.y + orbBox.height / 2 - panel.y
                xScale: root.panelOpen ? 1 : 0.05
                yScale: root.panelOpen ? 1 : 0.05

                Behavior on xScale { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
                Behavior on yScale { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
            }

            // Clicks on the card itself stay in it.
            MouseArea {
                anchors.fill: parent
                z: -1
                onClicked: {}
            }
        }

        // The cards, grown out of the orb: placed by Place.cardRect from where the orb is now,
        // scaled up from the point nearest it, and carried along when the orb moves.
        Item {
            id: cards

            readonly property var spot: Place.cardRect({ x: group.x, y: group.y, w: group.width, h: group.height },
                { w: cards.width, h: Math.max(1, cardColumn.implicitHeight) }, { w: follow.width, h: follow.height },
                follow.edge, 10)

            x: cards.spot.x
            y: cards.spot.y
            width: 300
            height: cardColumn.implicitHeight
            readonly property bool showing: root.cardsUp && !root.panelOpen

            visible: cards.showing || grow.xScale > 0.05
            opacity: cards.showing ? 1 : 0

            Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

            transform: Scale {
                id: grow

                origin.x: cards.spot.originX
                origin.y: cards.spot.originY
                xScale: cards.showing ? 1 : 0.15
                yScale: cards.showing ? 1 : 0.15

                Behavior on xScale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
                Behavior on yScale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
            }

            HoverHandler {
                id: cardArea

                readonly property bool containsMouse: hovered
            }

            Column {
                id: cardColumn

                width: parent.width
                spacing: 10

                Repeater {
                    model: cardModel

                    Card {
                        required property string json

                        style: theme
                        info: JSON.parse(json)
                        // A card after the first grows out of the stack the same way.
                        transformOrigin: cards.spot.below ? Item.Top : Item.Bottom
                        scale: 0.6
                        opacity: 0
                        Component.onCompleted: {
                            scale = 1;
                            opacity = 1;
                        }

                        Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutBack } }
                        Behavior on opacity { NumberAnimation { duration: 180 } }

                        onDismiss: id => {
                            const next = Object.assign({}, root.dismissed);
                            next[id] = true;
                            root.dismissed = next;
                        }
                    }
                }
            }
        }
    }
}
