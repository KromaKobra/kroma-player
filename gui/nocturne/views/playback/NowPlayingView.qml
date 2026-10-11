import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import Nocturne
import "../../components/artwork"
import "../../components/controls"
import "../../components/dialogs"
import "../../components/playback"

// The centrepiece. Large artwork on the left; on the right either synced
// lyrics, when the track carries them, or what is coming up next.
Item {
    id: root

    property bool active: false

    // Lyrics are read from the audio file on demand, so tell the backend when
    // this view is actually on screen.
    Binding {
        target: typeof lyricVM !== "undefined" ? lyricVM : null
        property: "active"
        value: root.active
    }

    readonly property bool wide: width > 900
    readonly property bool hasLyrics: lyricVM.has_lyrics

    // Which half has the page: "" | "art" | "words". Read through `wide`, so a
    // stored focus is simply not in effect on a window too narrow to have two
    // halves, and comes back when the window is widened again rather than
    // being thrown away. Anything else reads as no focus: session.json is a
    // file on disk, and the two halves' geometry is decided between them — a
    // third value would leave both of them thinking they had the width.
    //
    // Not named `focus`: that is `Item.focus`.
    readonly property string focusHalf: {
        if (!root.wide)
            return ""
        var stored = Theme.prefs.nowPlayingFocus
        return stored === "art" || stored === "words" ? stored : ""
    }
    readonly property bool split: root.wide && root.focusHalf === ""

    // Writes the pref directly, the way AlbumView's artwork toggle writes
    // `Theme.prefs.albumArtwork` — there is no view-local copy to keep in step
    // with it.
    function toggleFocus(half) {
        Theme.prefs.nowPlayingFocus = Theme.prefs.nowPlayingFocus === half ? "" : half
    }

    // Edit mode is GUI-local state on purpose. The backend holds the stored
    // document and hears from the editor exactly once, on save — a per-keystroke
    // conversation would mean rewriting the list the fields are reading from,
    // and Qt drops a text field's binding the moment somebody types into it.
    // Same rule docs/GUI_API.md states for editVM.
    //
    // A retime is the opposite: the session lives in the view model, because it
    // drives playback and a GUI holds no playback logic.
    property bool editing: false
    readonly property bool retiming: lyricVM.is_retiming

    // Set by the edit_failed handler below, read back synchronously by
    // saveDraft(). saveLyrics() is a direct call, so a refusal has already
    // arrived by the time it returns.
    property bool editRefused: false

    // Which editor the draft gets, and whether it has the choice. A document
    // with no timings has nothing to lay out in proportion, so it only ever
    // gets the rows; one that has them opens on the timeline and can be
    // switched back — the rows are still the faster way to retype a batch of
    // words, and the two edit the same draft.
    //
    // Both are decided in beginEdit() rather than bound to `lyricVM.is_synced`,
    // so a document cannot change editor underneath somebody as they type
    // times into it.
    property bool canUseTimeline: false
    property bool timelineDraft: false

    function toggleEditorMode() {
        if (root.canUseTimeline)
            root.timelineDraft = !root.timelineDraft
    }

    onHasLyricsChanged: if (!hasLyrics) editing = false
    onActiveChanged: if (!active) { editing = false; lyricVM.cancelRetime() }

    // Seed the editor from whatever is on screen. Pushed once, never bound —
    // from here the local model is the draft and the view model knows nothing
    // about it until saveDraft().
    function beginEdit() {
        draftModel.clear()
        if (lyricVM.is_synced) {
            var lines = lyricVM.synced_lines
            var times = lyricVM.synced_timestamps
            for (var i = 0; i < lines.length; i++)
                draftModel.append({ time: Fmt.timecode(times[i]), words: String(lines[i]) })
        } else {
            var plain = String(lyricVM.plain_text).split("\n")
            for (var j = 0; j < plain.length; j++)
                draftModel.append({ time: "", words: plain[j] })
        }
        if (draftModel.count === 0)
            draftModel.append({ time: "", words: "" })
        root.canUseTimeline = lyricVM.is_synced
        root.timelineDraft = root.canUseTimeline
        root.refreshSeconds()
        root.editing = true
    }

    function saveDraft() {
        root.editRefused = false
        // Handed over as the raw field text. The view model parses it, so a
        // typo is refused with the line number rather than silently becoming
        // zero.
        lyricVM.saveLyrics(root.draftTimes(), root.draftWords())
        if (!root.editRefused)
            root.editing = false
    }

    // ---------------------------------------------------------- the draft
    //
    // Structural edits — insert, remove, move, split a paste — all end up
    // asking the view model what timecodes the affected rows should carry, so
    // that rule lives in one place and a GUI is not carrying its own copy of
    // it. Text edits stay entirely local; see saveDraft().

    ListModel { id: draftModel; objectName: "lyricsDraft" }

    // The draft's two columns, as plain arrays. Everything that reads the
    // whole draft — the save, the insertion rule, the re-sort — goes through
    // these rather than walking the model itself, so `ListModel.get()` is
    // called in one place.
    function draftTimes() {
        var out = []
        for (var i = 0; i < draftModel.count; i++)
            out.push(String(draftModel.get(i).time))
        return out
    }

    function draftWords() {
        var out = []
        for (var i = 0; i < draftModel.count; i++)
            out.push(String(draftModel.get(i).words))
        return out
    }

    // ------------------------------------------------- the numeric column
    //
    // A timeline lays its lines out in proportion, so it needs the draft's
    // timings as numbers rather than as the raw text of a field. Parsed by the
    // view model, like every other question the draft asks, so the GUI carries
    // no copy of the timecode parser — see lyricVM.parseTimes().
    //
    // Refreshed wherever the draft changes *structurally*, and never on a
    // keystroke: `0` on its way to becoming `01:30` would collapse the layout
    // under the cursor, which is the same reason resortLine() waits for a
    // commit.

    property var draftSeconds: []

    function refreshSeconds() {
        root.draftSeconds = lyricVM.parseTimes(root.draftTimes())
    }

    // The last moment the draft knows about. The timeline needs somewhere to
    // end even for a track whose duration the backend has not got — a file
    // with no audio reports 0, and dividing a layout by that is a warning at
    // best.
    readonly property real lastDraftTime: {
        var last = 0
        for (var i = 0; i < root.draftSeconds.length; i++) {
            var t = Number(root.draftSeconds[i])
            if (isFinite(t) && t > last)
                last = t
        }
        return last
    }

    // A boundary was dragged. Both columns move together, live: the numbers so
    // the two blocks either side resize under the pointer, and the text so the
    // gutter counts as it goes and the save path sees it. The array is
    // replaced rather than written into — a `var` property announces an
    // assignment and not a mutation.
    function moveBoundary(index, seconds) {
        if (index < 0 || index >= draftModel.count)
            return
        var next = root.draftSeconds.slice()
        next[index] = seconds
        root.draftSeconds = next
        draftModel.setProperty(index, "time", Fmt.timecode(seconds))
    }

    // A boundary was Ctrl+dragged: the line at `index` goes to `seconds` and
    // everything below it comes along, keeping the gap it had. Which is the
    // whole of "the song drifts from here on" — the timings are right relative
    // to each other and wrong relative to the track, so the fix is one number
    // applied to all of them rather than a line-by-line pass.
    //
    // Stated as the new absolute time of `index` rather than as a delta, the
    // same currency moveBoundary() takes, so a cancelled drag puts everything
    // back by asking for the time it started at.
    //
    // Lines with no time are left alone. There is nothing to offset, and
    // giving them one would quietly sync a document nobody asked to sync.
    function shiftBoundary(index, seconds) {
        if (index < 0 || index >= draftModel.count)
            return
        var next = root.draftSeconds.slice()
        var from = Number(next[index])
        if (!isFinite(from) || from < 0)
            return
        var delta = seconds - from
        if (delta === 0)
            return
        for (var i = index; i < next.length; i++) {
            var t = Number(next[i])
            if (!isFinite(t) || t < 0)
                continue
            next[i] = Math.max(0, t + delta)
            draftModel.setProperty(i, "time", Fmt.timecode(next[i]))
        }
        root.draftSeconds = next
    }

    // Insert `words` (an array) at `at`, timed by the backend's rule. The one
    // path every structural insert goes through — the plus button hands it one
    // empty string, a paste hands it the rest of the block.
    function insertLines(at, words) {
        if (!words || words.length === 0)
            return
        var times = lyricVM.insertionTimes(root.draftTimes(), at, words.length)
        for (var i = 0; i < words.length; i++)
            draftModel.insert(at + i, {
                time: Fmt.timecode(times[i]),
                words: String(words[i])
            })
        root.refreshSeconds()
    }

    function removeLine(index) {
        draftModel.remove(index)
        // The list is never empty: an editor with no rows has no way back to
        // having one, and clearing the last row's text and saving is already
        // how lyrics get deleted.
        if (draftModel.count === 0)
            draftModel.append({ time: "", words: "" })
        root.refreshSeconds()
    }

    // Commit a drag. The row has already been moved in the model so the list
    // could show where it would land; this is what re-times it once it is
    // there. Its own time is blanked first, or it would be its own neighbour.
    function retimeMoved(index) {
        draftModel.setProperty(index, "time", "")
        var times = lyricVM.insertionTimes(root.draftTimes(), index, 1)
        draftModel.setProperty(index, "time", Fmt.timecode(times[0]))
        root.refreshSeconds()
    }

    // A timecode was typed and committed. Only the one row moves — a typed
    // time is a statement about a single line, not a request to reflow.
    function resortLine(index) {
        var to = lyricVM.sortedIndex(root.draftTimes(), index)
        if (to !== index)
            draftModel.move(index, to, 1)
        root.refreshSeconds()
    }

    // ------------------------------------------------------- dragging a row
    //
    // The row index being dragged, -1 for none, and how far past its current
    // slot the pointer has travelled. The model is moved *live* as the pointer
    // crosses each row boundary — so the list shows where the row would land
    // rather than describing it — and only the timecode waits for the release.

    property int dragRow: -1
    property real dragOffset: 0
    property int dragOrigin: -1

    // One row plus the list's spacing. Rows are a fixed height, which is what
    // makes this a division rather than TrackTable's arithmetic.
    readonly property real dragRowPitch: 44

    function dragTo(delta) {
        if (root.dragRow < 0)
            return
        if (root.dragOrigin < 0)
            root.dragOrigin = root.dragRow

        var steps = Math.round(delta / root.dragRowPitch)
        var target = Math.max(0, Math.min(draftModel.count - 1,
                                          root.dragOrigin + steps))
        if (target !== root.dragRow) {
            draftModel.move(root.dragRow, target, 1)
            root.dragRow = target
        }
        // Whatever is left over after the whole-row moves, so the row tracks
        // the pointer between crossings instead of jumping a row at a time.
        root.dragOffset = delta - (root.dragRow - root.dragOrigin) * root.dragRowPitch
    }

    function endDrag(commit) {
        var row = root.dragRow
        var origin = root.dragOrigin
        root.dragRow = -1
        root.dragOffset = 0
        root.dragOrigin = -1
        if (row < 0)
            return
        if (!commit) {
            if (origin >= 0 && origin !== row)
                draftModel.move(row, origin, 1)
            return
        }
        if (row !== origin)
            root.retimeMoved(row)
    }

    // A multi-line paste landed in row `index`. The text before the cursor
    // keeps the row along with the first pasted line; the rest become rows
    // beneath it, and whatever was after the cursor rides on the last one —
    // which is what any text editor does with a paste, spread over rows
    // instead of over lines.
    function splitPaste(index, before, after, lines) {
        var parts = lyricVM.splitPastedLines(lines.join("\n"))
        if (parts.length === 0)
            return
        draftModel.setProperty(index, "words", before + String(parts[0]))
        if (parts.length === 1) {
            draftModel.setProperty(index, "words",
                                   before + String(parts[0]) + after)
            root.refreshSeconds()
            return
        }
        var rest = []
        for (var i = 1; i < parts.length; i++)
            rest.push(String(parts[i]))
        rest[rest.length - 1] = rest[rest.length - 1] + after
        root.insertLines(index + 1, rest)
    }

    // ------------------------------------------------------------ empty state

    Column {
        anchors.centerIn: parent
        spacing: Theme.gap
        visible: !playerVM.has_current_track
        width: parent.width * 0.6

        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            name: "disc"
            size: 54
            color: Theme.alpha(Theme.text, 0.16)
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "Nothing playing"
            color: Theme.dim
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: "Pick something from your library, or search for something new."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }
    }

    // ---------------------------------------------------------------- content

    Item {
        anchors.fill: parent
        anchors.margins: Theme.gapXl
        visible: playerVM.has_current_track

        // ------------------------------------------------------- artwork side
        Item {
            id: artSide
            objectName: "artSide"
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            visible: root.focusHalf !== "words"
            // Half of what the details pane leaves, less the gutter between the
            // columns. `content` in main.qml is anchored to the panel's left
            // edge, so `parent.width` here already *is* the room available and
            // splitting it needs to know nothing about the panel.
            //
            // Focused, it takes the whole width and its centred Column centres
            // the sleeve in it — the same thing the narrow layout has always
            // done.
            width: root.split ? (parent.width - Theme.gapXl) / 2 : parent.width

            Column {
                id: artColumn
                anchors.centerIn: parent
                width: parent.width
                spacing: Theme.gapLg

                Item {
                    // Square, so whichever of the three limits binds first
                    // wins: the column's width, its height less the 224px the
                    // title/artist block, the button row and the focus handle
                    // below need, or the ceiling. That 224 is a constant rather
                    // than a measurement on purpose — measuring the siblings
                    // would make the column's height depend on the art's, which
                    // depends on the column's height.
                    //
                    // It was 190 before the handle: a gapXl more, which is what
                    // keeps the handle inside the half at the window's minimum
                    // height with a one-line title. Unconditional, so the
                    // artwork never resizes for a reason nobody can see.
                    //
                    // 800 is the backend's ORIGINAL_SIZE: the master embedded
                    // in the file is encoded at that edge, so there is no more
                    // artwork above this size to show and growing further only
                    // stretches what there is.
                    width: Math.min(parent.width, artSide.height - 224, 800)
                    height: width
                    anchors.horizontalCenter: parent.horizontalCenter

                    // Accent-tinted glow pooling under the sleeve. The blur is
                    // allowed to bleed past its own bounds (autoPadding), which
                    // is what turns a plain rounded rect into a halo.
                    Item {
                        id: glowLayer
                        // Slightly larger than the sleeve and pushed down, so the
                        // halo pools below it instead of hiding behind it.
                        width: parent.width * 1.04
                        height: parent.height * 1.04
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: parent.height * 0.05

                        Rectangle {
                            id: glowSource
                            anchors.fill: parent
                            radius: Theme.radiusXl
                            visible: false
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: Theme.accent }
                                GradientStop { position: 1.0; color: Theme.accentAlt }
                            }
                        }

                        MultiEffect {
                            anchors.fill: parent
                            source: glowSource
                            blurEnabled: true
                            blur: 1.0
                            blurMax: 64
                            blurMultiplier: 1.6
                            opacity: 0.55
                        }
                    }

                    CoverArt {
                        anchors.fill: parent
                        radius: Theme.radiusXl
                        tier: 512
                        hash: playerVM.current_track_cover_hash
                        placeholderText: playerVM.current_track_title
                    }
                }

                Column {
                    width: parent.width
                    spacing: 6

                    Text {
                        width: parent.width
                        text: playerVM.current_track_title
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        color: Theme.text
                        font.family: Theme.displayFamily
                        font.pixelSize: Theme.fzTitle
                        font.weight: Font.DemiBold
                        font.letterSpacing: -0.4
                    }

                    Text {
                        width: parent.width
                        text: playerVM.current_track_artist
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        color: Theme.dim
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzMed
                    }
                }

                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: Theme.gapSm

                    AppButton {
                        text: "Add to playlist"
                        iconName: "playlist"
                        variant: "soft"
                        onClicked: addDialog.openFor([playerVM.current_track_id])
                    }

                    AppButton {
                        text: "Details"
                        iconName: "info"
                        variant: "ghost"
                        // Nothing is selected on this page, so the panel already
                        // falls back to the playing track — opening it is the
                        // whole action. Pinning the selection to the current id
                        // instead would freeze the panel on this track once
                        // playback moved on.
                        onClicked: root.detailsRequested()
                    }
                }
            }

            // Hover is tracked by a handler on the half itself rather than by a
            // MouseArea: the pointer spends most of its time over the buttons
            // and the sleeve inside it, and a sibling MouseArea would report a
            // leave at every one of them. The same reasoning TrackTable's
            // `rowHover` states in place.
            HoverHandler { id: artHover }

            // Last child of the half, so it paints over everything in it.
            // Anchored to the column rather than to the half's bottom edge,
            // which is what guarantees it never lands on the button row
            // whatever the title does.
            FocusHandle {
                objectName: "artFocusHandle"
                offered: root.wide
                revealed: artHover.hovered
                focused: root.focusHalf === "art"
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: artColumn.bottom
                anchors.topMargin: Theme.gapSm
                onToggled: root.toggleFocus("art")
            }
        }

        // --------------------------------------------------------- right side
        Item {
            id: rightSide
            objectName: "rightSide"
            visible: root.wide && root.focusHalf !== "art"
            // Focused, it starts at the content edge instead of at the art
            // half's right edge. The ternary is only there for that: an
            // invisible QML item keeps its geometry and its anchor lines, so
            // `artSide.right` would stay perfectly valid — it would just be in
            // the middle of the page. MenuRow.qml switches an anchor the same
            // way.
            anchors.left: root.focusHalf === "words" ? parent.left : artSide.right
            anchors.leftMargin: root.focusHalf === "words" ? 0 : Theme.gapXl
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            // The other half, by construction rather than by arithmetic — so it
            // cannot drift from artSide if the gutter ever changes. Still
            // whatever is left over; focused, it is left over from a different
            // edge.
            anchors.right: parent.right

            // ------------------------------------------------------- lyrics
            Item {
                objectName: "lyricsPane"
                anchors.fill: parent
                visible: root.hasLyrics || root.editing

                // The column is honestly half the window; the *text* is not.
                // Long lyric lines are unreadable at full width, so the lyrics
                // keep a readable measure and sit centred in the space. Up Next
                // below is deliberately not wrapped — a queue row wants it all.
                Item {
                    id: lyricsMeasure
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    // Wider than it was, because the reading type is larger:
                    // the measure is a line-length budget, not a pixel one.
                    width: Math.min(parent.width, 680)

                    Item {
                        id: lyricsHeader
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        height: 30

                        Text {
                            id: lyricsTitle
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.editing ? "EDITING LYRICS"
                                : (root.retiming ? "RETIMING" : "LYRICS")
                            color: root.editing || root.retiming ? Theme.accent : Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                            font.weight: Font.DemiBold
                            font.letterSpacing: 1.4
                        }

                        // One row, three sets of buttons. Which set is showing
                        // is the whole of the mode indicator, so nothing else
                        // has to switch on the mode.
                        Row {
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2

                            // -- reading
                            IconButton {
                                visible: !root.editing && !root.retiming
                                icon: "stage-mic"
                                iconSize: 15
                                diameter: 28
                                enabledAction: !lyricVM.is_fetching && playerVM.has_current_track
                                tooltip: lyricVM.is_fetching ? "Searching…"
                                                             : "Search again for lyrics"
                                onClicked: lyricVM.fetchLyrics()
                            }
                            IconButton {
                                objectName: "lyricsEditButton"
                                visible: !root.editing && !root.retiming
                                icon: "edit"
                                iconSize: 15
                                diameter: 28
                                tooltip: "Edit the words and their timings"
                                onClicked: root.beginEdit()
                            }
                            IconButton {
                                objectName: "lyricsRetimeButton"
                                visible: !root.editing && !root.retiming
                                icon: "clock"
                                iconSize: 15
                                diameter: 28
                                enabledAction: playerVM.has_current_track
                                tooltip: "Retime: play the track and tap space on each line"
                                onClicked: lyricVM.beginRetime()
                            }

                            // -- editing a timeline
                            //
                            // Height is time, so the zoom is the only control
                            // the timeline needs that a row editor does not:
                            // it is how a half-second line becomes big enough
                            // to type into. Ctrl+wheel does the same thing
                            // anchored under the pointer.
                            IconButton {
                                visible: root.editing && root.timelineDraft
                                icon: "minus"
                                iconSize: 15
                                diameter: 28
                                enabledAction: editTimeline.pxPerSec > editTimeline.minPxPerSec
                                tooltip: "Zoom out"
                                onClicked: editTimeline.zoomOut()
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: root.editing && root.timelineDraft
                                text: Math.round(editTimeline.pxPerSec) + " px/s"
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzTiny
                                font.features: ({ "tnum": 1 })
                            }
                            IconButton {
                                visible: root.editing && root.timelineDraft
                                icon: "plus"
                                iconSize: 15
                                diameter: 28
                                enabledAction: editTimeline.pxPerSec < editTimeline.maxPxPerSec
                                tooltip: "Zoom in"
                                onClicked: editTimeline.zoomIn()
                            }
                            IconButton {
                                visible: root.editing && root.timelineDraft
                                icon: "target"
                                iconSize: 15
                                diameter: 28
                                active: editTimeline.follow
                                tooltip: editTimeline.follow
                                         ? "Following the playhead"
                                         : "Follow the playhead again"
                                onClicked: editTimeline.follow = true
                            }

                            // The two editors over one draft. Only offered
                            // where there is a choice — a document with no
                            // timings has no timeline to switch to.
                            IconButton {
                                objectName: "lyricsModeButton"
                                visible: root.editing && root.canUseTimeline
                                icon: root.timelineDraft ? "rows" : "timeline"
                                iconSize: 15
                                diameter: 28
                                tooltip: root.timelineDraft
                                         ? "Edit as a list of lines"
                                         : "Edit on the timeline"
                                onClicked: root.toggleEditorMode()
                            }

                            // -- editing
                            //
                            // No "add a line" here: every row carries its own
                            // plus, which puts the new line where it is wanted
                            // rather than at the end.
                            IconButton {
                                visible: root.editing
                                icon: "check"
                                iconSize: 15
                                diameter: 28
                                color: Theme.accent
                                tooltip: "Save"
                                onClicked: root.saveDraft()
                            }

                            // -- retiming
                            IconButton {
                                visible: root.retiming
                                icon: "check"
                                iconSize: 15
                                diameter: 28
                                color: Theme.accent
                                enabledAction: lyricVM.can_finish_retime
                                tooltip: lyricVM.can_finish_retime
                                         ? "Finish and save the new timings"
                                         : "Keep going — some lines still have no time"
                                onClicked: lyricVM.finishRetime()
                            }

                            // -- both
                            IconButton {
                                visible: root.editing || root.retiming
                                icon: "x"
                                iconSize: 15
                                diameter: 28
                                tooltip: "Cancel"
                                onClicked: {
                                    root.editing = false
                                    lyricVM.cancelRetime()
                                }
                            }
                        }
                    }

                    // The hint takes its own height from its text, and the
                    // panes below step down by that much rather than anchoring
                    // to it: a wrapping Text whose `height` is written from its
                    // own `implicitHeight` is a binding loop, because the
                    // implicit height is what the height is derived from.
                    Text {
                        id: retimeHint
                        anchors.top: lyricsHeader.bottom
                        anchors.left: parent.left
                        anchors.right: parent.right
                        visible: root.retiming
                        wrapMode: Text.WordWrap
                        text: "Space marks where the next line starts "
                              + "· Backspace steps back · Enter finishes · Esc cancels"
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzTiny
                    }

                    // What every pane below anchors to, so none of them has to
                    // know whether the hint is showing.
                    QtObject {
                        id: paneTop
                        readonly property real margin:
                            Theme.gap + (retimeHint.visible ? retimeHint.height + 6 : 0)
                    }

                    // Synced, and the retime session: one line per row, the
                    // active line lifted and coloured. During a retime the
                    // lines come from the session rather than the stored
                    // document, because an unsynced document being given
                    // timings has no `synced_lines` at all.
                    ListView {
                        id: lyricsList
                        anchors.top: lyricsHeader.bottom
                        anchors.topMargin: paneTop.margin
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        visible: !root.editing && (lyricVM.is_synced || root.retiming)
                        clip: true
                        model: root.retiming ? lyricVM.retime_lines : lyricVM.synced_lines
                        spacing: 14
                        currentIndex: lyricVM.current_line_index
                        preferredHighlightBegin: height * 0.36
                        preferredHighlightEnd: height * 0.52
                        highlightRangeMode: ListView.ApplyRange
                        highlightMoveDuration: Theme.slow
                        highlightMoveVelocity: -1

                        ScrollBar.vertical: ScrollBar { width: 6 }

                        delegate: Item {
                            id: lyricRow
                            required property int index
                            required property var modelData

                            readonly property bool isCurrent: index === lyricVM.current_line_index
                            readonly property bool isPast: index < lyricVM.current_line_index

                            width: ListView.view.width
                            implicitHeight: Math.max(lyricText.implicitHeight,
                                                     stampText.visible ? stampText.implicitHeight : 0)
                            height: implicitHeight

                            // The timings as they stand, shown only while a
                            // retime is running. A line already stamped shows
                            // its new time; one not yet reached shows the time
                            // it arrived with, dimmed, so the difference the
                            // session is making is visible as it happens.
                            Text {
                                id: stampText
                                anchors.left: parent.left
                                anchors.top: parent.top
                                width: 66
                                visible: root.retiming
                                readonly property real stamped:
                                    root.retiming && lyricRow.index < lyricVM.retime_times.length
                                    ? lyricVM.retime_times[lyricRow.index] : -1
                                readonly property real original:
                                    lyricRow.index < lyricVM.synced_timestamps.length
                                    ? lyricVM.synced_timestamps[lyricRow.index] : -1
                                text: Fmt.timecode(stamped >= 0 ? stamped : original)
                                color: stamped >= 0 ? Theme.accent : Theme.alpha(Theme.text, 0.22)
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzSmall
                                font.features: ({ "tnum": 1 })
                            }

                            Text {
                                id: lyricText
                                anchors.left: parent.left
                                anchors.leftMargin: stampText.visible ? stampText.width + Theme.gapSm : 0
                                anchors.right: parent.right
                                anchors.top: parent.top
                                text: lyricRow.modelData
                                wrapMode: Text.WordWrap
                                // Three states, pulled well apart: the line
                                // being sung carries the eye, what is coming is
                                // legible, and what has gone is nearly out of
                                // the way.
                                color: lyricRow.isCurrent ? Theme.text
                                     : (lyricRow.isPast ? Theme.alpha(Theme.text, 0.16)
                                                        : Theme.alpha(Theme.text, 0.55))
                                font.family: Theme.displayFamily
                                font.pixelSize: lyricRow.isCurrent ? Theme.fzTitle : Theme.fzLarge
                                font.weight: lyricRow.isCurrent ? Font.Bold
                                           : (lyricRow.isPast ? Font.Normal : Font.DemiBold)
                                font.letterSpacing: lyricRow.isCurrent ? -0.3 : 0
                                lineHeight: 1.3

                                Behavior on color { ColorAnimation { duration: Theme.normal } }
                                Behavior on font.pixelSize { NumberAnimation { duration: Theme.normal } }
                            }
                        }
                    }

                    // Unsynced: plain scrolling text.
                    Flickable {
                        id: plainLyricsFlick
                        anchors.top: lyricsHeader.bottom
                        anchors.topMargin: paneTop.margin
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        visible: !root.editing && !root.retiming && !lyricVM.is_synced
                        contentHeight: plainLyrics.implicitHeight
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds

                        ScrollBar.vertical: ScrollBar { width: 6 }

                        Text {
                            id: plainLyrics
                            width: parent.width
                            text: lyricVM.plain_text
                            wrapMode: Text.WordWrap
                            color: Theme.alpha(Theme.text, 0.8)
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzLarge
                            lineHeight: 1.5
                        }
                    }

                    // A document with no timings has nothing to lay out in
                    // proportion, so it never leaves the row editor — and this
                    // says where its timings come from, because a retime pass
                    // is the tool for giving it a first set. A synced document
                    // switched to the rows on purpose needs no such notice.
                    Text {
                        id: retimeNotice
                        anchors.top: lyricsHeader.bottom
                        anchors.topMargin: paneTop.margin
                        anchors.left: parent.left
                        anchors.right: parent.right
                        visible: root.editing && !root.canUseTimeline
                        wrapMode: Text.WordWrap
                        text: "No timings yet — save these words, then use "
                              + "Retime to add them by ear."
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzTiny
                    }

                    // The editor. A timecode and the words per row, over a
                    // local model — see beginEdit()/saveDraft() above for why
                    // the draft lives here and not in the view model.
                    ListView {
                        id: editList
                        objectName: "lyricsEditList"
                        // Steps down past the notice rather than anchoring to
                        // it, for the reason paneTop exists: the notice is only
                        // sometimes there, and an invisible item still has a
                        // height to anchor to.
                        anchors.top: lyricsHeader.bottom
                        anchors.topMargin: paneTop.margin
                                           + (retimeNotice.visible
                                              ? retimeNotice.height + 6 : 0)
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        visible: root.editing && !root.timelineDraft
                        clip: true
                        model: draftModel
                        spacing: 4

                        ScrollBar.vertical: ScrollBar { width: 6 }

                        // A row lifted out of the list sits above the rest and
                        // does not animate — it is under the cursor, and
                        // easing it would put it behind the pointer.
                        moveDisplaced: Transition {
                            NumberAnimation { properties: "y"; duration: Theme.fast }
                        }

                        delegate: Item {
                            id: editRow
                            required property int index
                            required property string time
                            required property string words

                            readonly property bool lifted: root.dragRow === index

                            width: ListView.view.width
                            height: 40
                            z: lifted ? 2 : 0

                            // Sub-row travel only: the model has already been
                            // moved for anything past half a row, so this is
                            // the remainder that keeps the row under the
                            // pointer between crossings. `y` belongs to the
                            // ListView, hence a transform.
                            transform: Translate {
                                y: editRow.lifted ? root.dragOffset : 0
                            }

                            Icon {
                                id: grip
                                anchors.left: parent.left
                                anchors.leftMargin: 2
                                anchors.verticalCenter: parent.verticalCenter
                                name: "grip"
                                size: 16
                                color: editRow.lifted ? Theme.accent
                                     : (gripMouse.containsMouse ? Theme.text : Theme.faint)
                                Behavior on color { ColorAnimation { duration: Theme.fast } }
                            }

                            // Hand-rolled, like every other drag in this GUI —
                            // there is no DropArea or DelegateModel anywhere in
                            // the package. ArtistChips is the model to follow
                            // rather than TrackTable: a short list that moves
                            // its own model live so the row can show where it
                            // would land, with nothing committed until release.
                            MouseArea {
                                id: gripMouse
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                width: 24
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton
                                // The row lives in a ListView that would
                                // otherwise spend this gesture scrolling.
                                preventStealing: true
                                cursorShape: editRow.lifted ? Qt.ClosedHandCursor
                                                            : Qt.OpenHandCursor

                                property real pressY: 0
                                property bool armed: false

                                onPressed: function (mouse) {
                                    pressY = mapToGlobal(mouse.x, mouse.y).y
                                    armed = false
                                }

                                onPositionChanged: function (mouse) {
                                    if (!pressed)
                                        return
                                    var y = mapToGlobal(mouse.x, mouse.y).y
                                    if (!armed) {
                                        // The same 5px the column header and
                                        // the artist chips wait for.
                                        if (Math.abs(y - pressY) < 5)
                                            return
                                        if (draftModel.count < 2)
                                            return
                                        armed = true
                                        root.dragRow = editRow.index
                                    }
                                    root.dragTo(y - pressY)
                                }

                                onReleased: {
                                    if (armed)
                                        root.endDrag(true)
                                    armed = false
                                }

                                onCanceled: {
                                    if (armed)
                                        root.endDrag(false)
                                    armed = false
                                }
                            }

                            NocturneField {
                                id: timeField
                                anchors.left: grip.right
                                anchors.leftMargin: Theme.gapSm
                                anchors.verticalCenter: parent.verticalCenter
                                // Same as the timeline's gutter less its
                                // margin, and for the same reason: narrower
                                // than this and the field scrolls a whole
                                // stamp down to its tail, which in a column of
                                // timecodes reads as a different time rather
                                // than as a clipped one.
                                width: 100
                                clearable: false
                                placeholder: "--:--.--"
                                // Pushed, never bound. Typing assigns to
                                // `text` and would blow away a binding — the
                                // same rule TrackEditView.qml's syncFields()
                                // obeys. Pushed again whenever the *model*
                                // moves underneath it, which a drop and a
                                // paste both do, but never while it has focus.
                                Component.onCompleted: text = editRow.time
                                onEdited: function (value) {
                                    draftModel.setProperty(editRow.index, "time", value)
                                }
                                // On commit, not on every keystroke: `0` on its
                                // way to becoming `01:30` would otherwise fling
                                // the row out from under the cursor.
                                onAccepted: root.resortLine(editRow.index)
                                onInputFocusChanged: {
                                    if (!inputFocus)
                                        root.resortLine(editRow.index)
                                }
                            }

                            NocturneField {
                                id: wordsField
                                anchors.left: timeField.right
                                anchors.leftMargin: Theme.gapSm
                                anchors.right: insertButton.left
                                anchors.rightMargin: Theme.gapXs
                                anchors.verticalCenter: parent.verticalCenter
                                clearable: false
                                placeholder: "…"
                                forwardMultilinePaste: true
                                Component.onCompleted: text = editRow.words
                                onEdited: function (value) {
                                    draftModel.setProperty(editRow.index, "words", value)
                                }
                                onPastedLines: function (lines) {
                                    var at = cursorPosition
                                    root.splitPaste(editRow.index,
                                                    text.slice(0, at),
                                                    text.slice(at),
                                                    lines)
                                }
                            }

                            IconButton {
                                id: insertButton
                                anchors.right: removeButton.left
                                anchors.verticalCenter: parent.verticalCenter
                                icon: "plus"
                                iconSize: 14
                                diameter: 28
                                tooltip: "Add a line beneath this one"
                                onClicked: root.insertLines(editRow.index + 1, [""])
                            }

                            IconButton {
                                id: removeButton
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                icon: "trash"
                                iconSize: 14
                                diameter: 28
                                tooltip: "Remove this line"
                                onClicked: root.removeLine(editRow.index)
                            }

                            // The model moving under a delegate is the one case
                            // the push-once rule does not cover, and both of
                            // these are ways it happens: a drop re-times a row,
                            // a paste rewrites one. Skipped while the field has
                            // focus, so nothing is ever taken out from under
                            // somebody's hands.
                            onTimeChanged: if (!timeField.inputFocus) timeField.text = editRow.time
                            onWordsChanged: if (!wordsField.inputFocus) wordsField.text = editRow.words
                        }
                    }

                    // The same draft, laid out in proportion. A synced
                    // document goes here instead of into the rows above: the
                    // geometry *is* the timing, so judging one is looking at
                    // it. The component owns geometry and gesture only —
                    // every mutation comes back through a signal and is
                    // applied by the draft functions at the top of this file,
                    // so the rules stay in one place.
                    LyricsTimeline {
                        id: editTimeline
                        objectName: "lyricsTimeline"
                        anchors.top: lyricsHeader.bottom
                        anchors.topMargin: paneTop.margin
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        visible: root.editing && root.timelineDraft

                        model: draftModel
                        seconds: root.draftSeconds
                        position: playerVM.position
                        // The guard is not decoration: a track whose audio the
                        // backend has not read reports a duration of 0, and
                        // there is no timeline to draw over no seconds.
                        extent: Math.max(playerVM.duration, root.lastDraftTime + 5, 1)

                        onSeekRequested: function (seconds) { playerVM.seek(seconds) }
                        onBoundaryMoved: function (index, seconds) {
                            root.moveBoundary(index, seconds)
                        }
                        onBoundaryShifted: function (index, seconds) {
                            root.shiftBoundary(index, seconds)
                        }
                        onInsertRequested: function (index) { root.insertLines(index, [""]) }
                        onRemoveRequested: function (index) { root.removeLine(index) }
                        onWordsEdited: function (index, text) {
                            draftModel.setProperty(index, "words", text)
                        }
                        onWordsPasted: function (index, before, after, lines) {
                            root.splitPaste(index, before, after, lines)
                        }
                        onTimeEdited: function (index, text) {
                            draftModel.setProperty(index, "time", text)
                        }
                        onTimeCommitted: function (index) { root.resortLine(index) }
                    }

                    // Declared beside their targets rather than outside the
                    // measure. SmoothWheel reparents itself onto `target.parent`
                    // and then anchors to it, and doing that across a boundary
                    // is a race it loses noisily once there are three of them.
                    // The timeline carries its own, because it also wants
                    // Ctrl+wheel back for the zoom.
                    SmoothWheel { target: lyricsList; enabled: lyricsList.visible }
                    SmoothWheel { target: plainLyricsFlick; enabled: plainLyricsFlick.visible }
                    SmoothWheel { target: editList; enabled: editList.visible }
                }
            }

            // ------------------------------------------------------ up next
            Item {
                // Measured like the lyrics, for a different budget. A queue row
                // at half width wants it all, which is why this was unmeasured;
                // at full width it no longer does — a row spanning a maximized
                // window puts its duration a foot from its title. Wider than
                // the lyrics' 680 because it is a row budget rather than a
                // line-length one, and half a window is under 820 at every size
                // the window can be, so the split layout is unchanged.
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width, 820)
                visible: !root.hasLyrics && !root.editing

                Text {
                    id: upNextTitle
                    anchors.top: parent.top
                    anchors.left: parent.left
                    text: "UP NEXT"
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                    font.weight: Font.DemiBold
                    font.letterSpacing: 1.4
                }

                // This pane is what a track with no lyrics shows, so it is where
                // the button that goes looking for some belongs. On success the
                // backend announces it, `hasLyrics` flips, and the pair of
                // `visible` bindings above swaps this pane for the words — no
                // switching logic of its own.
                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: upNextTitle.verticalCenter
                    spacing: 2

                    IconButton {
                        icon: "stage-mic"
                        iconSize: 16
                        diameter: 28
                        enabledAction: !lyricVM.is_fetching && playerVM.has_current_track
                        tooltip: lyricVM.is_fetching ? "Searching…"
                                                     : "Search for synced lyrics"
                        onClicked: lyricVM.fetchLyrics()
                    }

                    // A search that finds nothing used to be the end of it.
                    // The editor works from an empty document just as well as
                    // from a bad one, so this is the way to type in lyrics the
                    // internet does not have.
                    IconButton {
                        icon: "edit"
                        iconSize: 15
                        diameter: 28
                        enabledAction: playerVM.has_current_track
                        tooltip: "Write lyrics by hand"
                        onClicked: root.beginEdit()
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: upNext.count === 0
                    text: "Nothing else queued"
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                }

                ListView {
                    id: upNext
                    anchors.top: upNextTitle.bottom
                    anchors.topMargin: Theme.gap
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    clip: true
                    spacing: 2
                    model: queueVM

                    ScrollBar.vertical: ScrollBar { width: 6 }

                    delegate: Item {
                        id: nextRow
                        required property int index
                        required property var title
                        required property var artist
                        required property var coverHash
                        required property var duration

                        // Row 0 is the track already playing.
                        readonly property bool upcoming: index > 0
                        width: ListView.view.width
                        height: upcoming ? 52 : 0
                        visible: upcoming

                        Rectangle {
                            anchors.fill: parent
                            anchors.rightMargin: 4
                            radius: Theme.radiusSm
                            color: nextMouse.containsMouse
                                   ? Theme.alpha(Theme.text, 0.06) : "transparent"
                            Behavior on color { ColorAnimation { duration: Theme.fast } }
                        }

                        Row {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 12
                            spacing: Theme.gapSm

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 18
                                text: nextRow.index
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzTiny
                                font.features: ({ "tnum": 1 })
                                horizontalAlignment: Text.AlignRight
                            }

                            CoverArt {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 36
                                height: 36
                                radius: 6
                                hash: String(nextRow.coverHash || "")
                                placeholderText: String(nextRow.title || "")
                                showPlaceholderGlyph: false
                            }

                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 18 - 36 - 44 - Theme.gapSm * 3
                                spacing: 2

                                Text {
                                    width: parent.width
                                    text: String(nextRow.title || "")
                                    elide: Text.ElideRight
                                    color: Theme.text
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fzBody
                                    font.weight: Font.Medium
                                }
                                Text {
                                    width: parent.width
                                    text: String(nextRow.artist || "")
                                    elide: Text.ElideRight
                                    color: Theme.faint
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fzTiny
                                }
                            }

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 40
                                horizontalAlignment: Text.AlignRight
                                text: Number(nextRow.duration) > 0
                                      ? Fmt.duration(nextRow.duration) : ""
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzTiny
                                font.features: ({ "tnum": 1 })
                            }
                        }

                        MouseArea {
                            id: nextMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onDoubleClicked: playerVM.play_queue_rows([nextRow.index])
                        }
                    }
                }

                SmoothWheel { target: upNext }
            }

            // The mirror of the art half's. A hidden half cannot be hovered, so
            // neither handle needs a `visible` of its own beyond `offered`.
            HoverHandler { id: wordsHover }

            FocusHandle {
                objectName: "wordsFocusHandle"
                offered: root.wide
                revealed: wordsHover.hovered
                focused: root.focusHalf === "words"
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme.gapSm
                onToggled: root.toggleFocus("words")
            }
        }
    }

    signal detailsRequested()

    // "No lyrics found" is an ordinary answer, not a failure, so this shows as a
    // plain toast rather than an error one — the same wiring TrackEditView uses
    // for its own view model's messages.
    Connections {
        target: typeof lyricVM !== "undefined" ? lyricVM : null
        function onFetch_failed(message) { toast.show(message, false) }

        // A refused save *is* an error — the user typed something that cannot
        // be stored, and the editor stays open on top of it rather than
        // throwing it away. See saveDraft().
        function onEdit_failed(message) {
            root.editRefused = true
            toast.show(message, true)
        }
    }

    // A seek is a jump, and the timeline's playhead interpolates between the
    // few position updates a second the backend sends — so without this it
    // would glide across the whole gap instead of arriving. Jumping is also a
    // statement about where you want to be looking, which is why it puts
    // following back on.
    Connections {
        target: typeof playerVM !== "undefined" ? playerVM : null
        function onSeeked(seconds) { editTimeline.snap() }
    }

    AddToPlaylistDialog {
        id: addDialog
    }

    Toast { id: toast }
}
