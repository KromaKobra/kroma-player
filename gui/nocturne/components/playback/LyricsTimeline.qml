import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

/*
 * The lyrics editor for a document that already has timings: a vertical
 * timeline whose geometry *is* the timing.
 *
 * The row editor beside this one edits timings as numbers, which means the
 * only way to judge whether one is right is to read it and imagine the song.
 * Here every line occupies a block as tall as it is long, the boundaries
 * between blocks are draggable, and a live playhead shows where playback has
 * reached — so judging a timing is looking at it and fixing one is dragging it.
 *
 * The scale rule, with no exceptions and no clamping:
 *
 *     y(t)          = t * pxPerSec
 *     t(y)          = y / pxPerSec
 *     contentHeight = extent * pxPerSec
 *
 * There is no minimum block height and no clamping, at any zoom. A line too
 * short to hold a text field is *drawn* too short to hold one — that is the
 * honest picture, and the zoom is how you get at it. Because nothing is
 * clamped, the playhead, the hit testing and the drag arithmetic are all the
 * two multiplications above rather than a walk over a list of block heights.
 *
 * Every draft mutation leaves by a signal. This component owns geometry and
 * gesture and nothing else — it never computes a timecode, exactly as the row
 * editor never does, so the draft's rules stay in one place in
 * `views/playback/NowPlayingView.qml`.
 */
