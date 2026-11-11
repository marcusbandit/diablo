import QtQuick
import QtQuick.Layouts

// The central card: Diablo opened up in the middle of the screen. Two pages: Now, a card he was
// asked to show big, and Settings, the few things worth changing about him, the voice setup
// among them. It talks to the ears' control panel (voice/ears_api.py) on this machine only.
Rectangle {
    id: panel

    required property var style
    property string view: "settings"       // "now" or "settings"
    property var card: null                // what "Now" shows: {title, big, lines, page?}
    property bool followCursor: true
    property string earsUrl: "http://127.0.0.1:8758"
    // Where an HTML page goes: shell.qml hands this rect to the web view so it sits over it.
    readonly property alias pageArea: pageArea
    signal closeRequested
    signal viewRequested(string view)
    signal followChanged(bool on)

    color: Qt.rgba(0.06, 0.07, 0.08, 0.96)
    radius: 20
    border.width: 1
    border.color: panel.style.line
    clip: true

    // The ears, read while Settings is open: the whole status each second, the meters faster.
    property var ears: null
    property var live: null
    property string earsError: ""
    readonly property var session: panel.ears?.session ?? null
    readonly property var model: panel.ears?.model ?? null

    function request(method: string, path: string, body: var, done: var) {
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = () => {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            let reply = null;
            try {
                reply = JSON.parse(xhr.responseText);
            } catch (e) {}
            if (done)
                done(xhr.status, reply);
        };
        xhr.open(method, panel.earsUrl + path);
        if (method === "POST") {
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-Diablo-Ears", "1");
        }
        xhr.send(body ? JSON.stringify(body) : null);
    }

    function refresh() {
        panel.request("GET", "/v1/ears", null, (status, reply) => {
            if (status === 200 && reply) {
                panel.ears = reply;
                panel.earsError = "";
            } else {
                panel.earsError = status === 0 ? "The ears are not running on this computer." : `The ears answered ${status}.`;
            }
        });
    }

    function act(path: string, body: var) {
        panel.request("POST", path, body || {}, (status, reply) => {
            if (status !== 200)
                panel.earsError = reply?.error ?? `That did not work (${status}).`;
            panel.refresh();
        });
    }

    Timer {
        interval: panel.session?.active ? 500 : 1500
        repeat: true
        triggeredOnStart: true
        running: panel.visible && panel.view === "settings"
        onTriggered: panel.refresh()
    }

    Timer {
        interval: 120
        repeat: true
        running: panel.visible && panel.view === "settings" && panel.ears !== null
        onTriggered: panel.request("GET", "/v1/ears/live", null, (status, reply) => {
            if (status === 200)
                panel.live = reply;
        })
    }

    // Header: his name, the two pages, and a way out.
    RowLayout {
        id: header

        x: 136
        y: 22
        width: parent.width - x - 22
        spacing: 10

        Text {
            text: "Diablo"
            color: panel.style.strong
            font.family: panel.style.family
            font.pixelSize: 22
            font.weight: Font.DemiBold
        }

        Item { Layout.fillWidth: true }

        Repeater {
            model: [{ id: "now", label: "Now" }, { id: "settings", label: "Settings" }]

            Rectangle {
                required property var modelData

                implicitWidth: tabText.implicitWidth + 28
                implicitHeight: 32
                radius: 16
                color: panel.view === modelData.id ? panel.style.line : (tabMouse.containsMouse ? panel.style.raised : "transparent")
                border.width: 1
                border.color: panel.style.line

                Text {
                    id: tabText

                    anchors.centerIn: parent
                    text: modelData.label
                    color: panel.view === modelData.id ? panel.style.strong : panel.style.text
                    font.family: panel.style.family
                    font.pixelSize: panel.style.normal
                }

                MouseArea {
                    id: tabMouse

                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.viewRequested(modelData.id)
                }
            }
        }

        Rectangle {
            implicitWidth: 32
            implicitHeight: 32
            radius: 16
            color: closeMouse.containsMouse ? panel.style.line : "transparent"

            Text {
                anchors.centerIn: parent
                text: "✕"
                color: panel.style.text
                font.pixelSize: 15
            }

            MouseArea {
                id: closeMouse

                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: panel.closeRequested()
            }
        }
    }

    // ---- Now: a card shown big --------------------------------------------------------------
    Item {
        id: nowPage

        x: 32
        y: 96
        width: parent.width - 64
        height: parent.height - y - 28
        visible: panel.view === "now"

        Column {
            width: parent.width
            spacing: 8
            visible: panel.card !== null

            Text {
                width: parent.width
                text: (panel.card?.title ?? "").toUpperCase()
                visible: text.length > 0
                color: panel.style.quiet
                font.family: panel.style.family
                font.pixelSize: panel.style.normal
                font.letterSpacing: 1
            }

            Text {
                width: parent.width
                text: panel.card?.big ?? ""
                visible: text.length > 0
                color: panel.style.strong
                font.family: panel.style.family
                font.pixelSize: 56
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }

            Repeater {
                model: panel.card?.lines ?? []

                Text {
                    required property string modelData

                    width: nowPage.width
                    text: modelData
                    color: panel.style.text
                    font.family: panel.style.family
                    font.pixelSize: 18
                    wrapMode: Text.WordWrap
                }
            }
        }

        // An HTML page fills what is left; the web view is drawn over this spot.
        Rectangle {
            id: pageArea

            readonly property bool wanted: panel.view === "now" && (panel.card?.page ?? "") !== ""

            y: panel.card && (panel.card.title || panel.card.big) ? 0 : 0
            width: parent.width
            height: parent.height
            visible: pageArea.wanted
            radius: 12
            color: panel.style.base
        }

        Text {
            anchors.centerIn: parent
            visible: panel.card === null
            text: "Nothing to show yet. Ask me to show you something, or to enlarge a card."
            color: panel.style.quiet
            font.family: panel.style.family
            font.pixelSize: 16
        }
    }

    // ---- Settings ---------------------------------------------------------------------------
    Flickable {
        id: settingsPage

        x: 32
        y: 96
        width: parent.width - 64
        height: parent.height - y - 20
        visible: panel.view === "settings"
        contentHeight: settingsColumn.implicitHeight + 20
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: settingsColumn

            width: settingsPage.width
            spacing: 14

            SectionTitle { text: "On screen" }

            SwitchRow {
                label: "Follow the cursor"
                detail: on ? "He sits beside the pointer and glides after it." : "He waits in the bottom right corner."
                on: panel.followCursor
                onToggled: on => panel.followChanged(on)
            }

            SectionTitle { text: "Ears" }

            Text {
                width: parent.width
                visible: panel.earsError !== ""
                text: panel.earsError
                color: panel.style.trouble
                font.family: panel.style.family
                font.pixelSize: panel.style.normal
                wrapMode: Text.WordWrap
            }

            SwitchRow {
                label: "Listening for his name"
                detail: !panel.ears ? "" : panel.ears.listening ? `Awake to "Diablo". He is ${panel.ears.state}.` : "Not listening. Nothing reaches him by voice."
                on: panel.ears?.listening ?? false
                enabled: panel.ears !== null
                onToggled: on => panel.act("/v1/ears/listen", { on: on })
            }

            // The meters: the microphone, and how sure he is that he just heard his name.
            Meter {
                label: "Microphone"
                value: Math.min(1, Math.sqrt((panel.live?.mic ?? 0) * 12))
                hue: panel.style.listening
            }

            Meter {
                label: "Hears his name"
                value: Math.min(1, panel.live?.score ?? 0)
                mark: panel.ears?.live?.threshold ?? -1
                hue: panel.style.speaking
            }

            SectionTitle { text: "Your voice" }

            // How well he knows it, in plain words.
            Text {
                width: parent.width
                visible: !panel.session?.active
                text: !panel.ears ? ""
                    : !panel.ears.trained ? "He does not know your voice yet. Teaching him takes about ten minutes: your name from a few spots in the room, a few sentences, and ten seconds of quiet."
                    : `He learned your voice from ${panel.model?.recordings ?? 0} recordings on ${(panel.model?.trained_at ?? "").slice(0, 10)}. `
                      + `He catches ${Math.round((panel.model?.held_out_recall_clean ?? 0) * 100)}% of your calls in a quiet room and `
                      + `${Math.round((panel.model?.held_out_recall_noisy ?? 0) * 100)}% over noise, and woke ${panel.model?.validation_fires ?? 0} times in `
                      + `${Math.round(panel.model?.validation_hours ?? 0)} hours of other sound.`
                color: panel.style.text
                font.family: panel.style.family
                font.pixelSize: panel.style.normal + 1
                wrapMode: Text.WordWrap
                lineHeight: 1.2
            }

            // A setup that is running: what he says, what to read, the takes so far.
            Rectangle {
                width: parent.width
                visible: !!panel.session
                implicitHeight: sessionColumn.implicitHeight + 32
                radius: 14
                color: panel.style.raised
                border.width: 1
                border.color: panel.session?.recording ? panel.style.speaking : panel.style.line

                Column {
                    id: sessionColumn

                    x: 16
                    y: 16
                    width: parent.width - 32
                    spacing: 10

                    Text {
                        width: parent.width
                        text: panel.session?.said || (panel.session?.stage === "learning" ? "Learning your voice." : "")
                        visible: text.length > 0
                        color: panel.style.strong
                        font.family: panel.style.family
                        font.pixelSize: 17
                        wrapMode: Text.WordWrap
                    }

                    // The word or sentence to say, large, the words he has heard lit.
                    Flow {
                        width: parent.width
                        spacing: 6
                        visible: (panel.session?.passage ?? "") !== "" || (panel.session?.word ?? "") !== ""

                        Repeater {
                            model: (panel.session?.passage || panel.session?.word || "").split(" ")

                            Text {
                                required property string modelData
                                required property int index

                                text: modelData
                                color: (panel.session?.marks ?? [])[index] === "hit" ? panel.style.speaking
                                    : (panel.session?.marks ?? [])[index] === "miss" ? panel.style.trouble : panel.style.text
                                font.family: panel.style.family
                                font.pixelSize: 26
                            }
                        }
                    }

                    // One dot per take of the name: green kept, pink thrown out.
                    Flow {
                        width: parent.width
                        spacing: 5
                        visible: (panel.session?.takes ?? []).length > 0

                        Repeater {
                            model: panel.session?.takes ?? []

                            Rectangle {
                                required property var modelData

                                width: 12
                                height: 12
                                radius: 6
                                color: modelData.ok ? panel.style.speaking : panel.style.trouble
                            }
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: 6
                        radius: 3
                        visible: panel.session?.stage === "learning"
                        color: panel.style.line

                        Rectangle {
                            width: parent.width * (panel.session?.progress ?? 0)
                            height: parent.height
                            radius: 3
                            color: panel.style.thinking

                            Behavior on width { NumberAnimation { duration: 400 } }
                        }
                    }

                    Text {
                        width: parent.width
                        visible: (panel.session?.error ?? "") !== ""
                        text: panel.session?.error ?? ""
                        color: panel.style.trouble
                        font.family: panel.style.family
                        font.pixelSize: panel.style.normal
                        wrapMode: Text.WordWrap
                    }

                    Row {
                        spacing: 8
                        visible: !!panel.session?.moving

                        PillButton { text: `I'm there (${Math.ceil(panel.session?.move_left ?? 0)} s)`; onClicked: panel.act("/v1/ears/train/ready") }
                        PillButton { text: "Skip this spot"; onClicked: panel.act("/v1/ears/train/skip") }
                    }
                }
            }

            Row {
                spacing: 8

                PillButton {
                    text: panel.ears?.trained ? "Teach him again" : "Teach him your voice"
                    accent: true
                    visible: !panel.session?.active
                    enabled: panel.ears !== null
                    onClicked: panel.act("/v1/ears/train", {})
                }

                PillButton {
                    text: "Start over"
                    visible: !panel.session?.active && !!panel.ears?.trained
                    onClicked: panel.act("/v1/ears/train", { fresh: true })
                }

                PillButton {
                    text: "Stop the setup"
                    visible: !!panel.session?.active
                    onClicked: panel.act("/v1/ears/train/stop")
                }
            }
        }
    }

    // ---- small parts ------------------------------------------------------------------------
    component SectionTitle: Text {
        topPadding: 6
        color: panel.style.quiet
        font.family: panel.style.family
        font.pixelSize: panel.style.small
        font.letterSpacing: 1
        font.capitalization: Font.AllUppercase
    }

    component SwitchRow: Item {
        id: row

        property string label
        property string detail
        property bool on
        signal toggled(bool on)

        width: settingsColumn.width
        implicitHeight: Math.max(40, rowText.implicitHeight)
        opacity: row.enabled ? 1 : 0.5

        Column {
            id: rowText

            width: parent.width - 70
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2

            Text {
                text: row.label
                color: panel.style.strong
                font.family: panel.style.family
                font.pixelSize: panel.style.normal + 1
            }

            Text {
                width: parent.width
                text: row.detail
                visible: text.length > 0
                color: panel.style.quiet
                font.family: panel.style.family
                font.pixelSize: panel.style.small + 1
                wrapMode: Text.WordWrap
            }
        }

        Rectangle {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 46
            height: 26
            radius: 13
            color: row.on ? panel.style.speaking : panel.style.line

            Behavior on color { ColorAnimation { duration: 150 } }

            Rectangle {
                x: row.on ? parent.width - width - 3 : 3
                anchors.verticalCenter: parent.verticalCenter
                width: 20
                height: 20
                radius: 10
                color: panel.style.strong

                Behavior on x { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
            }

            MouseArea {
                anchors.fill: parent
                enabled: row.enabled
                cursorShape: Qt.PointingHandCursor
                onClicked: row.toggled(!row.on)
            }
        }
    }

    component Meter: Item {
        id: meter

        property string label
        property real value: 0
        property real mark: -1
        property color hue

        width: settingsColumn.width
        implicitHeight: 22

        Text {
            width: 150
            anchors.verticalCenter: parent.verticalCenter
            text: meter.label
            color: panel.style.text
            font.family: panel.style.family
            font.pixelSize: panel.style.normal
        }

        Rectangle {
            id: track

            x: 160
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 160
            height: 8
            radius: 4
            color: panel.style.line

            Rectangle {
                width: track.width * Math.max(0, Math.min(1, meter.value))
                height: parent.height
                radius: 4
                color: meter.hue

                Behavior on width { NumberAnimation { duration: 110 } }
            }

            Rectangle {
                visible: meter.mark >= 0
                x: track.width * meter.mark - 1
                y: -4
                width: 2
                height: 16
                color: panel.style.strong
            }
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
        opacity: button.enabled ? 1 : 0.5
        color: button.accent ? (buttonMouse.containsMouse ? Qt.lighter(panel.style.speaking, 1.1) : panel.style.speaking)
            : (buttonMouse.containsMouse ? panel.style.line : panel.style.raised)
        border.width: button.accent ? 0 : 1
        border.color: panel.style.line

        Text {
            id: buttonText

            anchors.centerIn: parent
            text: button.text
            color: button.accent ? panel.style.base : panel.style.text
            font.family: panel.style.family
            font.pixelSize: panel.style.normal
            font.weight: button.accent ? Font.DemiBold : Font.Normal
        }

        MouseArea {
            id: buttonMouse

            anchors.fill: parent
            enabled: button.enabled
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: button.clicked()
        }
    }
}
