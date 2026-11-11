import QtQuick
import QtMultimedia

// One thing Diablo shows: a title, an optional big figure, and a few short lines.
Rectangle {
    id: card

    required property var style
    required property var info   // {id, kind, title, big, lines, at, ttl, video?, page?}
    readonly property string video: info.video ?? ""
    // An HTML page: this spot is kept free and the web view (webcard.py) is drawn over it.
    readonly property string page: info.page ?? ""
    readonly property alias pageSlot: pageSlot
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

        // Copy, save and open for a take; they run commands, so they load only for a video.
        Loader {
            active: card.video !== ""
            visible: active
            source: "CardActions.qml"
            onLoaded: {
                item.card = card;
                item.style = card.style;
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

        Rectangle {
            id: pageSlot

            width: parent.width
            height: card.page !== "" ? 190 : 0
            visible: card.page !== ""
            radius: 8
            color: card.style.base
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
