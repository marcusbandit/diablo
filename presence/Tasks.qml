import QtQuick
import "tasks.js" as TaskRows

// Tasks: every task card the hub holds, from the same feed as the moons (busy.py), so the two
// never disagree. Waiting ones first, saying what they wait for; finished ones leave after a
// short while (TaskRows.rows). A row opens its thread or pull request on a click.
Item {
    id: list

    required property var style
    required property var words
    property var tasks: []
    property bool active: false
    property real now: Date.now() / 1000

    readonly property var rows: TaskRows.rows(list.tasks, list.now)

    function key(event: var) {
        const page = view.height * 0.9;
        const keys = { [Qt.Key_Up]: -wheel.notch, [Qt.Key_Down]: wheel.notch, [Qt.Key_PageUp]: -page, [Qt.Key_PageDown]: page };
        if (event.key in keys) {
            wheel.scrollBy(keys[event.key]);
            event.accepted = true;
        } else if (event.key === Qt.Key_Home || event.key === Qt.Key_End) {
            wheel.scrollTo(event.key === Qt.Key_Home ? wheel.top : wheel.bottom);
            event.accepted = true;
        }
    }

    function hue(state: string): color {
        return { waiting: list.style.listening, working: list.style.thinking, queued: list.style.quiet,
            done: list.style.ok, failed: list.style.fail }[state] ?? list.style.quiet;
    }

    function clock(ts: real): string {
        return Qt.formatTime(new Date(ts * 1000), "HH:mm");
    }

    function stepOf(task: var): string {
        if (task.state === "waiting")
            return task.step || list.words.waitingFallback;
        return task.step || "";
    }

    function detailOf(task: var): string {
        const lines = [];
        if (task.machine)
            lines.push(list.words.onMachine.replace("{machine}", task.machine));
        if (task.started_at)
            lines.push(list.words.startedAt.replace("{time}", list.clock(task.started_at)));
        if (task.finished_at && (task.state === "done" || task.state === "failed"))
            lines.push(list.words.finishedAt.replace("{time}", list.clock(task.finished_at)));
        return lines.join("\n");
    }

    Timer {
        interval: 1000
        repeat: true
        triggeredOnStart: true
        running: list.active
        onTriggered: list.now = Date.now() / 1000
    }

    Flickable {
        id: view

        anchors.fill: parent
        contentWidth: width
        contentHeight: rowColumn.implicitHeight
        interactive: contentHeight > height
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        WheelStep {
            id: wheel

            flick: view
        }

        Column {
            id: rowColumn

            width: view.width
            spacing: 10

            Repeater {
                model: list.rows

                Rectangle {
                    id: row

                    required property var modelData
                    required property int index
                    readonly property bool waiting: row.modelData.state === "waiting"
                    readonly property string step: list.stepOf(row.modelData)

                    width: rowColumn.width
                    implicitHeight: rowText.implicitHeight + 28
                    radius: list.style.radius
                    color: rowMouse.containsMouse ? list.style.line : list.style.raised
                    border.width: 1
                    border.color: row.waiting ? list.style.listening : list.style.line
                    opacity: row.modelData.state === "done" ? 0.75 : 1
                    z: rowMouse.containsMouse ? 1 : 0

                    Behavior on color { ColorAnimation { duration: 120 } }

                    Rectangle {
                        id: chip

                        x: 16
                        y: 15
                        width: 92
                        height: 24
                        radius: 12
                        color: Qt.rgba(list.hue(row.modelData.state).r, list.hue(row.modelData.state).g, list.hue(row.modelData.state).b, 0.16)
                        border.width: 1
                        border.color: list.hue(row.modelData.state)

                        Text {
                            anchors.centerIn: parent
                            text: list.words[row.modelData.state] ?? row.modelData.state
                            color: list.hue(row.modelData.state)
                            font.family: list.style.family
                            font.pixelSize: list.style.small
                            font.weight: Font.DemiBold
                        }
                    }

                    Column {
                        id: rowText

                        x: chip.x + chip.width + 16
                        y: 14
                        width: row.width - x - took.width - 32
                        spacing: 4

                        Text {
                            width: parent.width
                            text: row.modelData.title || row.modelData.id
                            color: list.style.strong
                            font.family: list.style.family
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }

                        Text {
                            width: parent.width
                            text: row.step
                            visible: text.length > 0
                            color: row.waiting ? list.style.listening : list.style.text
                            font.family: list.style.family
                            font.pixelSize: list.style.normal
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                    }

                    Text {
                        id: took

                        anchors.right: parent.right
                        anchors.rightMargin: 16
                        y: 17
                        text: row.modelData.started_at ? TaskRows.taken(TaskRows.takenSeconds(row.modelData, list.now), list.words) : ""
                        color: list.style.quiet
                        font.family: list.style.family
                        font.pixelSize: list.style.normal
                        font.features: { "tnum": 1 }
                    }

                    MouseArea {
                        id: rowMouse

                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: row.modelData.link ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: if (row.modelData.link) Qt.openUrlExternally(row.modelData.link)
                    }

                    // On hover: where it runs and when it started or finished, under the row, or
                    // over it for the last one so the list's edge never cuts it.
                    Rectangle {
                        readonly property string detail: list.detailOf(row.modelData)
                        readonly property bool last: row.index === list.rows.length - 1 && list.rows.length > 1

                        visible: rowMouse.containsMouse && detail !== ""
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        y: last ? -height - 4 : row.height + 4
                        width: tip.implicitWidth + 20
                        height: tip.implicitHeight + 12
                        radius: 8
                        color: list.style.base
                        border.width: 1
                        border.color: list.style.faint

                        Text {
                            id: tip

                            anchors.centerIn: parent
                            text: parent.detail
                            color: list.style.text
                            font.family: list.style.family
                            font.pixelSize: list.style.small
                            lineHeight: 1.2
                        }
                    }
                }
            }
        }
    }

    Text {
        anchors.centerIn: parent
        visible: list.rows.length === 0
        text: list.words.emptyList
        color: list.style.quiet
        font.family: list.style.family
        font.pixelSize: 16
    }
}
