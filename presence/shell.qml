// Diablo on the screen: an orb that shows what he is doing, and cards for what he answers.
// A card opens beside the cursor on the cursor's monitor and stays where it opened; the bottom
// right corner is only the fallback when Hyprland cannot say where the cursor is.
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

ShellRoot {
    id: root

    readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || `${Quickshell.env("HOME")}/.local/state`) + "/diablo"
    property var ears: ({ state: "asleep" })
    property var mouthTrack: ({ start: 0, duration: 0, cues: [] })
    property var cards: []
    property real now: Date.now() / 1000
    property var dismissed: ({})
    // A feeling after something he did, shown for a moment on his face.
    property string moment: ""

    // The setting: false keeps the orb in the bottom right corner of the cursor's monitor.
    // Quickshell reloads on save, so flipping it here takes effect at once.
    readonly property bool followCursor: true

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
    readonly property bool up: root.awake || root.busy
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
        running: true
        stdout: SplitParser {
            onRead: line => root.hubLine(line)
        }
        onExited: {
            root.hubTurns = [];
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
    readonly property var cursorScreen: {
        const all = Quickshell.screens;
        for (const s of all) {
            if (root.cursor.x >= s.x && root.cursor.x < s.x + s.width && root.cursor.y >= s.y && root.cursor.y < s.y + s.height)
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

    // The orb's geometry beside the cursor, shared with the cards so they stay clear of it.
    readonly property real orbGap: 26
    readonly property real orbEdge: 12
    readonly property real orbHeight: 100

    // Where the cards open. A showing starts when the first card goes up and ends when the last
    // one goes away. It asks Hyprland for the cursor once, waits for the answer (or gives up
    // after a moment) and keeps that spot until the showing ends, so a card never walks away
    // from under the pointer while it is read.
    readonly property bool cardsUp: root.shown.length > 0
    property bool cardPending: false
    property bool cardPlaced: false
    property point cardAt: Qt.point(0, 0)
    property var cardScreen: null
    // Fixed at placement, so a card never jumps while it is read: whether the orb sits above the
    // cursor (it flips near the bottom edge) and whether the card goes below the cursor.
    property bool cardOrbUp: false
    property bool cardBelow: false

    onCardsUpChanged: {
        root.cardPlaced = false;
        if (root.cardsUp) {
            root.cardPending = true;
            giveUp.restart();
            hypr.connected = true;
        } else {
            root.cardPending = false;
            giveUp.stop();
        }
    }

    // The cursor answered while a showing was waiting for it.
    function placeCards() {
        giveUp.stop();
        root.cardPending = false;
        root.cardAt = root.cursor;
        root.cardScreen = root.cursorScreen;
        const sh = root.cursorScreen?.height ?? 0;
        const ly = root.cursor.y - (root.cursorScreen?.y ?? 0);
        root.cardOrbUp = ly + root.orbGap + root.orbHeight > sh - root.orbEdge;
        // Above the cursor, past the orb when it is up there; below only when there is no room.
        // The orb may wake after the card is up, so its room is always kept free.
        const need = content.implicitHeight + root.orbGap + (root.cardOrbUp ? root.orbHeight + 10 : 0);
        root.cardBelow = ly - need < root.orbEdge;
        root.cardPlaced = true;
    }

    // No answer from Hyprland: show the card in the corner, as before.
    Timer {
        id: giveUp

        interval: 400
        onTriggered: root.cardPending = false
    }

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

    FileView {
        id: earsFile

        path: root.stateDir + "/ears.json"
        watchChanges: true
        printErrors: false
        onFileChanged: earsFile.reload()
        onLoaded: root.ears = root.parse(earsFile.text(), root.ears)
    }

    // The mouth while he speaks: voice/mouth.py writes when the sound starts and which shape
    // shows at each moment, measured from the clip itself.
    FileView {
        id: mouthFile

        path: root.stateDir + "/mouth.json"
        watchChanges: true
        printErrors: false
        onFileChanged: mouthFile.reload()
        onLoaded: root.mouthTrack = root.parse(mouthFile.text(), root.mouthTrack)
    }

    FileView {
        id: presenceFile

        path: root.stateDir + "/presence.json"
        watchChanges: true
        printErrors: false
        onFileChanged: presenceFile.reload()
        onLoaded: {
            root.now = Date.now() / 1000;
            root.cards = root.parse(presenceFile.text(), { cards: [] }).cards ?? [];
        }
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
                    if (root.cardPending)
                        root.placeCards();
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
            if (!hypr.connected)
                hypr.connected = true;
        }
    }

    // The cards: a small layer of their own, pinned beside the cursor where the showing began, or
    // to the bottom right corner when the cursor could not be read. It only exists while a card
    // is up, and only what is drawn takes the pointer.
    PanelWindow {
        id: win

        // Everything here is in the monitor's own coordinates, the same space as the orb's, and
        // the window is always placed from the top left.
        readonly property real gap: root.orbGap
        readonly property real edge: root.orbEdge
        readonly property real clear: root.orbHeight + 10
        readonly property real sw: win.screen?.width ?? 0
        readonly property real sh: win.screen?.height ?? 0
        readonly property real lx: root.cardAt.x - (win.screen?.x ?? 0)
        readonly property real ly: root.cardAt.y - (win.screen?.y ?? 0)
        readonly property real h: content.implicitHeight

        // Right of the cursor, or left of it when the right side runs out of room; the corner
        // when the cursor was not read.
        readonly property real cardX: {
            if (!root.cardPlaced)
                return win.sw - win.implicitWidth - 22;
            const right = win.lx + win.gap;
            const x = right + win.implicitWidth > win.sw - win.edge ? win.lx - win.gap - win.implicitWidth : right;
            return Math.max(win.edge, Math.min(win.sw - win.implicitWidth - win.edge, x));
        }
        // Above the cursor, past the orb when it sits there; below it, past the orb, when there
        // was no room above.
        readonly property real cardY: {
            if (!root.cardPlaced)
                return win.sh - win.h - 22;
            const y = root.cardBelow ? win.ly + win.gap + (root.cardOrbUp ? 0 : win.clear)
                : win.ly - win.gap - (root.cardOrbUp ? win.clear : 0) - win.h;
            return Math.max(win.edge, Math.min(win.sh - win.h - win.edge, y));
        }

        screen: root.cardPlaced ? root.cardScreen : root.cursorScreen
        visible: root.cardsUp && !root.cardPending
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        WlrLayershell.namespace: "diablo"
        implicitWidth: 300
        implicitHeight: content.implicitHeight
        mask: Region {
            item: content
        }

        anchors {
            top: true
            left: true
        }

        margins {
            top: win.cardY
            left: win.cardX
        }

        Column {
            id: content

            anchors.right: parent.right
            anchors.bottom: parent.bottom
            spacing: 10

            HoverHandler {
                id: cardArea

                readonly property bool containsMouse: hovered
            }

            Repeater {
                model: cardModel

                Card {
                    required property string json

                    anchors.right: parent.right
                    style: theme
                    info: JSON.parse(json)
                    onDismiss: id => {
                        const next = Object.assign({}, root.dismissed);
                        next[id] = true;
                        root.dismissed = next;
                    }
                }
            }
        }
    }

    // The orb's own layer: the whole of the cursor's monitor, drawn on but never touched. An
    // empty mask hands every click and hover through to whatever is under it, and it never
    // takes the keyboard. It exists only while the orb is awake or still fading out.
    PanelWindow {
        id: follow

        screen: root.cursorScreen
        visible: root.up || group.opacity > 0.01
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        WlrLayershell.namespace: "diablo-orb"
        mask: Region {}

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
        readonly property real localX: root.cursor.x - (follow.screen?.x ?? 0)
        readonly property real localY: root.cursor.y - (follow.screen?.y ?? 0)
        readonly property bool flipX: root.followCursor && follow.localX + follow.gap + group.width > follow.width - follow.edge
        readonly property bool flipY: root.followCursor && follow.localY + follow.gap + group.height > follow.height - follow.edge
        readonly property real targetX: Math.max(follow.edge, Math.min(follow.width - group.width - follow.edge,
            !root.followCursor ? follow.width - group.width - 22
            : follow.flipX ? follow.localX - follow.gap - group.width : follow.localX + follow.gap))
        readonly property real targetY: Math.max(follow.edge, Math.min(follow.height - group.height - follow.edge,
            !root.followCursor ? follow.height - group.height - 22
            : follow.flipY ? follow.localY - follow.gap - group.height : follow.localY + follow.gap))

        // A fresh appearance, or a jump to another monitor, starts where he is, not where the
        // orb last was.
        function snap() {
            group.x = follow.targetX;
            group.y = follow.targetY;
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

            width: orbBox.width + 8 + pill.width
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
                    moment: root.moment
                    mouthTrack: root.mouthTrack

                    Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                }
            }

            // What he is doing, in words, so the colours never need learning.
            Rectangle {
                id: pill

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
    }
}
