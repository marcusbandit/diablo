import QtQuick
import QtQuick.Window
import ".."
import "../place.js" as Place
import "../marks.js" as Mk

// Diablo's pieces drawn offscreen, for scripts/presence-shots.sh: no compositor, no layer
// shell, no real state. Each scene lays the orb, cards and central card out the way shell.qml
// does (the same Place geometry), grabs the window to a PNG and quits.
//     QT_QPA_PLATFORM=offscreen qml presence/preview/Preview.qml -- SCENE OUT.png FACES_DIR [EARS_URL] [HUB_URL]
Window {
    id: win

    readonly property var args: Qt.application.arguments.slice(Qt.application.arguments.indexOf("--") + 1)
    readonly property string scene: args[0] ?? "card-below"
    readonly property string out: args[1] ?? "preview.png"
    readonly property string faces: args[2] ?? ""
    readonly property string ears: args[3] ?? "http://127.0.0.1:18758"
    readonly property string hub: args[4] ?? "http://127.0.0.1:18755"
    readonly property var diagram: { try { return JSON.parse(args[5] ?? "[]"); } catch (e) { return []; } }

    readonly property bool panelScene: scene.startsWith("panel")
    readonly property bool marksScene: scene.startsWith("marks") || scene.startsWith("diagram")
    readonly property bool diagramScene: scene.startsWith("diagram")
    width: s.screen?.[0] ?? (panelScene || marksScene ? 1280 : 760)
    height: s.screen?.[1] ?? (panelScene ? 860 : marksScene ? 800 : 560)
    visible: true
    color: "#2a2f36"

    Style {
        id: theme
    }

    // A made-up packing list the size of a real one: four sections, 24 items, some ticked.
    readonly property var packing: [
        { title: "Documents and money", items: [{ text: "Passport", done: true }, { text: "Bank card", done: true },
            { text: "A little cash", done: false }, { text: "Travel insurance card", done: true }] },
        { title: "Electronics", items: [{ text: "Laptop", done: true }, { text: "Laptop charger", done: true },
            { text: "Phone charger", done: false }, { text: "Power bank", done: false }, { text: "Headphones", done: false },
            { text: "Travel adapter", done: false }] },
        { title: "Clothes", items: ["4 sets of underwear", "4 pairs of socks", "4 t-shirts", "1 pair of trousers", "Hoodie",
            "Something smart for the dinner", "Warm jacket", "Hat, scarf and gloves", "Comfortable shoes"].map(t => ({ text: t, done: false })) },
        { title: "Toiletries", items: ["Toothbrush and toothpaste", "Deodorant", "Shampoo, 100 ml at most", "Any medicine",
            "Earplugs"].map(t => ({ text: t, done: false })) }
    ]
    // A list far longer than any card: eight sections of twelve.
    readonly property var chores: ["Kitchen", "Bathroom", "Bedroom", "Hall", "Garden", "Garage", "Office", "Attic"].map((room, r) => ({
        title: room, items: Array.from({ length: 12 }, (_, i) => ({ text: `${room} task ${i + 1}`, done: (r + i) % 4 === 0 }))
    }))

    readonly property var scenes: ({
        "orb-listening": { mood: "listening", at: [240, 180], cards: [] },
        "orb-thinking": { mood: "thinking", at: [240, 180], cards: [] },
        "orb-speaking": { mood: "speaking", at: [240, 180], cards: [] },
        "act-speaking": { mood: "speaking", at: [240, 180], cards: [], track: true },
        "act-surprised": { mood: "asleep", moment: "surprised", at: [240, 180], cards: [] },
        "act-sorry": { mood: "asleep", moment: "sorry", at: [240, 180], cards: [] },
        "act-delighted": { mood: "asleep", moment: "delighted", at: [240, 180], cards: [] },
        "moons-0": { mood: "asleep", at: [240, 180], cards: [], moons: [] },
        "moons-1": { mood: "asleep", at: [240, 180], cards: [], moons: [{ id: "a" }] },
        "moons-3": { mood: "asleep", at: [240, 180], cards: [], moons: [{ id: "a" }, { id: "b" }, { id: "c", waiting: true }] },
        "moons-9": { mood: "asleep", at: [240, 180], cards: [], moons: [1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => ({ id: `t${n}` })) },
        "card-below": { mood: "asleep", at: [240, 120], cards: [{ id: "a", title: "Weather", big: "7°", lines: ["cloudy, feels like 4°"] }] },
        "card-above": { mood: "asleep", at: [240, 420], cards: [{ id: "a", title: "Weather", big: "7°", lines: ["cloudy, feels like 4°"] }] },
        "card-right-edge": { mood: "asleep", at: [700, 160], cards: [{ id: "a", title: "Next bus", big: "5 min", lines: ["then 20 min"] }] },
        "two-cards": { mood: "speaking", at: [240, 100], cards: [{ id: "a", title: "Weather", big: "7°", lines: ["cloudy, feels like 4°"] },
            { id: "b", title: "Diablo", big: "", lines: ["Here is the weather for today, sir."] }] },
        "panel-settings": { mood: "asleep", panel: { view: "settings" } },
        "panel-voice": { mood: "asleep", panel: { view: "settings", page: "voice", short: true } },
        "panel-training": { mood: "listening", panel: { view: "settings" } },
        "panel-history": { mood: "asleep", panel: { view: "history" } },
        "panel-history-search": { mood: "asleep", panel: { view: "history", query: "basil" } },
        "panel-now": { mood: "asleep", panel: { view: "now", card: { title: "Weather", big: "7°", lines: ["Cloudy, feels like 4°. Rain from 16:00.", "Tomorrow 4 to 8 degrees, light rain."] } } },
        "panel-list": { mood: "asleep", panel: { view: "now", card: { title: "Packing list", sections: win.packing } } },
        "panel-list-desk": { mood: "asleep", screen: [5120, 1440], panel: { view: "now", card: { title: "Packing list", sections: win.packing } } },
        "panel-list-wide": { mood: "asleep", screen: [5120, 1440], panel: { view: "now", card: { title: "Spring cleaning", sections: win.chores } } },
        "panel-list-long": { mood: "asleep", panel: { view: "now", card: { title: "Spring cleaning", sections: win.chores } } },
        "marks-all": { mood: "speaking", at: [330, 600], cards: [], marks: d => [
            { kind: "ring", box: d.boxOf(d.exportButton) }, { kind: "arrow", box: d.boxOf(d.exportButton) },
            { kind: "label", text: "Then pick PDF", box: d.boxOf(d.exportButton) }, { kind: "num", n: 1, box: d.boxOf(d.fileMenu) },
            { kind: "underline", box: d.boxOf(d.keyLine) }, { kind: "highlight", box: d.boxOf(d.figure) },
            { kind: "check", box: d.boxOf(d.outline) }, { kind: "cross", box: d.boxOf(d.history) }] },
        "marks-spotlight": { mood: "speaking", at: [330, 600], cards: [], marks: d => [
            { kind: "spotlight", box: d.boxOf(d.exportButton) }, { kind: "ring", box: d.boxOf(d.exportButton) },
            { kind: "label", text: "Export", box: d.boxOf(d.exportButton) }] },
        "marks-steps": { mood: "speaking", at: [330, 600], cards: [], beat: d => ({ say: "Open the File menu, then choose Export.", caption: true,
            marks: [{ kind: "ring", box: d.boxOf(d.fileMenu) }, { kind: "num", n: 1, box: d.boxOf(d.fileMenu) },
                { kind: "arrow", box: d.boxOf(d.fileMenu) }] }) },
        "diagram-early": { mood: "speaking", at: [1500, 900], cards: [], screen: [1920, 1080], upTo: 2 },
        "diagram-last": { mood: "speaking", at: [1500, 900], cards: [], screen: [1920, 1080], upTo: 99 }
    })
    readonly property var s: scenes[scene] ?? scenes["card-below"]
    readonly property bool cardsUp: (s.cards ?? []).length > 0
    readonly property bool awake: ["listening", "thinking", "speaking"].includes(s.mood)

    // The orb and its pill, as shell.qml groups them, beside the cursor.
    readonly property var panelSpot: Place.panelRect({ w: win.width, h: win.height }, 12, previewPanel.want)
    Item {
        id: group

        x: win.panelScene ? win.panelSpot.x + 18 : (win.s.at[0] + 26 + width > win.width - 12 ? win.s.at[0] - 26 - width : win.s.at[0] + 26)
        y: win.panelScene ? win.panelSpot.y + 4 : (win.s.at[1] + 26 + height > win.height - 12 ? win.s.at[1] - 26 - height : win.s.at[1] + 26)
        z: 2
        width: 100 + (pill.visible ? 8 + pill.width : 0)
        height: 100

        Orb {
            anchors.centerIn: orbBox
            scale: win.awake ? 1.35 : 0.9
            style: theme
            facesDir: win.faces
            mood: win.s.mood
            moment: win.s.moment ?? ""
            presenting: (win.cardsUp || win.panelScene) && !win.awake
            thinkingBehind: (win.s.moons ?? []).length > 0
            moons: win.s.moons ?? null
            // A measured line in progress: one mouth shape and a look, the way voice/mouth.py writes them.
            mouthTrack: win.s.track ? { start: Date.now() / 1000 - 0.5, duration: 30, cues: [[0, "open"], [0.4, "ee"], [60, "rest"]],
                eyes: [[0, "happy"], [60, "neutral"]] } : { start: 0, duration: 0, cues: [] }
        }

        Item {
            id: orbBox

            width: 100
            height: 100
        }

        Rectangle {
            id: pill

            visible: win.awake && !win.panelScene
            x: 108
            anchors.verticalCenter: orbBox.verticalCenter
            width: label.implicitWidth + 24
            height: 30
            radius: 15
            color: Qt.rgba(0.06, 0.07, 0.08, 0.92)
            border.width: 1
            border.color: theme.line

            Text {
                id: label

                anchors.centerIn: parent
                text: win.s.mood
                color: theme.text
                font.pixelSize: theme.normal
            }
        }
    }

    MockDesk {
        id: desk

        anchors.fill: parent
        z: -2
        visible: win.marksScene
    }

    Marks {
        id: marksLayer

        anchors.fill: parent
        z: 1
        style: theme
        visible: win.marksScene && !win.diagramScene
    }

    Repeater {
        model: win.diagramScene ? win.diagram.slice(0, win.s.upTo ?? 99) : []

        Marks {
            required property var modelData

            anchors.fill: parent
            z: 1
            style: theme
            stagger: 300
            marks: Mk.toLocal(Mk.cleanMarks(modelData.marks), { x: 0, y: 0, w: win.width, h: win.height })
        }
    }

    Timer {
        interval: 80
        running: win.marksScene && !win.diagramScene
        onTriggered: {
            const beat = win.s.beat ? win.s.beat(desk) : { marks: win.s.marks(desk) };
            const orb = { x: group.x + 50, y: group.y + 50 };
            const screens = [{ x: 0, y: 0, w: win.width, h: win.height }];
            marksLayer.marks = Mk.toLocal(Mk.stage(Mk.withCaption(beat), screens, orb, null), screens[0]);
        }
    }

    // A cursor mark, so a shot shows where the pointer was.
    Rectangle {
        visible: !win.panelScene && !win.marksScene
        x: (win.s.at ?? [0, 0])[0] - 4
        y: (win.s.at ?? [0, 0])[1] - 4
        width: 8
        height: 8
        radius: 4
        color: "#ffffff"
    }

    Item {
        id: cards

        readonly property var spot: Place.cardRect({ x: group.x, y: group.y, w: group.width, h: group.height },
            { w: 300, h: Math.max(1, column.implicitHeight) }, { w: win.width, h: win.height }, 12, 10)

        visible: win.cardsUp
        x: spot.x
        y: spot.y
        width: 300
        height: column.implicitHeight

        Column {
            id: column

            width: 300
            spacing: 10

            Repeater {
                model: win.s.cards ?? []

                Card {
                    required property var modelData

                    style: theme
                    info: modelData
                }
            }
        }
    }

    Panel {
        id: previewPanel

        visible: win.panelScene
        base: Place.panelRect({ w: win.width, h: win.height }, 12)
        room: Place.panelRoom({ w: win.width, h: win.height }, 12)
        x: win.panelSpot.x
        y: win.panelSpot.y
        width: win.panelSpot.w
        style: theme
        view: win.s.panel?.view ?? "settings"
        card: win.s.panel?.card ?? null
        page: win.s.panel?.page ?? ""
        pageAt: (win.s.panel?.page ?? "") !== "" ? 1 : 0
        height: win.s.panel?.short ? 520 : win.panelSpot.h
        earsUrl: win.ears
        hubUrl: win.hub
        hubToken: "preview-token"
        Component.onCompleted: if (win.s.panel?.query)
            historySearch(win.s.panel.query)
    }

    Timer {
        interval: win.panelScene || win.marksScene ? 2600 : 1400
        running: true
        onTriggered: win.contentItem.grabToImage(result => {
            result.saveToFile(win.out);
            Qt.quit();
        })
    }
}
