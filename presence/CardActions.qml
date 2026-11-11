import QtQuick
import Quickshell.Io

// The buttons under a take Diablo acted out: copy it, save it, open it.
Row {
    id: actions

    property var card
    property var style

    spacing: 6
    topPadding: 6

    Repeater {
        model: [
            { label: "Copy", run: ["sh", "-c", "printf '%s\\r\\n' \"$(python3 -c 'import sys, pathlib; print(pathlib.Path(sys.argv[1]).as_uri())' \"$1\")\" | wl-copy --type text/uri-list", "sh", actions.card.video], done: "Copied, paste it anywhere" },
            { label: "Save", run: ["sh", "-c", "d=$(zenity --file-selection --save --confirm-overwrite --filename=\"$HOME/Videos/$(basename \"$1\")\") && cp \"$1\" \"$d\" && echo \"$d\"", "sh", actions.card.video], done: "Saved" },
            { label: "Open", run: ["xdg-open", actions.card.video], done: "" }
        ]

        Rectangle {
            required property var modelData

            width: label.implicitWidth + 22
            height: 26
            radius: 13
            color: button.containsMouse ? actions.style.line : actions.style.raised
            border.width: 1
            border.color: actions.style.line

            Text {
                id: label

                anchors.centerIn: parent
                text: modelData.label
                color: actions.style.text
                font.family: actions.style.family
                font.pixelSize: actions.style.small
            }

            Process {
                id: job

                command: modelData.run
                onExited: (code, status) => actions.card.said = code === 0 ? modelData.done : ""
            }

            MouseArea {
                id: button

                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: job.running = true
            }
        }
    }
}