Item {
    id: root

    property var model                  // the draft's ListModel
    property var seconds: []            // its timecodes as numbers, -1 for none
    property real extent: 1             // how long the timeline is, in seconds
    property real position: 0           // playerVM.position

    // Height is time. 25 is where the editor opens — a three-minute song is
    // ~4500px, which is a long scroll but the one that keeps a four-second
    // line big enough to read. 10 is the far end, where a whole verse fits on
    // screen and the shape of the timing is what you are looking at rather
    // than the words; 400 is the near end, a hundredth of a second per four
    // pixels.
    property real pxPerSec: 25
    readonly property real minPxPerSec: 10
    readonly property real maxPxPerSec: 400

    // Wide enough for a timecode field to show a whole stamp without
    // scrolling it. `NocturneField` spends 26px of its width on its own
    // padding, and "00:23.75" at the body size needs the rest — anything
    // narrower silently shows the tail of the number, which in a column of
    // timecodes reads as a different time rather than as a clipped one.
    readonly property real gutter: 106

    // Whether the view keeps the playhead in sight. Switched off by the user
    // scrolling, back on by the target button or by a seek.
    property bool follow: true

    // While a Ctrl+drag is running, the first line it is carrying — every
    // boundary from here down lights up, because every one of them is moving.
    // -1 when nothing is being shifted.
    property int shiftFrom: -1

    signal seekRequested(real seconds)
    signal boundaryMoved(int index, real seconds)
    signal boundaryShifted(int index, real seconds)
    signal insertRequested(int index)
    signal removeRequested(int index)
    signal wordsEdited(int index, string text)
    signal wordsPasted(int index, string before, string after, var lines)
    signal timeEdited(int index, string text)
    signal timeCommitted(int index)

    // ------------------------------------------------------- the scale rule

    readonly property int count: root.seconds ? root.seconds.length : 0

    // The raw column, -1 for "no time". A blanked timecode field is an
    // ordinary thing to find mid-edit, so everything below reads through here
    // rather than indexing `seconds` directly.
    function timeAt(index) {
        if (!root.seconds || index < 0 || index >= root.seconds.length)
            return -1
        var v = Number(root.seconds[index])
        return isFinite(v) ? v : -1
    }

    // Where a block starts. An untimed line has nowhere of its own to be, so
    // it sits at the top where it is still reachable and still editable —
    // typing a time into it is what moves it.
    function startOf(index) {
        var v = root.timeAt(index)
        return v >= 0 ? v : 0
    }

    // Where it ends: the next *timed* line, or the end of the timeline.
    function endOf(index) {
        for (var j = index + 1; j < root.count; j++) {
            var v = root.timeAt(j)
            if (v >= 0)
                return Math.max(v, root.startOf(index))
        }
        return Math.max(root.extent, root.startOf(index))
    }

    function blockY(index) { return root.startOf(index) * root.pxPerSec }
    function blockHeight(index) {
        return Math.max(0, (root.endOf(index) - root.startOf(index)) * root.pxPerSec)
    }

    // Where a boundary drag is allowed to land. Arithmetic rather than
    // gesture, so it is testable without a pointer — and the whole of why a
    // drag never reorders: in a timeline the order *is* the time, and a line
    // that needs to move far is a line to retype in the gutter, where the
    // draft's own re-sort already handles it.
    function clampBoundary(index, t) {
        var lo = (index > 0 ? root.startOf(index - 1) : 0) + 0.05
        var hi = (index + 1 < root.count ? root.startOf(index + 1) : root.extent) - 0.05
        return Math.max(lo, Math.min(hi, t))
    }

    // Where a Ctrl+drag is allowed to land. The same lower limit — the line
    // above is still standing still — and no upper one at all, because
    // everything below is coming too and there is nothing down there to run
    // into. That asymmetry is the feature: a document that drifts from some
    // point on is fixed by grabbing the boundary where it starts drifting and
    // pulling, and a document that is offset from the first word is fixed by
    // grabbing the very top one.
    function clampShift(index, t) {
        return Math.max((index > 0 ? root.startOf(index - 1) : 0) + 0.05, t)
    }

    // ------------------------------------------------------------- the zoom

    function clampZoom(value) {
        return Math.max(root.minPxPerSec, Math.min(root.maxPxPerSec, value))
    }

    function zoomIn()  { root.zoomAt(1, flick.height / 2) }
    function zoomOut() { root.zoomAt(-1, flick.height / 2) }

    // Anchored: the moment under `viewportY` stays under `viewportY`, which is
    // what makes Ctrl+wheel usable on a dense section instead of a way to lose
    // your place. Both writes are made behind `_adjusting` so the scroll they
    // cause is not mistaken for the user's — see `_setContentY`.
    function zoomAt(direction, viewportY) {
        var was = root.pxPerSec
        var next = root.clampZoom(direction > 0 ? was * 1.25 : was / 1.25)
        if (next === was)
            return
        var t = (flick.contentY + viewportY) / was
        root._adjusting += 1
        // Re-places the playhead, which re-runs the follow — hence the depth
        // count rather than a flag, or that nested write would clear the guard
        // and this one would come back as the user's.
        root.pxPerSec = next
        flick.contentY = root._boundedY(t * next - viewportY)
        root._lastWritten = flick.contentY
        root._adjusting -= 1
    }

    // ------------------------------------------------------- the playhead

    // Written rather than bound, because a seek has to be able to put it
    // somewhere without the crawl below carrying it there.
    property real playheadY: 0

    // `playerVM.position` arrives a few times a second, which at 50px/s is a
    // visible step. Interpolating between updates turns the steps into a
    // continuous crawl — the same thing SeekBar's Behavior does to its fill,
    // written out longhand here because a Behavior's animation is not a root
    // animation node and so cannot be stopped, and stopping it is exactly what
    // a seek has to do: a jump should read as one.
    NumberAnimation {
        id: crawl
        target: root
        property: "playheadY"
        duration: 260
        easing.type: Easing.Linear
    }

    function _placePlayhead(glideThere) {
        var y = root.position * root.pxPerSec
        crawl.stop()
        if (!glideThere) {
            root.playheadY = y
            return
        }
        crawl.from = root.playheadY
        crawl.to = y
        crawl.start()
    }

    // Called from the view when `playerVM` reports a seek. Jumping the
    // playhead is a statement about where you want to be looking, so it turns
    // following back on as well.
    function snap() {
        root._placePlayhead(false)
        root.follow = true
        root._followNow()
    }

    onPositionChanged: root._placePlayhead(true)
    onPxPerSecChanged: root._placePlayhead(false)
    onVisibleChanged: if (root.visible) { root._placePlayhead(false); root._followNow() }
    Component.onCompleted: root._placePlayhead(false)

    // ------------------------------------------------------------ following
    //
    // A flick, the wheel and the scrollbar all reach `contentY` by different
    // routes — SmoothWheel writes it directly, so `onMovementStarted` never
    // fires for it. They converge in exactly one place, which is `contentY`
    // itself: record every value this component writes, and treat any other
    // value as the user's.

    property real _lastWritten: 0
    property int _adjusting: 0

    function _boundedY(y) {
        return Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), y))
    }

    function _setContentY(y) {
        var v = root._boundedY(y)
        root._adjusting += 1
        root._lastWritten = v
        flick.contentY = v
        root._adjusting -= 1
    }

    // 35% down the viewport: enough of what is coming to read ahead, enough of
    // what has gone to see what a drag just did.
    function _followNow() {
        if (!root.follow || !root.visible)
            return
        root._setContentY(root.playheadY - flick.height * 0.35)
    }

    onPlayheadYChanged: root._followNow()
    onFollowChanged: if (root.follow) root._followNow()

    // ----------------------------------------------------------- the surface

    Flickable {
        id: flick
        objectName: "lyricsTimelineFlick"
        anchors.fill: parent
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        contentWidth: width
        contentHeight: root.extent * root.pxPerSec

        ScrollBar.vertical: ScrollBar { width: 6 }

        onContentYChanged: {
            if (root._adjusting > 0)
                return
            if (Math.abs(flick.contentY - root._lastWritten) < 0.5)
                return
            root.follow = false
        }

        Item {
            id: content
            width: flick.width
            height: flick.contentHeight

            // The column the blocks sit in, drawn whole so the stretch above
            // the first line reads as part of the timeline rather than as the
            // end of it — it is a click target like any other, and clicking it
            // seeks into the intro.
            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: root.gutter
                color: Theme.alpha(Theme.text, 0.02)
            }

            // Seek and scrub, beneath everything else. One area for the whole
            // timeline rather than one per block, because the region above the
            // first line wants exactly the same gesture — clicking the intro
            // seeks into it — and because a block's own fields and buttons sit
            // above this and take their own presses first.
            MouseArea {
                id: seekArea
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                // The Flickable would otherwise spend this gesture scrolling.
                preventStealing: true

                property real pressY: 0
                property bool scrubbing: false

                function _seek(y) {
                    root.seekRequested(Math.max(0, y / root.pxPerSec))
                }

                onPressed: function (mouse) {
                    seekArea.pressY = mouse.y
                    seekArea.scrubbing = false
                }
                onPositionChanged: function (mouse) {
                    if (!seekArea.pressed)
                        return
                    // The same 5px the column header and the artist chips
                    // wait for before calling a press a drag.
                    if (!seekArea.scrubbing) {
                        if (Math.abs(mouse.y - seekArea.pressY) < 5)
                            return
                        seekArea.scrubbing = true
                    }
                    seekArea._seek(mouse.y)
                }
                onReleased: function (mouse) {
                    if (!seekArea.scrubbing)
                        seekArea._seek(mouse.y)
                    seekArea.scrubbing = false
                }
                onCanceled: seekArea.scrubbing = false
            }

            // ------------------------------------------------------ blocks

            Repeater {
                id: blocks
                model: root.model

                delegate: Item {
                    id: block

                    required property int index
                    required property string time
                    required property string words

                    readonly property real startTime: root.startOf(index)
                    readonly property real endTime: root.endOf(index)
                    // Whether the playhead is inside this block — which is the
                    // whole of what a timing *means*, so it is worth showing.
                    readonly property bool current: root.position >= startTime
                                                    && root.position < endTime
                    readonly property bool roomy: block.height >= 22

                    x: 0
                    y: root.blockY(index)
                    width: content.width
                    height: root.blockHeight(index)
                    clip: true

                    HoverHandler { id: blockHover }

                    Rectangle {
                        anchors.fill: parent
                        anchors.leftMargin: root.gutter
                        color: block.current ? Theme.accentSoft
                             : (blockHover.hovered ? Theme.alpha(Theme.text, 0.07)
                                                   : Theme.alpha(Theme.text, 0.03))
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                    }

                    // Pushed, never bound, and re-pushed only when the model
                    // moves underneath — a paste and a boundary drag both do
                    // that. See `onTimeChanged`/`onWordsChanged` below.
                    NocturneField {
                        id: timeField
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.topMargin: 6
                        width: root.gutter - 6
                        height: Math.max(14, Math.min(26, block.height - 6))
                        visible: block.roomy
                        clearable: false
                        placeholder: "--:--.--"
                        Component.onCompleted: text = block.time
                        onEdited: function (value) { root.timeEdited(block.index, value) }
                        // On commit, not on every keystroke: `0` on its way to
                        // becoming `01:30` would collapse the layout under the
                        // cursor.
                        onAccepted: root.timeCommitted(block.index)
                        onInputFocusChanged: if (!inputFocus) root.timeCommitted(block.index)
                    }

                    NocturneField {
                        id: wordsField
                        anchors.left: parent.left
                        anchors.leftMargin: root.gutter
                        anchors.right: actions.left
                        anchors.rightMargin: Theme.gapXs
                        anchors.top: parent.top
                        anchors.topMargin: 6
                        height: Math.max(14, Math.min(26, block.height - 6))
                        visible: block.roomy
                        clearable: false
                        placeholder: "…"
                        forwardMultilinePaste: true
                        Component.onCompleted: text = block.words
                        onEdited: function (value) { root.wordsEdited(block.index, value) }
                        onPastedLines: function (lines) {
                            var at = cursorPosition
                            root.wordsPasted(block.index,
                                             text.slice(0, at), text.slice(at), lines)
                        }
                    }

                    // Kept laid out even while hidden, so the words field does
                    // not reflow the moment the pointer arrives.
                    Row {
                        id: actions
                        anchors.right: parent.right
                        anchors.rightMargin: 2
                        anchors.top: parent.top
                        anchors.topMargin: 5
                        spacing: 0
                        visible: block.roomy && (blockHover.hovered
                                                 || timeField.inputFocus
                                                 || wordsField.inputFocus)

                        IconButton {
                            icon: "plus"
                            iconSize: 14
                            diameter: 26
                            tooltip: "Add a line beneath this one"
                            onClicked: root.insertRequested(block.index + 1)
                        }
                        IconButton {
                            icon: "trash"
                            iconSize: 14
                            diameter: 26
                            tooltip: "Remove this line"
                            onClicked: root.removeRequested(block.index)
                        }
                    }

                    onTimeChanged: if (!timeField.inputFocus) timeField.text = block.time
                    onWordsChanged: if (!wordsField.inputFocus) wordsField.text = block.words
                }
            }

            // ---------------------------------------------------- playhead
            //
            // Above the blocks, below the boundary handles, and takes no
            // pointer events at all.

            Item {
                id: playhead
                y: root.playheadY
                width: content.width
                height: 2
                z: 4

                Rectangle {
                    anchors.left: parent.left
                    anchors.leftMargin: root.gutter
                    anchors.right: parent.right
                    height: 2
                    color: Theme.accent
                }

                Text {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    width: root.gutter - 6
                    horizontalAlignment: Text.AlignRight
                    text: Fmt.timecode(root.position)
                    color: Theme.accent
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                    font.features: ({ "tnum": 1 })
                }
            }

            // -------------------------------------------------- boundaries
            //
            // A boundary *is* the start time of the block beneath it, so
            // dragging one grows one block, shrinks the other, and touches
            // nothing else. Above everything, so an 11px strip either side of
            // an edge is unambiguously a retime and not a seek.
            //
            // Held with Ctrl it is the other gesture entirely: everything from
            // here down keeps the size it has and moves with the pointer, so a
            // document that runs late from some point on is corrected in one
            // drag rather than a line at a time. Which of the two it is, is
            // latched at the press — a gesture that changed its mind halfway
            // would leave half the document already shifted.

            Repeater {
                id: boundaries
                model: root.model

                delegate: MouseArea {
                    id: handle

                    required property int index

                    x: root.gutter
                    y: root.blockY(index) - height / 2
                    width: Math.max(0, content.width - root.gutter)
                    height: 11
                    z: 6
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton
                    preventStealing: true
                    cursorShape: Qt.SizeVerCursor

                    // Every boundary a running Ctrl+drag is carrying, not just
                    // the one under the pointer — which is the only way to see
                    // what the gesture is doing before letting go of it.
                    readonly property bool carried:
                        root.shiftFrom >= 0 && handle.index >= root.shiftFrom
                    readonly property bool lit:
                        handle.containsMouse || handle.pressed || handle.carried

                    property real origin: 0
                    property bool armed: false
                    property bool shifting: false

                    onPressed: function (mouse) {
                        handle.origin = root.startOf(handle.index)
                        handle.shifting = (mouse.modifiers & Qt.ControlModifier) !== 0
                        handle.armed = true
                        if (handle.shifting)
                            root.shiftFrom = handle.index
                    }
                    onPositionChanged: function (mouse) {
                        if (!handle.pressed || !handle.armed)
                            return
                        // Into the content item rather than through the screen:
                        // the content can scroll under a drag, and mapping into
                        // the thing that scrolls is already scroll-proof — no
                        // `contentY` compensation of the kind TrackTable
                        // carries.
                        var t = handle.mapToItem(content, mouse.x, mouse.y).y / root.pxPerSec
                        if (handle.shifting)
                            root.boundaryShifted(handle.index,
                                                 root.clampShift(handle.index, t))
                        else
                            root.boundaryMoved(handle.index,
                                               root.clampBoundary(handle.index, t))
                    }
                    onReleased: handle._finish()
                    onCanceled: {
                        if (handle.armed) {
                            // Absolute, both of them, so putting the boundary
                            // back where it started puts everything it was
                            // carrying back too.
                            if (handle.shifting)
                                root.boundaryShifted(handle.index, handle.origin)
                            else
                                root.boundaryMoved(handle.index, handle.origin)
                        }
                        handle._finish()
                    }

                    function _finish() {
                        if (handle.shifting && root.shiftFrom === handle.index)
                            root.shiftFrom = -1
                        handle.armed = false
                        handle.shifting = false
                    }

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        height: handle.lit ? 2 : 1
                        color: handle.lit ? Theme.accent : Theme.line

                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                    }

                    NocturneTooltip {
                        text: "Drag to retime this line · Ctrl+drag to move it "
                              + "and everything below together"
                        visible: handle.containsMouse && !handle.pressed
                    }
                }
            }
        }
    }

    // Declared beside its target, and opted in to Ctrl+wheel — see
    // Nocturne/SmoothWheel.qml, which otherwise swallows every wheel event.
    SmoothWheel {
        target: flick
        forwardZoomWheel: true
        onZoomWheel: function (event) {
            var up = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y
            if (up !== 0)
                root.zoomAt(up > 0 ? 1 : -1, event.y)
        }
    }
}
