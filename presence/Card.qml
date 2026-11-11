import QtQuick
import QtMultimedia
import Quickshell.Io

// One thing Diablo shows: a title, an optional big figure, and a few short lines.
Rectangle {
    id: card

    required property var style
    required property var info   // {id, kind, title, big, lines, at, ttl, video?}
    readonly property string video: info.video ?? ""
    property string said: ""
    signal dismiss(string id)

    width: 300
    implicitHeight: column.implicitHeight + 28
    height: implicitHeight
    radius: style.radius
    color: Qt.rgba(0.06, 0.07, 0.08, 0.92)
    border.width: 1
    border.color: hover.containsMouse ? style.faint : style.line

    // Hovering keeps a card up; a click puts it away. It sits under the card's own controls.
    MouseArea {
        id: hover

        anchors.fill: parent
        hoverEnabled: true
        onClicked: card.dismiss(card.info.id)
    }

    Column {
        id: column

        x: 16
        y: 14
        width: parent.width - 32
        spacing: 3

        Text {
            width: parent.width
            text: (card.info.title ?? "").toUpperCase()
            visible: text.length > 0
            color: card.style.quiet
            font.family: card.style.family
            font.pixelSize: card.style.small - 1
            font.letterSpacing: 0.8
            elide: Text.ElideRight
        }

        Text {
            width: parent.width
            text: card.info.big ?? ""
            visible: text.length > 0
            color: card.style.strong
            font.family: card.style.family
            font.pixelSize: card.style.big
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }

        // A take Diablo acted out: plays once with its sound, a click on it plays it again.
        Item {
            width: parent.width
            height: card.video ? width : 0
            visible: card.video !== ""

            Rectangle {
                anchors.fill: parent
                radius: 8
                color: "#f2f1ee"
            }

            Video {
                id: player

                anchors.fill: parent
                source: card.video ? "file://" + encodeURI(card.video) : ""
                autoPlay: card.video !== ""
                loops: 1
                volume: 1.0
                fillMode: VideoOutput.PreserveAspectFit
            }

            Text {
                anchors.centerIn: parent
                visible: player.playbackState !== MediaPlayer.PlayingState && card.video !== ""
                text: "▶"
                color: "#0040ff"
                font.pixelSize: 40
                opacity: 0.8
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    player.stop();
                    player.play();
                }
            }
        }

        Row {
            visible: card.video !== ""
            spacing: 6
            topPadding: 6

            Repeater {
                model: [
                    { label: "Copy", run: ["sh", "-c", "printf '%s\\r\\n' \"$(python3 -c 'import sys, pathlib; print(pathlib.Path(sys.argv[1]).as_uri())' \"$1\")\" | wl-copy --type text/uri-list", "sh", card.video], done: "Copied, paste it anywhere" },
                    { label: "Save", run: ["sh", "-c", "d=$(zenity --file-selection --save --confirm-overwrite --filename=\"$HOME/Videos/$(basename \"$1\")\") && cp \"$1\" \"$d\" && echo \"$d\"", "sh", card.video], done: "Saved" },
                    { label: "Open", run: ["xdg-open", card.video], done: "" }
                ]

                Rectangle {
                    required property var modelData

                    width: label.implicitWidth + 22
                    height: 26
                    radius: 13
                    color: button.containsMouse ? card.style.line : card.style.raised
                    border.width: 1
                    border.color: card.style.line

                    Text {
                        id: label

                        anchors.centerIn: parent
                        text: modelData.label
                        color: card.style.text
                        font.family: card.style.family
                        font.pixelSize: card.style.small
                    }

                    Process {
                        id: job

                        command: modelData.run
                        onExited: (code, status) => card.said = code === 0 ? modelData.done : ""
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

        Text {
            width: parent.width
            visible: card.said !== ""
            text: card.said
            color: card.style.quiet
            font.family: card.style.family
            font.pixelSize: card.style.small
        }

        Repeater {
            model: card.info.lines ?? []

            Text {
                required property string modelData

                width: column.width
                text: modelData
                color: card.style.text
                font.family: card.style.family
                font.pixelSize: card.style.normal - 1
                wrapMode: Text.WordWrap
                maximumLineCount: 6
                elide: Text.ElideRight
            }
        }
    }

}
