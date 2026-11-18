import QtQuick
import QtQuick.Shapes
import "marks.js" as Mk

Item {
    id: marksRoot

    required property var style
    property var marks: []
    property bool shown: true
    property int stagger: 140

    opacity: marksRoot.shown ? 1 : 0

    Behavior on opacity { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }

    Repeater {
        model: marksRoot.marks

        Loader {
            required property var modelData
            required property int index
            readonly property var mark: modelData
            readonly property int delay: index * marksRoot.stagger

            anchors.fill: parent
            z: modelData.kind === "spotlight" ? -1 : modelData.kind === "highlight" ? -0.5 : modelData.kind === "node" ? 0.5 : 0
            sourceComponent: ({
                ring: ringMark, underline: underlineMark, arrow: arrowMark, label: labelMark, spotlight: spotlightMark,
                highlight: highlightMark, num: numMark, check: tickMark, cross: tickMark, node: nodeMark, edge: edgeMark,
                title: titleMark
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

            readonly property string outline: Mk.nodePath(node.box, node.mark.shape ?? "box")
            readonly property real fill: Math.max(0, Math.min(1, (node.t - 0.35) / 0.45))
            duration: 620

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    fillColor: Qt.rgba(marksRoot.style.overlay.r, marksRoot.style.overlay.g, marksRoot.style.overlay.b, 0.96 * node.fill)
                    strokeColor: "transparent"
                    strokeWidth: -1

                    PathSvg {
                        path: node.outline
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.halo
                    strokeWidth: 6
                    trim.end: node.t

                    PathSvg {
                        path: node.outline
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 2.5
                    trim.end: node.t

                    PathSvg {
                        path: node.outline
                    }
                }
            }

            Text {
                x: node.box.x + 10
                y: node.box.y
                width: node.box.w - 20
                height: node.box.h
                opacity: node.fill
                text: node.mark.text ?? ""
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                color: marksRoot.style.ink
                font.family: marksRoot.style.family
                font.pixelSize: node.mark.size ?? 17
                font.weight: Font.DemiBold
            }
        }
    }

    Component {
        id: edgeMark

        Drawn {
            id: edge

            readonly property var e: Mk.edgePath(edge.mark.start ?? ({ x: 0, y: 0 }), edge.mark.end ?? ({ x: 0, y: 0 }), edge.mark.via ?? null)
            readonly property real head: Math.max(0, Math.min(1, (edge.t - 0.78) / 0.22))
            readonly property real font: Math.max(11, (edge.mark.size ?? 17) - 4)
            readonly property color under: marksRoot.style.halo
            duration: 520

            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer

                Pen {
                    strokeColor: edge.under
                    strokeWidth: 7
                    trim.end: edge.t
                    startX: edge.e.x1
                    startY: edge.e.y1

                    PathQuad {
                        controlX: edge.e.cx
                        controlY: edge.e.cy
                        x: edge.e.x2
                        y: edge.e.y2
                    }
                }

                Pen {
                    strokeColor: marksRoot.style.ember
                    strokeWidth: 3
                    trim.end: edge.t
                    startX: edge.e.x1
                    startY: edge.e.y1

                    PathQuad {
                        controlX: edge.e.cx
                        controlY: edge.e.cy
                        x: edge.e.x2
                        y: edge.e.y2
                    }
                }
            }

            Shape {
                anchors.fill: parent
                opacity: edge.head
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    fillColor: marksRoot.style.ember
                    strokeColor: edge.under
                    strokeWidth: 2
                    joinStyle: ShapePath.RoundJoin

                    PathPolyline {
                        path: [Qt.point(edge.e.hx1, edge.e.hy1), Qt.point(edge.e.x2, edge.e.y2), Qt.point(edge.e.hx2, edge.e.hy2),
                            Qt.point(edge.e.hx1, edge.e.hy1)]
                    }
                }
            }

            Rectangle {
                visible: (edge.mark.text ?? "") !== ""
                x: edge.e.mx - width / 2
                y: edge.e.my - height / 2
                width: tag.implicitWidth + 14
                height: tag.implicitHeight + 6
                radius: height / 2
                opacity: edge.head
                color: marksRoot.style.raised
                border.width: 1
                border.color: marksRoot.style.line

                Text {
                    id: tag

                    anchors.centerIn: parent
                    text: edge.mark.text ?? ""
                    color: marksRoot.style.text
                    font.family: marksRoot.style.family
                    font.pixelSize: edge.font
                }
            }
        }
    }

    Component {
        id: titleMark

        Drawn {
            id: heading

            duration: 700
            easing: Easing.OutCubic

            Rectangle {
                x: heading.box.x
                y: heading.box.y
                width: heading.box.w
                height: heading.box.h
                radius: heading.box.h / 2
                opacity: Math.min(1, heading.t * 2.5)
                color: Qt.rgba(marksRoot.style.base.r, marksRoot.style.base.g, marksRoot.style.base.b, 0.94)
                border.width: 1
                border.color: marksRoot.style.line

                Item {
                    anchors.fill: parent
                    anchors.leftMargin: 20
                    anchors.rightMargin: 20
                    clip: true

                    Item {
                        width: parent.width * heading.t
                        height: parent.height
                        clip: true

                        Text {
                            width: heading.box.w - 40
                            height: heading.box.h
                            text: heading.mark.text ?? ""
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            color: marksRoot.style.strong
                            font.family: marksRoot.style.family
                            font.pixelSize: heading.mark.size ?? 26
                            font.weight: Font.DemiBold
                        }
                    }
                }
            }
        }
    }
}
