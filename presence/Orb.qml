import QtQuick
import QtQuick.Effects
import Quickshell

// Diablo himself: his hand-drawn face (assets/faces, four frames a mood, boiling like a
// cartoon) on a small dark disc, tinted and haloed by what he is doing. Every animation runs
// only while it is needed, so a resting face costs next to nothing.
Item {
    id: orb

    required property var style
    property string mood: "asleep"     // the ears' state: asleep, listening, thinking, speaking, off...
    property bool control: false        // speech control is on
    property bool dictating: false
    property bool thinkingBehind: false // an answer still thinking in the background
    property string moment: ""          // a short feeling after something: delighted, sorry, relieved
    property bool presenting: false     // only showing a card: he holds it out, pleased with it
    property bool hovered: false
    property var mouthTrack: ({ start: 0, duration: 0, cues: [] })

    // While a measured track plays, the talking face is drawn in two parts: the head without
    // its mouth, and one of Marcus's mouth drawings for the sound being said this instant.
    readonly property bool lipSync: face === "talking" && (mouthTrack.cues ?? []).length > 0
    property string mouthShape: "rest"
    property int mouthTake: 1
    property string eyeLook: "neutral"
    onLipSyncChanged: eyeLook = "neutral"
    property bool blinking: false

    readonly property bool awake: mood !== "asleep" || control || dictating || thinkingBehind || moment !== "" || presenting

    // Which drawing: what he feels just now wins, then what he is doing.
    readonly property string face: moment !== "" ? moment
        : dictating ? "listening"
        : mood === "listening" ? (control ? "expectant" : "listening")
        : mood === "waking" ? "surprised"
        : mood === "thinking" ? "thinking"
        : mood === "speaking" ? "talking"
        : mood === "untrained" ? "worried"
        : mood === "off" ? "resting"
        : thinkingBehind ? "working"
        : presenting ? "proud"
        : control ? "expectant"
        : "asleep"

    readonly property color hue: moment === "sorry" ? style.trouble
        : moment !== "" ? style.speaking
        : dictating ? style.dictating
        : mood === "listening" ? (control ? style.control : style.listening)
        : mood === "thinking" || mood === "waking" ? style.thinking
        : mood === "speaking" ? style.speaking
        : mood === "off" || mood === "untrained" ? style.trouble
        : thinkingBehind ? style.thinking
        : presenting ? style.listening
        : control ? style.control
        : style.asleep

    implicitWidth: 72
    implicitHeight: 72

    // The halo: speech control and dictation keep a ring around him for as long as they last.
    Rectangle {
        anchors.centerIn: parent
        width: 70
        height: 70
        radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: orb.hue
        opacity: orb.control || orb.dictating ? 0.6 : 0
        scale: orb.control || orb.dictating ? 1 : 0.85

        Behavior on opacity { NumberAnimation { duration: 220 } }
        Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Behavior on border.color { ColorAnimation { duration: 200 } }
    }

    // A soft glow that breathes while he listens and quickens while he speaks.
    Rectangle {
        anchors.centerIn: parent
        width: 60
        height: 60
        radius: width / 2
        color: orb.hue
        opacity: orb.awake ? 0.2 : 0

        Behavior on color { ColorAnimation { duration: 200 } }
        Behavior on opacity { NumberAnimation { duration: 200 } }

        SequentialAnimation on scale {
            running: orb.mood === "listening" || orb.mood === "speaking"
            loops: Animation.Infinite
            alwaysRunToEnd: true
            NumberAnimation { to: orb.mood === "speaking" ? 1.22 : 1.15; duration: orb.mood === "speaking" ? 260 : 900; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: orb.mood === "speaking" ? 260 : 900; easing.type: Easing.InOutSine }
        }
    }

    // The disc his face sits on.
    Rectangle {
        id: disc

        anchors.centerIn: parent
        width: orb.awake || orb.hovered ? 56 : 46
        height: width
        radius: width / 2
        color: Qt.rgba(0.06, 0.07, 0.08, orb.awake || orb.hovered ? 0.94 : 0.7)
        border.width: 1
        border.color: Qt.rgba(orb.hue.r, orb.hue.g, orb.hue.b, orb.awake ? 0.7 : 0.35)

        Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
        Behavior on border.color { ColorAnimation { duration: 200 } }

        // His face: four hand-drawn frames a mood, shown in shuffled order.
        Image {
            id: face

            readonly property int frames: 4
            property int frame: 1

            anchors.centerIn: parent
            // The drawings leave a margin round the face on their 512 canvas; this fills the disc.
            width: parent.width * 1.18
            height: width
            // The repo's own faces, next to presence/: the drawings the apps use.
            // horns alone; the eyes and the mouth are drawn on top as their own layers
            source: orb.lipSync
                ? `file://${Quickshell.shellDir}/../assets/faces/horns/${face.frame}.png`
                : `file://${Quickshell.shellDir}/../assets/faces/${orb.face}/${face.frame}.png`
            sourceSize: Qt.size(160, 160)
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            asynchronous: false
            opacity: orb.awake || orb.hovered ? 1 : 0.85
            onStatusChanged: {
                if (face.status === Image.Error && face.frame !== 1)
                    face.frame = 1;
            }

            layer.enabled: true
            layer.effect: MultiEffect {
                colorization: 1.0
                colorizationColor: Qt.lighter(orb.hue, orb.awake ? 1.25 : 1.7)
            }

            Behavior on opacity { NumberAnimation { duration: 200 } }

            Image {
                anchors.fill: parent
                visible: orb.lipSync
                // the eyes boil with the horns: same frame number, four takes each
                source: orb.lipSync ? `file://${Quickshell.shellDir}/../assets/faces/eyes/${orb.blinking ? "closed" : orb.eyeLook}/${face.frame}.png` : ""
                sourceSize: Qt.size(160, 160)
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
                asynchronous: false
            }

            Image {
                anchors.fill: parent
                visible: orb.lipSync
                source: orb.lipSync ? `file://${Quickshell.shellDir}/../assets/faces/mouths/${orb.mouthShape}/${orb.mouthTake}.png` : ""
                sourceSize: Qt.size(160, 160)
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
                asynchronous: false
            }

            // Plays the track against the wall clock, the same clock the voice stamped it with.
            Timer {
                interval: 16
                repeat: true
                running: orb.lipSync && orb.visible
                onTriggered: {
                    const t = Date.now() / 1000 - orb.mouthTrack.start;
                    let next = "rest";
                    for (const cue of orb.mouthTrack.cues) {
                        if (cue[0] > t)
                            break;
                        next = cue[1];
                    }
                    if (t < 0 || t > orb.mouthTrack.duration)
                        next = "rest";
                    let look = "neutral";
                    for (const cue of (orb.mouthTrack.eyes ?? [])) {
                        if (cue[0] > t)
                            break;
                        look = cue[1];
                    }
                    const speaking = t >= 0 && t <= orb.mouthTrack.duration;
                    if (!speaking)
                        look = "neutral";
                    if (look !== orb.eyeLook) {
                        orb.eyeLook = look;
                        // a new look while he speaks lands with a blink half the time, the way
                        // people re-set their face; the settle back between sentences does not
                        if (speaking && Math.random() < 0.5)
                            blink.start();
                    }
                    if (next !== orb.mouthShape) {
                        // a new take with every new shape keeps the line boiling while he talks
                        let take = 1 + Math.floor(Math.random() * 2);
                        if (take >= orb.mouthTake)
                            take += 1;
                        orb.mouthTake = take;
                        orb.mouthShape = next;
                    }
                }
            }

            // Blinks: every 2 to 6 seconds while the face is assembled, 130 ms with the eyes shut.
            Timer {
                id: blinkClock

                interval: 2000 + Math.random() * 4000
                repeat: true
                running: orb.lipSync && orb.visible
                onTriggered: {
                    blinkClock.interval = 2000 + Math.random() * 4000;
                    blink.start();
                }
            }

            Timer {
                id: blink

                interval: 130
                onRunningChanged: orb.blinking = blink.running
            }

            // The boil: a new frame every 250 ms while he is awake, slower while he sleeps.
            Timer {
                interval: orb.awake ? 250 : 700
                repeat: true
                running: orb.visible
                onTriggered: {
                    let next = 1 + Math.floor(Math.random() * (face.frames - 1));
                    if (next >= face.frame)
                        next += 1;
                    face.frame = next;
                }
            }
        }
    }

    // Thinking in the background: one slow moon circles him while he is free to talk.
    Item {
        anchors.fill: parent
        visible: orb.thinkingBehind && orb.mood !== "thinking"

        RotationAnimation on rotation {
            running: parent.visible
            from: 0
            to: 360
            duration: 2600
            loops: Animation.Infinite
        }

        Rectangle {
            width: 6
            height: 6
            radius: 3
            color: orb.style.thinking
            x: parent.width / 2 - 3 + 32
            y: parent.height / 2 - 3
        }
    }
}
