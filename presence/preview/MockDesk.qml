import QtQuick

Rectangle {
    id: desk

    property Item fileMenu: null
    property Item outline: null
    property Item history: null
    readonly property alias exportButton: exportButton
    readonly property alias shareButton: shareButton
    readonly property alias keyLine: keyLine
    readonly property alias figure: figure

    function boxOf(item) {
        const p = item.mapToItem(desk, 0, 0);
        return { x: Math.round(p.x), y: Math.round(p.y), w: Math.round(item.width), h: Math.round(item.height) };
    }

    color: "#3a3f47"

    Rectangle {
        id: app

        x: 70
        y: 46
        width: parent.width - 140
        height: parent.height - 92
        radius: 8
        color: "#f7f6f2"
        border.color: "#c9c6bf"
        clip: true

        Rectangle {
            id: bar

            width: parent.width
            height: 46
            color: "#ecebe6"

            Row {
                x: 14
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4

                Repeater {
                    model: ["File", "Edit", "View", "Insert", "Format"]

                    Rectangle {
                        id: menu

                        required property string modelData
                        required property int index

                        width: menuText.implicitWidth + 20
                        height: 30
                        radius: 4
                        color: "transparent"
                        Component.onCompleted: if (index === 0) desk.fileMenu = menu

                        Text {
                            id: menuText

                            anchors.centerIn: parent
                            text: menu.modelData
                            color: "#2b2a28"
                            font.pixelSize: 14
                        }
                    }
                }
            }

            Row {
                anchors.right: parent.right
                anchors.rightMargin: 14
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                Rectangle {
                    id: shareButton

                    width: 72
                    height: 30
                    radius: 4
                    color: "#ffffff"
                    border.color: "#c9c6bf"

                    Text {
                        anchors.centerIn: parent
                        text: "Share"
                        color: "#2b2a28"
                        font.pixelSize: 14
                    }
                }

                Rectangle {
                    id: exportButton

                    width: 78
                    height: 30
                    radius: 4
                    color: "#3d6fb6"

                    Text {
                        anchors.centerIn: parent
                        text: "Export"
                        color: "#ffffff"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                }
            }
        }

        Rectangle {
            y: bar.height
            width: 200
            height: parent.height - bar.height
            color: "#f0efea"

            Column {
                x: 14
                y: 18
                spacing: 6

                Repeater {
                    model: ["Outline", "Comments", "History"]

                    Text {
                        id: side

                        required property string modelData
                        required property int index

                        text: modelData
                        color: "#4a4844"
                        font.pixelSize: 14
                        height: 28
                        verticalAlignment: Text.AlignVCenter
                        Component.onCompleted: {
                            if (index === 0)
                                desk.outline = side;
                            if (index === 2)
                                desk.history = side;
                        }
                    }
                }
            }
        }

        Column {
            x: 240
            y: bar.height + 30
            width: parent.width - 290
            spacing: 14

            Text {
                text: "Quarterly notes"
                color: "#1d1c1a"
                font.pixelSize: 28
                font.weight: Font.DemiBold
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "The garden club met three times this quarter and agreed on a new watering rota for the shared beds."
                color: "#3a3936"
                font.pixelSize: 16
                lineHeight: 1.3
            }

            Text {
                id: keyLine

                text: "Members now water on their own day, so no bed goes dry over a long weekend."
                color: "#3a3936"
                font.pixelSize: 16
            }

            Row {
                spacing: 0

                Text {
                    text: "Seed orders came to "
                    color: "#3a3936"
                    font.pixelSize: 16
                }

                Text {
                    id: figure

                    text: "412 packets"
                    color: "#3a3936"
                    font.pixelSize: 16
                }

                Text {
                    text: ", a little over the plan for spring."
                    color: "#3a3936"
                    font.pixelSize: 16
                }
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Next quarter the club will repair the tool shed roof and try a second compost bay beside the gate."
                color: "#3a3936"
                font.pixelSize: 16
                lineHeight: 1.3
            }
        }
    }
}
