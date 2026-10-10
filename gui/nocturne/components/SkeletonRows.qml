import QtQuick
import Nocturne

/*
 * Where the rows will be, drawn before there are any.
 *
 * A list that is waiting on a long job has nothing to show and, until now, showed
 * nothing: a whole panel of background for as long as a scan and a match run take,
 * with the only sign of life a progress bar off in the rail. An empty panel does
 * not read as "working" — it reads as "finished, and there was nothing".
 *
 * So the panel is filled with the shape of the answer. Bars stand where the row
 * cards will land — same inset, same height, same corner radius, taken from the
 * table rather than guessed — so when the rows arrive they arrive into the space
 * that was already being held for them, and nothing jumps.
 *
 * They are deliberately **not** a preview of the columns. One bar per row, all
 * the same, no cover square and no title stub. A skeleton that mimicked the
 * column layout would be making a promise about what is coming — how wide the
 * album column is, whether there is artwork — that it cannot keep, and it would
 * have to be kept in step with a layout the user can reorder and resize. This
 * says only "a row goes here", which is the one thing that is certainly true.
 *
 * The pulse is one animation, not one per bar. Every bar reads `pulse`, so they
 * brighten and dim as a single object rather than a crowd — the same reason
 * TrackTable drives its arrival flash from one table-level `_flashLevel`.
 *
 * Nothing here measures itself. Height and count come from the geometry handed
 * in, never from a positioner's laid-out size, and there is no positioner in the
 * component at all. An item that is not effectively visible does not lay out, and
 * this one spends its life being switched off and on — a component that sized
 * itself by measuring would latch at zero the first time it was hidden and never
 * recover. See the note on `shownCount` in ImportLegend.qml, which is that bug.
 */
Item {
    id: root

    // Whether the placeholders should be on screen. The caller decides; this
    // draws nothing of its own accord.
    property bool active: false

    // Row geometry, handed in by the list so the bars line up with real rows.
    // Defaults match the table's own, for a caller that has nothing to say.
    property int rowHeight: Theme.rowHeight
    property int sideMargin: Theme.gapLg

    // One full brighten-and-dim. Slow on purpose: this is a thing to notice and
    // then stop noticing, and anything faster reads as urgency the wait does not
    // have. Raw milliseconds because the Theme durations are transition lengths
    // — `slow` is 340ms, a third of one half-cycle here.
    property int period: 1800

    // 0 to 1, and the only thing the bars read.
    property real pulse: 0

    // The most bars worth drawing, when the caller knows how many rows are
    // actually coming. 0 means it does not know yet, and the panel fills.
    //
    // Promising twelve rows and delivering four is a worse answer than an empty
    // panel: the placeholder is a claim about what is on its way, and a claim
    // that overshoots collapses when the truth lands.
    property int maxRows: 0

    // How many bars fill the panel, capped by what is coming. Derived from the
    // height given, so it follows a resized window without being told — and
    // fewer than `maxRows` is fine, since a panel that cannot hold them all was
    // always going to scroll.
    readonly property int rows: {
        var fits = Math.max(0, Math.floor(height / rowHeight))
        return maxRows > 0 ? Math.min(fits, maxRows) : fits
    }

    // Faded rather than switched, and on the same duration as the empty state it
    // shares a panel with: the first real rows fade in as these fade out, so the
    // handover is one movement instead of a blink.
    opacity: active ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: Theme.normal } }

    // Gated on `visible` rather than left running, the same bargain EqualizerBars
    // makes: a loop nobody can see is a timer waking the render thread for
    // nothing. `Easing.InOutSine` is the house easing for a symmetric
    // back-and-forth, and its only other use is that meter.
    SequentialAnimation on pulse {
        running: root.visible
        loops: Animation.Infinite

        NumberAnimation {
            to: 1
            duration: root.period / 2
            easing.type: Easing.InOutSine
        }
        NumberAnimation {
            to: 0
            duration: root.period / 2
            easing.type: Easing.InOutSine
        }
    }

    Repeater {
        // Nothing at all until it is wanted. Every track list in the window
        // carries one of these — six of them — and a panel's worth of
        // rectangles each, built at load and never shown, is a hundred items
        // the queue and the library pay for so that the import page can have a
        // waiting state.
        //
        // `rows` above stays the honest count either way, because that is the
        // thing that must not be measured; this is only about what exists.
        model: root.active ? root.rows : 0

        // Positioned by index rather than stacked in a Column — see the note
        // above. `rowHeight` is the pitch the list tiles its rows at, and the
        // 1px top and bottom are the gap TrackTable leaves between one row's
        // backdrop and the next.
        delegate: Rectangle {
            required property int index

            x: root.sideMargin - 8
            y: index * root.rowHeight + 1
            width: Math.max(0, root.width - 2 * (root.sideMargin - 8))
            height: root.rowHeight - 2
            radius: Theme.radius

            // Just under the progress track's own 0.10, so a panel of these
            // reads as quieter than the one bar that is reporting real numbers.
            // Alpha over the theme's text colour rather than a grey, because a
            // literal would be black in both themes and invisible in one.
            color: Theme.alpha(Theme.text, 0.04 + 0.05 * root.pulse)
        }
    }
}
