import QtQuick

// A Flickable's wheel, scrolled the way a browser does it. Qt's own wheel moves 72 px a notch and
// the next notch cancels what the last glide had left, so a fast spin barely went past one notch.
// Here every notch adds 100 px to one target (120 of angle a notch, so the compositor's scroll
// factor still applies) and the content glides there in 200 ms. A touchpad's pixels go on 1:1.
// Qt's Wayland plugin reports a mouse wheel as a touchpad, so the phase tells them apart: a finger
// comes as begin, update, end, a notch with no phase. Sideways scroll passes through.
WheelHandler {
    id: root

    required property Flickable flick
    readonly property real notch: 100
    readonly property real top: root.flick.originY
    readonly property real bottom: Math.max(root.top, root.flick.originY + root.flick.contentHeight - root.flick.height)
    readonly property bool scrolls: root.flick.contentHeight > root.flick.height
    readonly property real target: root.glide.running ? root.glide.to : root.flick.contentY
    // A wheel or a touchpad moved the content by hand: anything else steering it lets go.
    signal began

    function bound(y: real): real {
        return Math.max(root.top, Math.min(y, root.bottom));
    }

    // Keys scroll through here too, so a key and a notch add to the same target.
    function scrollTo(y: real) {
        if (!root.scrolls)
            return;
        root.flick.cancelFlick();
        root.glide.stop();
        root.glide.from = root.flick.contentY;
        root.glide.to = root.bound(y);
        root.glide.start();
    }

    function scrollBy(dy: real) {
        root.scrollTo(root.target + dy);
    }

    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad

    onWheel: event => {
        const sideways = Math.abs(event.angleDelta.x) > Math.abs(event.angleDelta.y) || Math.abs(event.pixelDelta.x) > Math.abs(event.pixelDelta.y);
        if (sideways || !root.scrolls || (event.angleDelta.y === 0 && event.pixelDelta.y === 0)) {
            event.accepted = false;
            return;
        }
        root.began();
        if (event.phase === Qt.NoScrollPhase) {
            const notches = event.angleDelta.y !== 0 ? event.angleDelta.y / 120 : event.pixelDelta.y / 15;
            root.scrollTo(root.target - notches * root.notch);
            return;
        }
        root.glide.stop();
        root.flick.cancelFlick();
        root.flick.contentY = root.bound(root.flick.contentY - event.pixelDelta.y);
    }

    property NumberAnimation glide: NumberAnimation {
        target: root.flick
        property: "contentY"
        duration: 200
        easing.type: Easing.OutCubic
    }

    property Connections hooks: Connections {
        target: root.flick

        function onMovementStarted() {
            root.glide.stop();
        }

        // Content that grows or shrinks under a glide moves its end back inside.
        function onContentHeightChanged() {
            if (root.glide.running && root.glide.to !== root.bound(root.glide.to))
                root.scrollTo(root.glide.to);
        }
    }
}
