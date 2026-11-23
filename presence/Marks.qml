import QtQuick
import QtQuick.Shapes
import "marks.js" as Mk

Item {
    id: marksRoot

    required property var style
    property var marks: []
    property bool shown: true
    property int stagger: 140
    readonly property string hand: handFont.status === FontLoader.Ready ? handFont.name : marksRoot.style.family
    readonly property color ground: Qt.rgba(0.075, 0.067, 0.071, 0.75)

    opacity: marksRoot.shown ? 1 : 0

    FontLoader {
        id: handFont

        source: Qt.resolvedUrl("fonts/Caveat.ttf")
    }

    Behavior on opacity { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }

    Repeater {
        model: marksRoot.marks

        Loader {
            required property var modelData
            required property int index
            readonly property var mark: modelData
            readonly property int delay: index * marksRoot.stagger

            anchors.fill: parent
            z: modelData.kind === "spotlight" ? -1 : modelData.kind === "highlight" ? -0.5 : modelData.kind === "node" || modelData.kind === "choice" ? 0.5 : 0
            sourceComponent: ({
                ring: ringMark, underline: underlineMark, arrow: arrowMark, label: labelMark, spotlight: spotlightMark,
                highlight: highlightMark, num: numMark, check: tickMark, cross: tickMark, node: nodeMark, edge: edgeMark,
                title: titleMark, choice: choiceMark
            })[modelData.kind] ?? null
        }
    }

    component Pen: ShapePath {
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin
    }

    component Drawn: Item {
        id: drawn

        readonly property var mark: parent ? parent.mark : ({})
        readonly property var box: drawn.mark.box ?? ({ x: 0, y: 0, w: 0, h: 0 })
        property int delay: parent ? parent.delay : 0
        property int duration: 520
        property int easing: Easing.InOutCubic
        property real t: 0

        SequentialAnimation on t {
            PauseAnimation { duration: drawn.delay }
            NumberAnimation { to: 1; duration: drawn.duration; easing.type: drawn.easing }
        }
    }

    Component {
        id: ringMark

        Drawn {
            id: ring

            readonly property var r: Mk.ringFor(ring.box)
            duration: 640

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                transform: Rotation {
                    origin.x: ring.r.cx
                    origin.y: ring.r.cy
                    angle: -4
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 7
                    trim.end: ring.t

                    PathAngleArc {
                        centerX: ring.r.cx
                        centerY: ring.r.cy
                        radiusX: ring.r.rx
                        radiusY: ring.r.ry
                        startAngle: -160
                        sweepAngle: 300
                    }

                    PathAngleArc {
                        moveToStart: false
                        centerX: ring.r.cx
                        centerY: ring.r.cy
                        radiusX: ring.r.rx + 5
                        radiusY: ring.r.ry + 4
                        startAngle: 140
                        sweepAngle: 95
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3.5
                    trim.end: ring.t

                    PathAngleArc {
                        centerX: ring.r.cx
                        centerY: ring.r.cy
                        radiusX: ring.r.rx
                        radiusY: ring.r.ry
                        startAngle: -160
                        sweepAngle: 300
                    }

                    PathAngleArc {
                        moveToStart: false
                        centerX: ring.r.cx
                        centerY: ring.r.cy
                        radiusX: ring.r.rx + 5
                        radiusY: ring.r.ry + 4
                        startAngle: 140
                        sweepAngle: 95
                    }
                }
            }
        }
    }

    Component {
        id: underlineMark

        Drawn {
            id: line

            readonly property var u: Mk.underlineFor(line.box)
            duration: 420
            easing: Easing.OutCubic

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 6
                    trim.end: line.t
                    startX: line.u.x1
                    startY: line.u.y1

                    PathQuad {
                        controlX: line.u.cx
                        controlY: line.u.cy
                        x: line.u.x2
                        y: line.u.y2
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3
                    trim.end: line.t
                    startX: line.u.x1
                    startY: line.u.y1

                    PathQuad {
                        controlX: line.u.cx
                        controlY: line.u.cy
                        x: line.u.x2
                        y: line.u.y2
                    }
                }
            }
        }
    }

    Component {
        id: arrowMark

        Drawn {
            id: arrow

            readonly property var from: arrow.mark.fromPoint ?? Mk.arrowFrom(arrow.box, { x: 0, y: 0, w: marksRoot.width, h: marksRoot.height }, null, null, null)
            readonly property var a: Mk.arrowPath(arrow.from, arrow.box)
            readonly property real head: Math.max(0, Math.min(1, (arrow.t - 0.82) / 0.18))
            duration: 560

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 7
                    trim.end: arrow.t
                    startX: arrow.a.x1
                    startY: arrow.a.y1

                    PathQuad {
                        controlX: arrow.a.cx
                        controlY: arrow.a.cy
                        x: arrow.a.x2
                        y: arrow.a.y2
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3.5
                    trim.end: arrow.t
                    startX: arrow.a.x1
                    startY: arrow.a.y1

                    PathQuad {
                        controlX: arrow.a.cx
                        controlY: arrow.a.cy
                        x: arrow.a.x2
                        y: arrow.a.y2
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 7
                    trim.end: arrow.head

                    PathPolyline {
                        path: [Qt.point(arrow.a.hx1, arrow.a.hy1), Qt.point(arrow.a.x2, arrow.a.y2), Qt.point(arrow.a.hx2, arrow.a.hy2)]
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3.5
                    trim.end: arrow.head

                    PathPolyline {
                        path: [Qt.point(arrow.a.hx1, arrow.a.hy1), Qt.point(arrow.a.x2, arrow.a.y2), Qt.point(arrow.a.hx2, arrow.a.hy2)]
                    }
                }
            }
        }
    }

    Component {
        id: labelMark

        Drawn {
            id: label

            readonly property bool caption: !!label.mark.caption
            readonly property var spot: Mk.labelSpot(label.box, { w: pill.width, h: pill.height }, { w: marksRoot.width, h: marksRoot.height },
                label.mark.side || (label.caption ? "below" : "right"))
            delay: (parent ? parent.delay : 0) + 160
            duration: 260
            easing: Easing.OutCubic

            Rectangle {
                id: pill

                x: label.spot.x + (label.spot.side === "left" ? 8 : label.spot.side === "right" ? -8 : 0) * (1 - label.t)
                y: label.spot.y + (label.spot.side === "above" ? 6 : label.spot.side === "below" ? -6 : 0) * (1 - label.t)
                width: words.width + 22
                height: words.height + 12
                radius: 6
                opacity: label.t
                color: marksRoot.style.overlay
                border.width: 1.5
                border.color: marksRoot.style.ember

                Text {
                    id: words

                    x: 11
                    y: 6
                    width: label.caption ? Math.min(implicitWidth, 380) : implicitWidth
                    text: label.mark.text ?? ""
                    wrapMode: label.caption ? Text.WordWrap : Text.NoWrap
                    color: marksRoot.style.ink
                    font.family: marksRoot.style.family
                    font.pixelSize: label.caption ? marksRoot.style.normal + 1 : marksRoot.style.normal
                    font.weight: label.caption ? Font.Normal : Font.DemiBold
                }
            }
        }
    }

    Component {
        id: spotlightMark

        Drawn {
            id: spot

            duration: 360
            easing: Easing.OutCubic

            Shape {
                anchors.fill: parent
                opacity: spot.t
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    fillColor: marksRoot.style.scrim
                    fillRule: ShapePath.OddEvenFill
                    strokeColor: "transparent"
                    strokeWidth: -1

                    PathRectangle {
                        x: 0
                        y: 0
                        width: marksRoot.width
                        height: marksRoot.height
                    }

                    PathRectangle {
                        x: spot.box.x - 10
                        y: spot.box.y - 10
                        width: spot.box.w + 20
                        height: spot.box.h + 20
                        radius: 10
                    }
                }
            }
        }
    }

    Component {
        id: highlightMark

        Drawn {
            id: marker

            duration: 380
            easing: Easing.OutCubic

            Rectangle {
                x: marker.box.x - 3
                y: marker.box.y - 2
                width: (marker.box.w + 6) * marker.t
                height: marker.box.h + 4
                radius: 3
                color: Qt.rgba(marksRoot.style.warn.r, marksRoot.style.warn.g, marksRoot.style.warn.b, 0.34)
            }
        }
    }

    Component {
        id: numMark

        Drawn {
            id: num

            readonly property int size: 28
            readonly property var spot: Mk.badgeSpot(num.box, num.size, { w: marksRoot.width, h: marksRoot.height })
            duration: 340
            easing: Easing.OutBack

            Rectangle {
                x: num.spot.x
                y: num.spot.y
                width: num.size
                height: num.size
                radius: num.size / 2
                scale: 0.3 + 0.7 * num.t
                opacity: Math.min(1, num.t * 2)
                color: marksRoot.style.ember
                border.width: 2
                border.color: marksRoot.style.halo

                Text {
                    anchors.centerIn: parent
                    text: String(num.mark.n ?? 1)
                    color: marksRoot.style.emberText
                    font.family: marksRoot.style.family
                    font.pixelSize: marksRoot.style.normal + 1
                    font.weight: Font.Bold
                }
            }
        }
    }

    Component {
        id: tickMark

        Drawn {
            id: tick

            readonly property bool good: tick.mark.kind === "check"
            readonly property int size: 30
            readonly property var spot: Mk.labelSpot(tick.box, { w: tick.size, h: tick.size }, { w: marksRoot.width, h: marksRoot.height }, "right", 8)
            duration: 420
            easing: Easing.OutCubic

            Shape {
                x: tick.spot.x
                y: tick.spot.y
                width: tick.size
                height: tick.size
                preferredRendererType: Shape.CurveRenderer

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 7
                    trim.end: tick.t

                    PathSvg {
                        path: tick.good ? "M 5 16 L 12 23 L 26 7" : "M 7 7 L 23 23 M 23 7 L 7 23"
                    }
                }

                Pen {
                    strokeColor: tick.good ? marksRoot.style.ok : marksRoot.style.fail
                    strokeWidth: 4
                    trim.end: tick.t

                    PathSvg {
                        path: tick.good ? "M 5 16 L 12 23 L 26 7" : "M 7 7 L 23 23 M 23 7 L 7 23"
                    }
                }
            }
        }
    }

    Component {
        id: nodeMark

        Drawn {
            id: node

            readonly property var sk: Mk.sketchNode(node.box, node.mark.shape ?? "box", node.mark.text ?? "")
            readonly property real fill: Mk.phase(node.t, 0.3, 0.75)
            duration: 900
            easing: Easing.InOutSine

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    fillColor: Qt.rgba(marksRoot.style.overlay.r, marksRoot.style.overlay.g, marksRoot.style.overlay.b, 0.94 * node.fill)
                    strokeColor: "transparent"
                    strokeWidth: -1

                    PathSvg {
                        path: node.sk.fill
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 6
                    trim.end: Mk.phase(node.t, 0, 0.7)

                    PathSvg {
                        path: node.sk.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 2.8
                    trim.end: Mk.phase(node.t, 0, 0.7)

                    PathSvg {
                        path: node.sk.first
                    }
                }

                Pen {
                    strokeColor: Qt.rgba(marksRoot.style.ember.r, marksRoot.style.ember.g, marksRoot.style.ember.b, 0.7)
                    strokeWidth: 1.6
                    trim.end: Mk.phase(node.t, 0.45, 1)

                    PathSvg {
                        path: node.sk.second
                    }
                }
            }

            Text {
                x: node.box.x + 8
                y: node.box.y
                width: node.box.w - 16
                height: node.box.h
                opacity: node.fill
                text: node.mark.text ?? ""
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                lineHeight: 0.85
                fontSizeMode: Text.Fit
                minimumPixelSize: 12
                color: marksRoot.style.ink
                font.family: marksRoot.hand
                font.pixelSize: Math.round((node.mark.size ?? 17) * 1.45)
                font.weight: Font.DemiBold
            }
        }
    }

    Component {
        id: choiceMark

        Drawn {
            id: choice

            readonly property bool pick: choice.mark.pick === true
            readonly property real size: choice.mark.size ?? 20
            readonly property real pad: Math.round(choice.size * 0.9)
            readonly property var badge: ({ x: choice.box.x + choice.pad, y: choice.box.y + choice.pad, w: choice.size * 1.9, h: choice.size * 1.9 })
            readonly property var sk: Mk.sketchNode(choice.box, "box", choice.mark.text ?? "")
            readonly property var outer: Mk.sketchNode({ x: choice.box.x - 8, y: choice.box.y - 8, w: choice.box.w + 16, h: choice.box.h + 16 }, "box",
                (choice.mark.text ?? "") + "|pick")
            readonly property var ring: Mk.sketchNode(choice.badge, "round", `${choice.mark.num ?? ""}`)
            readonly property real fill: Mk.phase(choice.t, 0.3, 0.7)
            readonly property real words: Mk.phase(choice.t, 0.5, 0.9)
            duration: 1100
            easing: Easing.InOutSine

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    fillColor: Qt.rgba(marksRoot.style.overlay.r, marksRoot.style.overlay.g, marksRoot.style.overlay.b, 0.94 * choice.fill)
                    strokeColor: "transparent"
                    strokeWidth: -1

                    PathSvg {
                        path: choice.sk.fill
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 6
                    trim.end: Mk.phase(choice.t, 0, 0.6)

                    PathSvg {
                        path: choice.sk.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: choice.pick ? 3.6 : 2.6
                    trim.end: Mk.phase(choice.t, 0, 0.6)

                    PathSvg {
                        path: choice.sk.first
                    }
                }

                Pen {
                    strokeColor: Qt.rgba(marksRoot.style.ember.r, marksRoot.style.ember.g, marksRoot.style.ember.b, 0.7)
                    strokeWidth: 1.6
                    trim.end: Mk.phase(choice.t, 0.4, 0.9)

                    PathSvg {
                        path: choice.sk.second
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 2.4
                    trim.end: Mk.phase(choice.t, 0.35, 0.75)

                    PathSvg {
                        path: choice.ring.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: choice.pick ? 6 : 0
                    trim.end: choice.pick ? Mk.phase(choice.t, 0.6, 1) : 0

                    PathSvg {
                        path: choice.outer.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: choice.pick ? 3 : 0
                    trim.end: choice.pick ? Mk.phase(choice.t, 0.6, 1) : 0

                    PathSvg {
                        path: choice.outer.first
                    }
                }
            }

            Text {
                x: choice.badge.x
                y: choice.badge.y
                width: choice.badge.w
                height: choice.badge.h
                opacity: choice.words
                text: `${choice.mark.num ?? ""}`
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                color: marksRoot.style.ember
                font.family: marksRoot.hand
                font.pixelSize: Math.round(choice.size * 1.5)
                font.weight: Font.Bold
            }

            Text {
                id: choiceLabel

                x: choice.badge.x + choice.badge.w + choice.pad * 0.7
                y: choice.box.y + choice.pad * 0.6
                width: choice.box.x + choice.box.w - choice.pad - x
                height: choice.badge.h + choice.pad * 0.4
                opacity: choice.words
                text: choice.mark.text ?? ""
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                lineHeight: 0.85
                fontSizeMode: Text.Fit
                minimumPixelSize: 14
                color: marksRoot.style.strong
                font.family: marksRoot.hand
                font.pixelSize: Math.round(choice.size * 1.6)
                font.weight: Font.Bold
            }

            Text {
                x: choice.box.x + choice.pad
                y: choiceLabel.y + choiceLabel.height + choice.pad * 0.3
                width: choice.box.w - choice.pad * 2
                height: choice.box.y + choice.box.h - choice.pad * 0.7 - y
                visible: text !== ""
                opacity: choice.words * 0.92
                text: choice.mark.detail ?? ""
                wrapMode: Text.WordWrap
                maximumLineCount: 4
                lineHeight: 0.9
                fontSizeMode: Text.Fit
                minimumPixelSize: 15
                color: marksRoot.style.ink
                font.family: marksRoot.hand
                font.pixelSize: Math.round(choice.size * 1.2)
                font.weight: Font.DemiBold
            }

            Rectangle {
                visible: choice.pick
                x: choice.box.x + choice.box.w - width - choice.pad
                y: choice.box.y - height / 2 - 8
                width: pickWord.implicitWidth + 24
                height: pickWord.implicitHeight + 4
                radius: height / 2
                rotation: -3
                opacity: Mk.phase(choice.t, 0.75, 1)
                color: marksRoot.style.ember

                Text {
                    id: pickWord

                    anchors.centerIn: parent
                    text: "Recommended"
                    color: marksRoot.style.emberText
                    font.family: marksRoot.hand
                    font.pixelSize: Math.round(choice.size * 1.25)
                    font.weight: Font.Bold
                }
            }
        }
    }

    Component {
        id: edgeMark

        Drawn {
            id: edge

            readonly property var e: Mk.sketchEdge(edge.mark.start ?? ({ x: 0, y: 0 }), edge.mark.end ?? ({ x: 0, y: 0 }), edge.mark.via ?? null,
                edge.mark.text ?? "")
            readonly property real line: Mk.phase(edge.t, 0, 0.62)
            readonly property real head: Mk.phase(edge.t, 0.62, 0.85)
            readonly property real tag: Mk.phase(edge.t, 0.7, 1)
            duration: 760
            easing: Easing.InOutSine

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 6
                    trim.end: edge.line

                    PathSvg {
                        path: edge.e.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 2.8
                    trim.end: edge.line

                    PathSvg {
                        path: edge.e.first
                    }
                }

                Pen {
                    strokeColor: Qt.rgba(marksRoot.style.ember.r, marksRoot.style.ember.g, marksRoot.style.ember.b, 0.55)
                    strokeWidth: 1.4
                    trim.end: Mk.phase(edge.t, 0.3, 0.8)

                    PathSvg {
                        path: edge.e.second
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 6
                    trim.end: edge.head

                    PathSvg {
                        path: edge.e.head
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3
                    trim.end: edge.head

                    PathSvg {
                        path: edge.e.head
                    }
                }
            }

            Rectangle {
                readonly property real gap: 8
                visible: tag.text !== ""
                x: edge.e.beside ? edge.e.mx + edge.e.nx * gap : edge.e.mx - width / 2
                y: edge.e.beside ? edge.e.my - height / 2 : edge.e.my - height - gap
                width: tag.implicitWidth + 16
                height: tag.implicitHeight + 2
                radius: height / 2
                rotation: -2
                opacity: edge.tag
                color: marksRoot.ground

                Text {
                    id: tag

                    anchors.centerIn: parent
                    text: edge.mark.text ?? ""
                    color: marksRoot.style.ink
                    font.family: marksRoot.hand
                    font.pixelSize: Math.round((edge.mark.size ?? 17) * 1.3)
                    font.weight: Font.Bold
                }
            }
        }
    }

    Component {
        id: titleMark

        Drawn {
            id: heading

            readonly property real textX: heading.box.x + heading.box.w / 2 - words.contentWidth / 2
            readonly property var under: Mk.scribble(heading.textX - 6, heading.textX + words.contentWidth + 6, heading.box.y + heading.box.h - 2,
                heading.mark.text ?? "")
            duration: 1100
            easing: Easing.OutSine

            Rectangle {
                x: heading.textX - 18
                y: heading.box.y + heading.box.h - words.contentHeight - 4
                width: words.contentWidth + 36
                height: words.contentHeight + 12
                radius: height / 2
                rotation: -1
                opacity: Math.min(1, heading.t * 3)
                color: marksRoot.ground
            }

            Item {
                x: heading.textX - 4
                y: heading.box.y - 20
                width: words.contentWidth * heading.t + 8
                height: heading.box.h + 20
                clip: true

                Text {
                    id: words

                    x: 4
                    width: heading.box.w
                    height: parent.height
                    text: heading.mark.text ?? ""
                    verticalAlignment: Text.AlignBottom
                    color: marksRoot.style.strong
                    font.family: marksRoot.hand
                    font.pixelSize: Math.round((heading.mark.size ?? 26) * 1.6)
                    font.weight: Font.Bold
                }
            }

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 7
                    trim.end: Mk.phase(heading.t, 0.55, 0.85)

                    PathSvg {
                        path: heading.under.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3.2
                    trim.end: Mk.phase(heading.t, 0.55, 0.85)

                    PathSvg {
                        path: heading.under.first
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 2
                    trim.end: Mk.phase(heading.t, 0.8, 1)

                    PathSvg {
                        path: heading.under.second
                    }
                }
            }
        }
    }
}
