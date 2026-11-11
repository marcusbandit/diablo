import QtQuick
import QtQuick.Window
import ".."
import "../place.js" as Place

// Diablo's pieces drawn offscreen, for scripts/presence-shots.sh: no compositor, no layer
// shell, no real state. Each scene lays the orb, cards and central card out the way shell.qml
// does (the same Place geometry), grabs the window to a PNG and quits.
//     QT_QPA_PLATFORM=offscreen qml presence/preview/Preview.qml -- SCENE OUT.png FACES_DIR [EARS_URL]
Window {
    id: win

    readonly property var args: Qt.application.arguments.slice(Qt.application.arguments.indexOf("--") + 1)
    readonly property string scene: args[0] ?? "card-below"
    readonly property string out: args[1] ?? "preview.png"
    readonly property string faces: args[2] ?? ""
    readonly property string ears: args[3] ?? "http://127.0.0.1:18758"

    readonly property bool panelScene: scene.startsWith("panel")
    width: panelScene ? 1280 : 760
    height: panelScene ? 860 : 560
    visible: true
    color: "#2a2f36"

    Style {
        id: theme
    }

    readonly property var scenes: ({
        "orb-listening": { mood: "listening", at: [240, 180], cards: [] },
        "orb-thinking": { mood: "thinking", at: [240, 180], cards: [] },
        "orb-speaking": { mood: "speaking", at: [240, 180], cards: [] },
        "act-speaking": { mood: "speaking", at: [240, 180], cards: [], track: true },
        "act-surprised": { mood: "asleep", moment: "surprised", at: [240, 180], cards: [] },
        "act-sorry": { mood: "asleep", moment: "sorry", at: [240, 180], cards: [] },
        "act-delighted": { mood: "asleep", moment: "delighted", at: [240, 180], cards: [] },
        "card-below": { mood: "asleep", at: [240, 120], cards: [{ id: "a", title: "Weather", big: "7°", lines: ["cloudy, feels like 4°"] }] },
        "card-above": { mood: "asleep", at: [240, 420], cards: [{ id: "a", title: "Weather", big: "7°", lines: ["cloudy, feels like 4°"] }] },
        "card-right-edge": { mood: "asleep", at: [700, 160], cards: [{ id: "a", title: "Next bus", big: "5 min", lines: ["then 20 min"] }] },
        "two-cards": { mood: "speaking", at: [240, 100], cards: [{ id: "a", title: "Weather", big: "7°", lines: ["cloudy, feels like 4°"] },
            { id: "b", title: "Diablo", big: "", lines: ["Here is the weather for today, sir."] }] },
        "panel-settings": { mood: "asleep", panel: { view: "settings" } },
        "panel-training": { mood: "listening", panel: { view: "settings" } },
        "panel-now": { mood: "asleep", panel: { view: "now", card: { title: "Weather", big: "7°", lines: ["Cloudy, feels like 4°. Rain from 16:00.", "Tomorrow 4 to 8 degrees, light rain."] } } }
    })
    readonly property var s: scenes[scene] ?? scenes["card-below"]
    readonly property bool cardsUp: (s.cards ?? []).length > 0
    readonly property bool awake: ["listening", "thinking", "speaking"].includes(s.mood)

    // The orb and its pill, as shell.qml groups them, beside the cursor.
    readonly property var panelSpot: Place.panelRect({ w: win.width, h: win.height }, 12)
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

    // A cursor mark, so a shot shows where the pointer was.
    Rectangle {
        visible: !win.panelScene
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
        visible: win.panelScene
        x: win.panelSpot.x
        y: win.panelSpot.y
        width: win.panelSpot.w
        height: win.panelSpot.h
        style: theme
        view: win.s.panel?.view ?? "settings"
        card: win.s.panel?.card ?? null
        earsUrl: win.ears
    }

    Timer {
        interval: win.panelScene ? 2600 : 1400
        running: true
        onTriggered: win.contentItem.grabToImage(result => {
            result.saveToFile(win.out);
            Qt.quit();
        })
    }
}
