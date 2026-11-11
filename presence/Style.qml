import QtQuick

// The desktop's own ramp (as banditshell and slip use it), plus one hue per thing Diablo does.
QtObject {
    readonly property var ramp: ["#08090b", "#0f1114", "#181b1f", "#1d2126", "#262b31", "#3a4149",
        "#545d67", "#78838f", "#a5aeb8", "#d0d7dd", "#eef2f5"]
    readonly property color base: ramp[1]
    readonly property color raised: ramp[2]
    readonly property color line: ramp[4]
    readonly property color faint: ramp[6]
    readonly property color quiet: ramp[7]
    readonly property color text: ramp[9]
    readonly property color strong: ramp[10]

    // His states. Asleep is the ramp itself, so a resting orb is part of the desktop.
    readonly property color asleep: ramp[6]
    readonly property color listening: "#7fb3d4"
    readonly property color thinking: "#e0915a"
    readonly property color speaking: "#6fc49a"
    readonly property color control: "#a98fe0"
    readonly property color dictating: "#d8c48c"
    readonly property color trouble: "#f29ba8"

    readonly property string family: Qt.application.font.family
    readonly property int small: 12
    readonly property int normal: 14
    readonly property int big: 30
    readonly property int radius: 14
}
