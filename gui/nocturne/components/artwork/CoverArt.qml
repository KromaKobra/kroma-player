import QtQuick
import QtQuick.Effects
import Nocturne

// Album art with a graceful placeholder and genuinely rounded corners.
//
// Three sources, tried in order: the cached cover hash, a remote URL (web
// search results have no cached art), and a local file path (import previews).
// `tier` maps onto the backend's stored thumbnail sizes: 128 or below selects
// the thumbnail tier, anything larger selects the 512px tier.
//
// Rounding goes through MultiEffect rather than `clip`, which in Qt Quick only
// ever clips to the bounding rectangle and would leave square corners.
//
// `maskSource` matters in lists. Left null, every instance builds its own
// layer texture for the rounded rectangle — and in a scrolling view, where
// delegates are created and destroyed constantly, some of those textures are
// not ready when the effect first draws. The effect then renders an empty mask
// and never repaints, leaving that row with no artwork until the delegate is
// rebuilt. It was a different handful of rows every time, which is what made
// it look random.
//
// Measured against a 288-track library, scrolling the whole list: 1-3% of rows
// blank with a mask per row, 0 with one shared mask. So any view that shows
// more than a few of these at identical size should declare one mask and hand
// it to all of them.
Item {
    id: root

    property string hash: ""
    property string url: ""
    property string filePath: ""
    property int tier: 128
    property real radius: Theme.radiusSm
    property string placeholderText: ""
    property bool showPlaceholderGlyph: true

    // A rounded-rect item to use as the corner mask, shared with other covers
    // of the same size. Null means "make a private one", which is fine for the
    // one-off covers that are not in a scrolling list.
    property Item maskSource: null

    readonly property string source: hash !== "" ? "image://cover/" + hash
                                   : (filePath !== "" ? "image://localart/" + filePath
                                   : (url !== "" ? url : ""))
    readonly property bool ready: art.status === Image.Ready && art.implicitWidth > 1

    // ------------------------------------------------------------ placeholder

    Rectangle {
        anchors.fill: parent
        radius: root.radius
        visible: !root.ready
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.alpha(Theme.accent, 0.24) }
            GradientStop { position: 1.0; color: Theme.alpha(Theme.accentAlt, 0.09) }
        }

        Text {
            anchors.centerIn: parent
            visible: root.placeholderText !== ""
            text: Fmt.initial(root.placeholderText)
            color: Theme.alpha(Theme.text, 0.6)
            font.family: Theme.displayFamily
            font.pixelSize: Math.max(11, Math.round(parent.height * 0.38))
            font.weight: Font.DemiBold
        }

        Icon {
            anchors.centerIn: parent
            visible: root.placeholderText === "" && root.showPlaceholderGlyph
            name: "disc"
            size: Math.max(12, Math.round(parent.height * 0.44))
            color: Theme.alpha(Theme.text, 0.38)
        }
    }

    // ------------------------------------------------------------------ art

    Image {
        id: art
        anchors.fill: parent
        source: root.source
        sourceSize.width: root.tier
        sourceSize.height: root.tier
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        visible: false
    }

    // Only built when no shared mask was supplied; `layer.enabled` is what
    // allocates the texture, so leaving it off costs nothing when sharing.
    CornerMask {
        id: privateMask
        anchors.fill: parent
        radius: root.radius
        layer.enabled: root.maskSource === null
    }

    MultiEffect {
        anchors.fill: parent
        source: art
        visible: root.ready
        // Without this the effect pads its render area and the mask no longer
        // lines up with the artwork, shaving one edge.
        autoPaddingEnabled: false
        maskEnabled: true
        maskSource: root.maskSource !== null ? root.maskSource : privateMask

        // MultiEffect applies a mask by thresholding it, and the default spread
        // of 0 makes that threshold a hard cut — every pixel of the mask's
        // antialiased fringe snaps to fully-on or fully-off, so a carefully
        // drawn curve comes out stair-stepped. Giving the threshold some spread
        // maps the fringe back onto a gradient.
        //
        // Measured on a 160px cover with a 28px radius, counting partial-
        // coverage pixels along the corner arc and the average step between
        // neighbouring pixels across the edge:
        //
        //     plain rounded Rectangle   40 px   avg step 86   <- the target
        //     spread 0.0 (the default)   0 px   avg step 255  <- a hard edge
        //     spread 0.5                14 px   avg step 140
        //     spread 1.0                29 px   avg step 85   <- matches
        //
        // The spread is in mask-value units rather than pixels, so it tracks
        // the fringe at any cover size instead of going soft on large ones.
        maskThresholdMin: 0.5
        maskSpreadAtMin: 1.0

        opacity: root.ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }

    // Hairline so pale artwork does not bleed into a pale surface behind it.
    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: "transparent"
        border.width: 1
        border.color: Theme.lineSoft
    }
}
