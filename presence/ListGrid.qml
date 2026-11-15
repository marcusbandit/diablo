import QtQuick
import "place.js" as Place

// A list in the central card: every section a block, the blocks in columns read top to bottom,
// as many columns as there is room for (Place.listFit), so a list of a few dozen items stands
// whole. Clicking an item ticks it. Every block is measured first, out of sight, at the
// column's width; the layout is worked out from those heights.
Item {
    id: grid

    required property var style
    property var sections: []
    property int minColumns: 3
    property int maxColumns: 3
    property real baseHeight: 560
    property real roomHeight: 560
    readonly property int columnWidth: 280
    readonly property int columnGap: 24
    readonly property var gaps: ({ pad: 14, block: 14 })
    signal toggled(int section, int item, bool done)

    property var heads: []
    property var rows: []
    readonly property var layout: Place.listFit(grid.heads, grid.rows, grid.minColumns, grid.maxColumns,
        grid.baseHeight, grid.roomHeight, grid.gaps)

    implicitWidth: grid.layout.columns.length ? grid.layout.columns.length * (grid.columnWidth + grid.columnGap) - grid.columnGap : 0
    implicitHeight: grid.layout.h

    function itemText(item: var): string {
        return typeof item === "string" ? item : String(item?.text ?? "");
    }

    function itemDone(item: var): bool {
        return typeof item === "object" && !!item?.done;
    }

    function measure() {
        const heads = [], rows = [];
        for (let s = 0; s < measurer.count; s++) {
            const block = measurer.itemAt(s);
            if (!block)
                return;
            heads.push(block.headHeight);
            rows.push(block.itemHeights());
        }
        grid.heads = heads;
        grid.rows = rows;
    }

    function measureSoon() {
        Qt.callLater(grid.measure);
    }

    onSectionsChanged: grid.measureSoon()

    Item {
        opacity: 0
        enabled: false

        Repeater {
            id: measurer

            model: grid.sections
            onItemAdded: grid.measureSoon()
            onItemRemoved: grid.measureSoon()

            Block {
                required property int index

                section: index
                to: (grid.sections[index]?.items ?? []).length
                onImplicitHeightChanged: grid.measureSoon()
            }
        }
    }

    Row {
        spacing: grid.columnGap

        Repeater {
            model: grid.layout.columns

            Column {
                required property var modelData

                spacing: grid.gaps.block

                Repeater {
                    model: modelData

                    Block {
                        required property var modelData

                        section: modelData.section
                        from: modelData.from
                        to: modelData.to
                    }
                }
            }
        }
    }

    // A section, or the part of one that a column holds: its heading, then its items.
    component Block: Rectangle {
        id: block

        property int section: 0
        property int from: 0
        property int to: 0
        readonly property var info: grid.sections[block.section] ?? ({})
        readonly property real headHeight: head.height

        function itemHeights(): var {
            const out = [];
            for (let i = 0; i < checks.count; i++)
                out.push(checks.itemAt(i)?.implicitHeight ?? 0);
            return out;
        }

        width: grid.columnWidth
        implicitHeight: content.implicitHeight + 2 * grid.gaps.pad
        radius: 14
        color: grid.style.raised
        border.width: 1
        border.color: grid.style.line

        Column {
            id: content

            x: grid.gaps.pad - 6
            y: grid.gaps.pad
            width: parent.width - 2 * (grid.gaps.pad - 6)

            Item {
                id: head

                width: parent.width
                height: headText.text !== "" ? headText.implicitHeight + 8 : 0

                Text {
                    id: headText

                    x: 6
                    width: parent.width - 12
                    text: block.info.title ?? ""
                    visible: text !== ""
                    color: grid.style.quiet
                    font.family: grid.style.family
                    font.pixelSize: grid.style.normal
                    font.letterSpacing: 1
                    font.capitalization: Font.AllUppercase
                    wrapMode: Text.WordWrap
                }
            }

            Repeater {
                id: checks

                model: (block.info.items ?? []).slice(block.from, block.to)

                Check {
                    section: block.section
                    offset: block.from
                }
            }
        }
    }

    // One item: a box, ticked when done, and its words, struck through and faded once done.
    component Check: Rectangle {
        id: check

        required property var modelData
        required property int index
        property int section: 0
        property int offset: 0
        readonly property bool done: grid.itemDone(check.modelData)

        width: parent?.width ?? 0
        implicitHeight: Math.max(30, label.implicitHeight + 10)
        radius: 8
        color: mouse.containsMouse ? grid.style.line : "transparent"

        Rectangle {
            x: 6
            y: 6
            width: 18
            height: 18
            radius: 5
            color: check.done ? grid.style.speaking : "transparent"
            border.width: check.done ? 0 : 1.5
            border.color: mouse.containsMouse ? grid.style.text : grid.style.faint

            Text {
                anchors.centerIn: parent
                visible: check.done
                text: "✓"
                color: grid.style.base
                font.pixelSize: 13
                font.weight: Font.Bold
            }
        }

        Text {
            id: label

            x: 34
            y: 5
            width: parent.width - x - 6
            text: grid.itemText(check.modelData)
            color: grid.style.text
            opacity: check.done ? 0.45 : 1
            font.family: grid.style.family
            font.pixelSize: 16
            font.strikeout: check.done
            wrapMode: Text.WordWrap
        }

        MouseArea {
            id: mouse

            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: grid.toggled(check.section, check.offset + check.index, !check.done)
        }
    }
}
