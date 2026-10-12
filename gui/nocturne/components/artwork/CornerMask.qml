import QtQuick
import Nocturne

// The rounded-rectangle mask that gives album art its corners.
//
// This is a component rather than three copies of a Rectangle because the
// settings that make a masked corner look right are easy to get wrong and the
// failure is subtle — you get a corner that is *shaped* correctly but visibly
// stair-stepped, which reads as "low quality" long before anyone works out
// why.
//
// Two things matter here:
//
//   antialiasing  the rounded rect is drawn with a soft one-pixel fringe.
//                 Without it the mask is a hard shape and no amount of
//                 filtering downstream can recover the curve.
//
//   layer.smooth  the mask is rendered to a texture, and Qt samples layer
//                 textures nearest-neighbour by default. One mask is shared by
//                 a whole list, so it will not always be sampled at exactly
//                 its own size — nearest-neighbour sampling then throws the
//                 fringe away again.
//
// The other half of the job is on the consumer: MultiEffect applies a mask
// through a threshold, and its default spread of 0 turns the fringe back into
// a hard edge. See CoverArt.qml.
//
// A mask whose size follows a resizable view is given `targetWidth` and
// `targetHeight` instead of `width` and `height`. Every change of size throws
// the multisampled layer away and allocates a new one, which while a sidebar
// edge moves would be every frame. So while LiveResize is active the mask
// holds the size it had. MultiEffect stretches a mask over the artwork's own
// rect, so a held mask still covers the art exactly — only its radius is off
// by the few percent the art has grown or shrunk, while it is in motion — and
// it settles to the exact size, once, when the edge comes to rest. Masks of a
// fixed size go on setting `width` and `height` directly.
Rectangle {
    id: root

    property real targetWidth: -1
    property real targetHeight: -1

    color: "black"
    visible: false
    antialiasing: true

    layer.enabled: true
    layer.smooth: true
    layer.samples: 4

    // Created mid-drag, a mask has no earlier size to hold, so it starts at
    // its target rather than at nothing.
    Component.onCompleted: {
        if (targetWidth >= 0) width = targetWidth
        if (targetHeight >= 0) height = targetHeight
    }

    Binding on width {
        when: root.targetWidth >= 0 && !LiveResize.active
        value: root.targetWidth
        restoreMode: Binding.RestoreNone
    }
    Binding on height {
        when: root.targetHeight >= 0 && !LiveResize.active
        value: root.targetHeight
        restoreMode: Binding.RestoreNone
    }
}
