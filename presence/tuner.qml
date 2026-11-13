// The mouth tuner: his face, big, saying one line over and over through the same voice stream
// his answers use (voice/lipsync.py serve), and a delay to nudge until the mouth looks in step.
//     qs -n -p ~/Projects/diablo/presence/tuner.qml      (diablo-ears mouth runs this)
// Left and right move the mouth 10 ms, with Shift 50 ms. Enter saves to
// ~/.config/diablo/lipsync.json, which the orb reads for every answer; Esc leaves.
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root

    readonly property int limit: 400
    readonly property string configDir: (Quickshell.env("XDG_CONFIG_HOME") || `${Quickshell.env("HOME")}/.config`) + "/diablo"
    readonly property string repo: `${Quickshell.shellDir}/..`

    property int delay: 0
    property int saved: 0
    // Once the delay has been moved or saved here, nothing read from the file replaces it.
    property bool touched: false
    property string mood: "listening"
    property var track: ({ start: 0, duration: 0, cues: [] })
    property string status: "Getting his voice ready"
    property bool trouble: false
    readonly property bool changed: root.delay !== root.saved

    function clamp(ms: real): int {
        return Math.max(-root.limit, Math.min(root.limit, Math.round(ms)));
    }

    function nudge(ms: int) {
        root.touched = true;
        root.delay = root.clamp(root.delay + ms);
    }

    property int saving: 0

    function save() {
        root.touched = true;
        root.saving = root.delay;
        savedFile.setText(JSON.stringify({ delay_ms: root.delay }) + "\n");
    }

    function leave() {
        voice.running = false;
        Qt.quit();
    }

    function signed(ms: int): string {
        return ms > 0 ? `+${ms}` : `${ms}`;
    }

    function said(line: string) {
        let msg;
        try {
            msg = JSON.parse(line);
        } catch (e) {
            return;
        }
        if (msg.event === "heard") {
            root.track = msg;
            root.mood = "speaking";
            if (!root.changed && !root.trouble)
                root.status = "";
        } else if (msg.event === "done") {
            root.mood = "listening";
        } else if (msg.event === "status" || msg.event === "error") {
            root.trouble = msg.event === "error";
            root.status = msg.text ?? "";
        }
    }

    FileView {
        id: savedFile

        path: root.configDir + "/lipsync.json"
        printErrors: false
        onSaved: {
            root.saved = root.saving;
            root.trouble = false;
            root.status = `Saved. His answers use ${root.signed(root.saved)} ms from now on.`;
        }
        onSaveFailed: error => {
            root.trouble = true;
            root.status = `Could not save to ${savedFile.path}`;
        }
        onLoaded: {
            let ms = 0;
            try {
                ms = Number(JSON.parse(savedFile.text()).delay_ms);
            } catch (e) {
                ms = 0;
            }
            if (root.touched)
                return;
            root.saved = isFinite(ms) ? root.clamp(ms) : 0;
            root.delay = root.saved;
        }
    }

    // His voice, on the stream his answers play on. It stops when this window goes.
    Process {
        id: voice

        command: ["sh", "-c", 'cd "$1" && exec "${DIABLO_PYTHON:-$1/.venv-voice/bin/python}" -m voice.lipsync serve', "sh", root.repo]
        running: true
        stdinEnabled: true
        stdout: SplitParser {
            onRead: line => root.said(line)
        }
        onExited: code => {
            if (code !== 0 && !root.trouble) {
                root.trouble = true;
                root.status = "His voice stopped. Close this and open it again.";
            }
        }
    }

    Style {
        id: theme
    }

    PanelWindow {
        id: win

        implicitWidth: 720
        implicitHeight: 640
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        WlrLayershell.namespace: "diablo-tuner"

        Rectangle {
            id: card

            anchors.fill: parent
            color: theme.base
            radius: 20
            border.width: 1
            border.color: theme.line
            focus: true

            Keys.onPressed: event => {
                const step = (event.modifiers & Qt.ShiftModifier) ? 50 : 10;
                if (event.key === Qt.Key_Left) {
                    root.nudge(-step);
                } else if (event.key === Qt.Key_Right) {
                    root.nudge(step);
                } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    root.save();
                } else if (event.key === Qt.Key_Escape) {
                    root.leave();
                } else {
                    return;
                }
                event.accepted = true;
            }

            Text {
                x: 32
                y: 26
                text: "Mouth timing"
                color: theme.strong
                font.family: theme.family
                font.pixelSize: 22
                font.weight: Font.DemiBold
            }

            Rectangle {
                id: closeButton

                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 20
                width: 36
                height: 36
                radius: 18
                color: closeMouse.containsMouse ? theme.line : "transparent"

                Text {
                    anchors.centerIn: parent
                    text: "✕"
                    color: theme.text
                    font.pixelSize: 16
                }

                MouseArea {
                    id: closeMouse

                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.leave()
                }
            }

            // His face, big enough to read the mouth at a glance.
            Rectangle {
                id: stage

                anchors.horizontalCenter: parent.horizontalCenter
                y: 78
                width: parent.width - 64
                height: 220
                radius: 16
                color: theme.raised

                Orb {
                    anchors.centerIn: parent
                    scale: 2.6
                    style: theme
                    mood: root.mood
                    mouthTrack: root.track
                    mouthDelay: root.delay / 1000
                    facesDir: `${root.repo}/assets/faces`
                }
            }

            Column {
                id: readout

                anchors.horizontalCenter: parent.horizontalCenter
                y: stage.y + stage.height + 22
                spacing: 2

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: `${root.signed(root.delay)} ms`
                    color: theme.strong
                    font.family: theme.family
                    font.pixelSize: 44
                    font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.delay === 0 ? "mouth exactly on the sound"
                        : root.delay > 0 ? "mouth after the sound" : "mouth before the sound"
                    color: theme.quiet
                    font.family: theme.family
                    font.pixelSize: theme.normal
                }
            }

            // The delay itself: drag it, click along it, or use the arrow keys.
            Item {
                id: slider

                readonly property real span: root.limit * 2
                function xOf(ms: real): real {
                    return (ms + root.limit) / slider.span * track.width;
                }

                x: 48
                y: readout.y + readout.height + 26
                width: parent.width - 96
                height: 44

                Rectangle {
                    id: track

                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width
                    height: 6
                    radius: 3
                    color: theme.line

                    Rectangle {
                        x: Math.min(slider.xOf(0), slider.xOf(root.delay))
                        width: Math.abs(slider.xOf(root.delay) - slider.xOf(0))
                        height: parent.height
                        radius: 3
                        color: theme.speaking
                        opacity: 0.7
                    }
                }

                Rectangle {
                    x: slider.xOf(0) - 1
                    anchors.verticalCenter: parent.verticalCenter
                    width: 2
                    height: 18
                    color: theme.faint
                }

                // Where the saved value sits, so a nudge away can always find its way back.
                Rectangle {
                    visible: root.changed
                    x: slider.xOf(root.saved) - 4
                    anchors.verticalCenter: parent.verticalCenter
                    width: 8
                    height: 8
                    radius: 4
                    color: theme.quiet
                }

                Rectangle {
                    x: slider.xOf(root.delay) - width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    width: 22
                    height: 22
                    radius: 11
                    color: theme.strong
                    border.width: 3
                    border.color: theme.speaking
                    scale: drag.pressed ? 1.15 : 1

                    Behavior on x { enabled: !drag.pressed; NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }
                    Behavior on scale { NumberAnimation { duration: 120 } }
                }

                MouseArea {
                    id: drag

                    function set(px: real) {
                        const ms = px / track.width * slider.span - root.limit;
                        root.touched = true;
                        root.delay = root.clamp(Math.round(ms / 5) * 5);
                    }

                    anchors.fill: parent
                    anchors.margins: -8
                    cursorShape: Qt.PointingHandCursor
                    onPressed: mouse => drag.set(mouse.x - 8)
                    onPositionChanged: mouse => {
                        if (drag.pressed)
                            drag.set(mouse.x - 8);
                    }
                }
            }

            Item {
                x: slider.x
                y: slider.y + slider.height + 2
                width: slider.width
                height: 18

                Text {
                    text: `earlier  ${-root.limit}`
                    color: theme.faint
                    font.family: theme.family
                    font.pixelSize: theme.small
                }

                Text {
                    x: slider.xOf(0) - width / 2
                    text: "0"
                    color: theme.faint
                    font.family: theme.family
                    font.pixelSize: theme.small
                }

                Text {
                    anchors.right: parent.right
                    text: `+${root.limit}  later`
                    color: theme.faint
                    font.family: theme.family
                    font.pixelSize: theme.small
                }
            }

            Row {
                id: keys

                anchors.horizontalCenter: parent.horizontalCenter
                y: slider.y + slider.height + 40
                spacing: 18

                KeyHint { keyText: "← →"; what: "10 ms" }
                KeyHint { keyText: "Shift"; what: "50 ms" }
                KeyHint { keyText: "Enter"; what: "save" }
                KeyHint { keyText: "Esc"; what: "leave" }
            }

            Text {
                x: 32
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 34
                width: parent.width - buttons.width - 80
                text: root.changed && root.status.startsWith("Saved") ? `Saved is ${root.signed(root.saved)} ms` : root.status
                color: root.trouble ? theme.trouble : theme.quiet
                font.family: theme.family
                font.pixelSize: theme.normal
                elide: Text.ElideRight
            }

            Row {
                id: buttons

                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 24
                spacing: 10

                PillButton {
                    text: `Back to ${root.signed(root.saved)} ms`
                    visible: root.changed
                    onClicked: {
                        root.touched = true;
                        root.delay = root.saved;
                    }
                }

                PillButton {
                    text: root.changed ? "Save" : "Saved"
                    accent: root.changed
                    onClicked: root.save()
                }
            }
        }
    }

    component KeyHint: Row {
        property string keyText
        property string what

        spacing: 8

        Rectangle {
            width: keyLabel.implicitWidth + 16
            height: 26
            radius: 6
            color: theme.raised
            border.width: 1
            border.color: theme.line

            Text {
                id: keyLabel

                anchors.centerIn: parent
                text: parent.parent.keyText
                color: theme.text
                font.family: theme.family
                font.pixelSize: theme.small + 1
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: parent.what
            color: theme.quiet
            font.family: theme.family
            font.pixelSize: theme.normal
        }
    }

    component PillButton: Rectangle {
        id: button

        property string text
        property bool accent: false
        signal clicked

        implicitWidth: buttonText.implicitWidth + 30
        implicitHeight: 36
        radius: 18
        color: button.accent ? (buttonMouse.containsMouse ? Qt.lighter(theme.speaking, 1.1) : theme.speaking)
            : (buttonMouse.containsMouse ? theme.line : theme.raised)
        border.width: button.accent ? 0 : 1
        border.color: theme.line

        Text {
            id: buttonText

            anchors.centerIn: parent
            text: button.text
            color: button.accent ? theme.base : theme.text
            font.family: theme.family
            font.pixelSize: theme.normal
            font.weight: button.accent ? Font.DemiBold : Font.Normal
        }

        MouseArea {
            id: buttonMouse

            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: button.clicked()
        }
    }
}
