import QtQuick
import QtQuick.Effects
import Nocturne

// The signature of this interface: the playing track's artwork, blurred to a
// wash of colour behind the whole window, cross-fading whenever the track
// changes. Kroma means colour — the app takes its colour from the music.
//
// Deliberately cheap: it samples the 128px thumbnail tier, since the result is
// blurred past recognition anyway.
//
// The wash is pushed away from the page rather than toward it — down toward
// black behind a dark interface, up toward white behind a light one — so that
// however bright the cover is, it stays behind the words on top of it. Both
// directions come from the theme; see `ambientBrightness`.
Item {
    id: root

    property string hash: ""
    property real intensity: 1.0

    // Two layers so a track change cross-fades instead of popping.
    property string _frontHash: ""
    property string _backHash: ""
    property bool _frontActive: true

    onHashChanged: {
        if (hash === (_frontActive ? _frontHash : _backHash))
            return
        if (_frontActive) {
            _backHash = hash
            _frontActive = false
        } else {
            _frontHash = hash
            _frontActive = true
        }
    }

    Component.onCompleted: {
        _frontHash = hash
        _frontActive = true
    }

    Image {
        id: frontArt
        anchors.fill: parent
        source: root._frontHash !== "" ? "image://cover/" + root._frontHash : ""
        sourceSize.width: 128
        sourceSize.height: 128
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        visible: false
    }

    Image {
        id: backArt
        anchors.fill: parent
        source: root._backHash !== "" ? "image://cover/" + root._backHash : ""
        sourceSize.width: 128
        sourceSize.height: 128
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        visible: false
    }

    MultiEffect {
        anchors.fill: parent
        source: frontArt
        autoPaddingEnabled: false
        blurEnabled: true
        blur: 1.0
        blurMax: 64
        blurMultiplier: 2.0
        saturation: Theme.ambientSaturation
        brightness: Theme.ambientBrightness
        opacity: root._frontActive && root._frontHash !== "" ? root.intensity : 0
        Behavior on opacity { NumberAnimation { duration: Theme.slow } }
    }

    MultiEffect {
        anchors.fill: parent
        source: backArt
        autoPaddingEnabled: false
        blurEnabled: true
        blur: 1.0
        blurMax: 64
        blurMultiplier: 2.0
        saturation: Theme.ambientSaturation
        brightness: Theme.ambientBrightness
        opacity: !root._frontActive && root._backHash !== "" ? root.intensity : 0
        Behavior on opacity { NumberAnimation { duration: Theme.slow } }
    }

    // Scrims: keep text legible no matter how bright the artwork is, and sink
    // the bottom of the window toward the player bar.
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.alpha(Theme.bg, 0.58) }
            GradientStop { position: 0.55; color: Theme.alpha(Theme.bg, 0.80) }
            GradientStop { position: 1.0; color: Theme.alpha(Theme.bg, 0.96) }
        }
    }
}
