import QtQuick

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
Rectangle {
    color: "black"
    visible: false
    antialiasing: true

    layer.enabled: true
    layer.smooth: true
    layer.samples: 4
}
