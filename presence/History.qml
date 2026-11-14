import QtQuick

// History: everything Marcus said to him on every channel, spoken or typed, and every reply,
// oldest at the top and newest at the bottom. Pages come from the hub (GET /v1/history, the
// same token busy.py uses); scrolling up loads older ones, and new lines come in while it is open.
Item {
    id: history

    required property var style
    property string hubUrl: ""
    property string hubToken: ""
    property bool active: false

    property string query: ""
    property string channel: ""
    property var channels: ({})
    property bool more: true
    property bool loading: false
    property string error: ""
    property bool loaded: false
    property int generation: 0
    property int oldest: 0
    property int newest: 0

    readonly property var names: ({ voice: "Voice", telegram: "Telegram", desktop: "Desktop", phone: "Phone", fleet: "Fleet",
        api: "Web", schedule: "Reminders", system: "System" })
    readonly property var channelList: Object.keys(history.channels).filter(c => history.channels[c] > 0)
        .sort((a, b) => history.channels[b] - history.channels[a])

    function nameOf(channel: string): string {
        return history.names[channel] ?? (channel ? channel.charAt(0).toUpperCase() + channel.slice(1) : "Unknown");
    }

    function dayOf(ts: real): string {
        return Qt.formatDate(new Date(ts * 1000), "yyyy-MM-dd");
    }

    function dayTitle(ts: real): string {
        const date = new Date(ts * 1000);
        const today = new Date();
        const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
        if (history.dayOf(ts) === Qt.formatDate(today, "yyyy-MM-dd"))
            return "Today";
        if (history.dayOf(ts) === Qt.formatDate(yesterday, "yyyy-MM-dd"))
            return "Yesterday";
        return Qt.formatDate(date, date.getFullYear() === today.getFullYear() ? "dddd d MMMM" : "dddd d MMMM yyyy");
    }

    function escaped(text: string): string {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    // The text with every searched word lit, as rich text; plain text needs none of this.
    function marked(text: string): string {
        const words = history.query.split(/\s+/).filter(w => w.length > 0)
            .map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
        let html = history.escaped(text);
        if (words.length)
            html = html.replace(new RegExp(`(${words.map(w => history.escaped(w)).join("|")})`, "gi"),
                `<span style="background-color:${history.style.dictating}; color:${history.style.base};">$1</span>`);
        return `<div style="white-space: pre-wrap;">${html}</div>`;
    }

    function url(params: var): string {
        const parts = Object.keys(params).filter(k => params[k] !== "" && params[k] !== 0)
            .map(k => `${k}=${encodeURIComponent(params[k])}`);
        return `${history.hubUrl.replace(/\/+$/, "")}/v1/history?${parts.join("&")}`;
    }

    function fetch(params: var, done: var) {
        if (!history.hubUrl || !history.hubToken) {
            history.error = "No hub address or token in config.toml.";
            return;
        }
        const xhr = new XMLHttpRequest();
        const generation = history.generation;
        xhr.onreadystatechange = () => {
            if (xhr.readyState !== XMLHttpRequest.DONE || generation !== history.generation)
                return;
            let reply = null;
            try {
                reply = JSON.parse(xhr.responseText);
            } catch (e) {}
            if (xhr.status === 200 && reply) {
                history.error = "";
                done(reply);
            } else {
                history.error = xhr.status === 0 ? "The hub cannot be reached." : `The hub answered ${xhr.status}.`;
                history.loading = false;
            }
        };
        xhr.open("GET", history.url(params));
        xhr.setRequestHeader("X-Diablo-Token", history.hubToken);
        xhr.send();
    }

    function row(m: var): var {
        return { mid: m.id, ts: m.ts, role: m.role ?? "", body: m.text ?? "", channel: m.channel ?? "", spoken: !!m.spoken,
            day: history.dayOf(m.ts) };
    }

    // From the newest again: the search or the channel changed, or the page was opened.
    function reload() {
        history.generation++;
        rows.clear();
        history.oldest = 0;
        history.newest = 0;
        history.more = true;
        history.loading = false;
        history.loaded = false;
        history.older();
    }

    function older() {
        if (history.loading || !history.more)
            return;
        history.loading = true;
        history.fetch({ before: history.oldest, q: history.query, channel: history.channel }, reply => {
            const got = reply.messages ?? [];
            for (let i = got.length - 1; i >= 0; i--)
                rows.append(history.row(got[i]));
            if (got.length) {
                history.oldest = got[0].id;
                history.newest = Math.max(history.newest, got[got.length - 1].id);
            }
            history.more = !!reply.more;
            history.channels = reply.channels ?? {};
            history.loading = false;
            history.loaded = true;
        });
    }

    // New lines while it is open go in at the bottom. Too many at once and it starts over.
    function newer() {
        if (history.loading || !history.loaded)
            return;
        history.fetch({ q: history.query, channel: history.channel }, reply => {
            const got = (reply.messages ?? []).filter(m => m.id > history.newest);
            history.channels = reply.channels ?? history.channels;
            if (!got.length)
                return;
            if (got.length === (reply.messages ?? []).length && reply.more && history.newest > 0)
                return history.reload();
            const atBottom = list.atYEnd;
            for (const m of got)
                rows.insert(0, history.row(m));
            history.newest = got[got.length - 1].id;
            if (history.oldest === 0)
                history.oldest = got[0].id;
            if (atBottom)
                list.positionViewAtBeginning();
        });
    }

    function searchFor(text: string) {
        search.text = text;
        history.query = text.trim();
        history.reload();
    }

    function key(event: var) {
        const page = list.height * 0.9;
        const keys = { [Qt.Key_Up]: -wheel.notch, [Qt.Key_Down]: wheel.notch, [Qt.Key_PageUp]: -page, [Qt.Key_PageDown]: page };
        if (event.key in keys && !(event.modifiers & Qt.ControlModifier)) {
            wheel.scrollBy(keys[event.key]);
            event.accepted = true;
        } else if (event.key === Qt.Key_Home) {
            wheel.scrollTo(wheel.top);
            event.accepted = true;
        } else if (event.key === Qt.Key_End) {
            wheel.scrollTo(wheel.bottom);
            event.accepted = true;
        }
    }

    function reloadSoon() {
        if (history.active)
            Qt.callLater(history.reload);
    }

    onActiveChanged: history.reloadSoon()
    onHubUrlChanged: history.reloadSoon()
    onHubTokenChanged: history.reloadSoon()
    onChannelChanged: history.reloadSoon()

    Timer {
        id: typing

        interval: 280
        onTriggered: if (history.query !== search.text.trim()) {
            history.query = search.text.trim();
            history.reload();
        }
    }

    Timer {
        interval: 4000
        repeat: true
        running: history.active && history.loaded
        onTriggered: history.newer()
    }

    ListModel {
        id: rows
    }

    // The search field and one chip per channel.
    Item {
        id: bar

        width: parent.width
        height: Math.max(36, chips.implicitHeight)

        Rectangle {
            id: field

            width: Math.min(280, parent.width * 0.34)
            height: 36
            radius: 18
            color: search.activeFocus ? history.style.base : (fieldMouse.containsMouse ? history.style.raised : "transparent")
            border.width: 1
            border.color: search.activeFocus ? history.style.quiet : history.style.line

            MouseArea {
                id: fieldMouse

                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.IBeamCursor
                onClicked: search.forceActiveFocus()
            }

            TextInput {
                id: search

                x: 16
                width: parent.width - 32 - (clear.visible ? 22 : 0)
                anchors.verticalCenter: parent.verticalCenter
                color: history.style.strong
                selectionColor: history.style.line
                font.family: history.style.family
                font.pixelSize: history.style.normal
                clip: true
                onTextChanged: typing.restart()
                Keys.onEscapePressed: event => {
                    if (search.text.length) {
                        search.text = "";
                        event.accepted = true;
                    } else {
                        event.accepted = false;
                    }
                }

                Text {
                    visible: !search.text.length
                    text: "Search"
                    color: history.style.quiet
                    font: search.font
                }
            }

            Text {
                id: clear

                visible: search.text.length > 0
                anchors.right: parent.right
                anchors.rightMargin: 14
                anchors.verticalCenter: parent.verticalCenter
                text: "✕"
                color: clearMouse.containsMouse ? history.style.strong : history.style.quiet
                font.pixelSize: 12

                MouseArea {
                    id: clearMouse

                    anchors.fill: parent
                    anchors.margins: -6
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: search.text = ""
                }
            }
        }

        Flow {
            id: chips

            x: field.width + 12
            y: 2
            width: parent.width - x
            spacing: 6

            Repeater {
                model: [""].concat(history.channelList)

                Rectangle {
                    id: chip

                    required property string modelData
                    readonly property bool chosen: history.channel === modelData
                    readonly property int count: modelData ? (history.channels[modelData] ?? 0)
                        : Object.values(history.channels).reduce((a, b) => a + b, 0)

                    implicitWidth: chipText.implicitWidth + 24
                    height: 32
                    radius: 16
                    color: chip.chosen ? history.style.line : (chipMouse.containsMouse ? history.style.raised : "transparent")
                    border.width: 1
                    border.color: history.style.line

                    Text {
                        id: chipText

                        anchors.centerIn: parent
                        textFormat: Text.StyledText
                        text: (chip.modelData ? history.nameOf(chip.modelData) : "All")
                            + (chipMouse.containsMouse || chip.chosen ? `&nbsp;&nbsp;<font color="${history.style.quiet}">${chip.count}</font>` : "")
                        color: chip.chosen ? history.style.strong : history.style.text
                        font.family: history.style.family
                        font.pixelSize: history.style.normal
                    }

                    MouseArea {
                        id: chipMouse

                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: history.channel = chip.modelData
                    }
                }
            }
        }
    }

    ListView {
        id: list

        y: bar.height + 14
        width: parent.width
        height: parent.height - y
        clip: true
        model: rows
        verticalLayoutDirection: ListView.BottomToTop
        interactive: false
        cacheBuffer: 600
        boundsBehavior: Flickable.StopAtBounds

        WheelStep {
            id: wheel

            flick: list
        }

        delegate: Item {
            id: line

            required property int index
            required property int mid
            required property real ts
            required property string role
            required property string body
            required property string channel
            required property bool spoken
            required property string day
            readonly property bool mine: line.role === "user"
            readonly property bool firstOfDay: line.index === rows.count - 1 || rows.get(line.index + 1).day !== line.day
            readonly property bool long: message.implicitHeight > 260
            property bool open: false

            width: list.width
            height: content.implicitHeight + 6

            Component.onCompleted: if (line.index === rows.count - 1)
                history.older()

            Column {
                id: content

                width: parent.width
                spacing: 0

                Item {
                    width: parent.width
                    height: line.firstOfDay ? 44 : 0
                    visible: line.firstOfDay

                    Rectangle {
                        anchors.verticalCenter: dayText.verticalCenter
                        width: dayText.x - 14
                        height: 1
                        color: history.style.line
                    }

                    Rectangle {
                        anchors.verticalCenter: dayText.verticalCenter
                        x: dayText.x + dayText.width + 14
                        width: parent.width - x
                        height: 1
                        color: history.style.line
                    }

                    Text {
                        id: dayText

                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 14
                        text: history.dayTitle(line.ts)
                        color: history.style.quiet
                        font.family: history.style.family
                        font.pixelSize: history.style.small
                        font.letterSpacing: 1
                        font.capitalization: Font.AllUppercase
                    }
                }

                Rectangle {
                    id: card

                    width: parent.width
                    height: cardColumn.implicitHeight + 20
                    radius: 10
                    color: hover.hovered ? history.style.raised : "transparent"

                    HoverHandler {
                        id: hover
                    }

                    Rectangle {
                        x: 0
                        y: 10
                        width: 3
                        height: parent.height - 20
                        radius: 1.5
                        color: !line.mine ? history.style.speaking : line.spoken ? history.style.listening : history.style.faint
                    }

                    Column {
                        id: cardColumn

                        x: 16
                        y: 10
                        width: parent.width - 30
                        spacing: 4

                        Item {
                            width: parent.width
                            height: 20

                            Row {
                                spacing: 8
                                anchors.verticalCenter: parent.verticalCenter

                                Text {
                                    text: line.mine ? "You" : "Diablo"
                                    color: line.mine ? history.style.strong : history.style.speaking
                                    font.family: history.style.family
                                    font.pixelSize: history.style.normal
                                    font.weight: Font.DemiBold
                                }

                                Rectangle {
                                    visible: line.mine
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: howText.implicitWidth + 14
                                    height: 18
                                    radius: 9
                                    color: "transparent"
                                    border.width: 1
                                    border.color: line.spoken ? history.style.listening : history.style.faint

                                    Text {
                                        id: howText

                                        anchors.centerIn: parent
                                        text: line.spoken ? "spoken" : "typed"
                                        color: line.spoken ? history.style.listening : history.style.quiet
                                        font.family: history.style.family
                                        font.pixelSize: history.style.small
                                    }
                                }

                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: history.nameOf(line.channel)
                                    color: history.style.quiet
                                    font.family: history.style.family
                                    font.pixelSize: history.style.small + 1
                                }
                            }

                            Text {
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                text: hover.hovered ? Qt.formatDateTime(new Date(line.ts * 1000), "dddd d MMMM yyyy, HH:mm:ss")
                                    : Qt.formatTime(new Date(line.ts * 1000), "HH:mm")
                                color: hover.hovered ? history.style.text : history.style.quiet
                                font.family: history.style.family
                                font.pixelSize: history.style.small + 1
                            }
                        }

                        Item {
                            width: parent.width
                            height: line.long && !line.open ? 240 : message.implicitHeight
                            clip: true

                            TextEdit {
                                id: message

                                width: parent.width
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.Wrap
                                textFormat: history.query ? TextEdit.RichText : line.mine ? TextEdit.PlainText : TextEdit.MarkdownText
                                text: history.query ? history.marked(line.body) : line.body
                                color: line.mine ? history.style.strong : history.style.text
                                selectionColor: history.style.line
                                selectedTextColor: history.style.strong
                                font.family: history.style.family
                                font.pixelSize: 15
                            }
                        }

                        Rectangle {
                            visible: line.long
                            width: moreText.implicitWidth + 24
                            height: 28
                            radius: 14
                            color: moreMouse.containsMouse ? history.style.line : "transparent"
                            border.width: 1
                            border.color: history.style.line

                            Text {
                                id: moreText

                                anchors.centerIn: parent
                                text: line.open ? "Show less" : "Show all"
                                color: history.style.text
                                font.family: history.style.family
                                font.pixelSize: history.style.small + 1
                            }

                            MouseArea {
                                id: moreMouse

                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: line.open = !line.open
                            }
                        }
                    }
                }
            }
        }
    }

    // Where in the history the view is, along the right edge.
    Rectangle {
        readonly property real share: list.visibleArea.heightRatio

        visible: share < 1 && rows.count > 0
        x: parent.width - 4
        y: list.y + list.visibleArea.yPosition * list.height
        width: 4
        height: Math.max(24, share * list.height)
        radius: 2
        color: history.style.faint
        opacity: 0.6
    }

    Text {
        anchors.centerIn: list
        visible: rows.count === 0 && (history.loaded || history.error !== "")
        text: history.error || (history.query || history.channel ? "Nothing matches." : "Nothing said yet.")
        color: history.error ? history.style.trouble : history.style.quiet
        font.family: history.style.family
        font.pixelSize: 16
    }
}
