import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import Nocturne
import "TrackColumns.js" as Catalog

// The workhorse: one track list used by the queue, history, search results,
// playlists, and the library browser.
//
// Selection is keyed on `uid` (the per-row instance id), never `trackId` — the
// same track can legitimately appear twice in a queue. Row identity is read
// through the model's uidAt/trackIdAt slots so no Qt role numbers appear here.
//
// Columns come from `columnLayout`, a TrackColumnLayout profile. The row is
// three regions and only the middle one is column-managed:
//
//   |<-side->|  LEAD  |             FLEX              | TRAIL |<-side->|
//   |        |[num]art| title | col | col | …         | more  |        |
//
// `leadWidth` and `trailWidth` below are the single definition of the two fixed
// regions. The header reads the same two properties, which is what keeps its
// cells over the row's cells — neither computes its own geometry.
Item {
    id: root

    // "queue" | "history" | "search" | "playlist" | "library"
    property string mode: "library"
    property var listVM: null
    property bool active: false
    property bool gridMode: false
    property bool gridAvailable: false
    property int tileSize: 200
    readonly property var songGrid: songGridLoader.item
    readonly property int artworkSize: Math.min(42, Math.max(24, rowHeight - 12))


    property bool showIndex: false
    // Rows can be dragged by their number: up and down to reorder the list,
    // right to throw them out of it. The queue and playlists — the two lists
    // with an order worth changing, which a search result is not. The model has
    // to expose moveRows(rows, to) and removeAtRows(rows); both are looked for
    // by name before the gesture commits, so a list that offers only the first
    // reorders and quietly declines to remove.
    property bool reorderable: false
    // Whether rows carry their track's artwork. An album page turns it off: it
    // is one record under one cover, and repeating that cover down every row
    // says nothing the hero has not already said. Turning it off also takes the
    // row's play button with it, since that button *is* the artwork — so the
    // number slot grows one in its place. See `numberSlot.playAction`.
    property bool showArt: true
    property bool webMode: false
    property int rowHeight: Theme.rowHeight
    property int sideMargin: Theme.gapLg

    // The column profile this list draws with. Without one the list still
    // works — it just shows title and artist and no header.
    property var columnLayout: null

    // Which action the STATUS column offers, if the profile has one:
    // "web" (download a search result) | "import" (stash or download a
    // candidate) | "" (no status column here).
    property string statusMode: ""

    // Optional content that scrolls above the first row rather than sitting in
    // a separate pane above the list. The Library page puts its whole overview
    // in here, so reading down the tracks reclaims the space it occupies.
    property Component listHeader: null

    property var selectedIds: new Set()
    // uid -> selected (1), joined above (2), joined below (4). Built alongside
    // the sidebar's existing selection walk, only on selection/order changes.
    // Delegates read one cached value; scrolling makes no neighbour queries.
    property var _selectionShapes: ({})
    property int selectionAnchor: -1
    // Snapshots taken at popup time, so a menu item acts on what was selected
    // when it opened rather than on whatever the list looks like by the time
    // it is triggered.
    property var _menuTrackIds: []
    property var _menuRows: []
    property int _menuUid: -1
    property int _menuTrackId: -1
    property int _menuRow: -1
    property string _menuTitle: ""
    // The whole web-search result behind the right-clicked row, fetched when
    // Import local file… is chosen and held for as long as its file dialog is
    // up — which outlives the menu, and outlives `_menuUid` pointing at it.
    property var _importMeta: null

    // Rows that just arrived, wearing the flash — see `revealAdded`. Keyed by
    // uid for the same reason `selectedIds` is: a row taken out of the list
    // mid-flash must not hand its wash to whatever slides up into its index.
    // Reassigned rather than mutated, because QML does not see a Set change
    // under it (see `clearSelection`).
    property var _flashUids: new Set()
    // How bright the flash is right now, for every flashing row at once. One
    // animation rather than one per delegate, so a row recycled into view
    // halfway through picks up the level already in force instead of starting
    // its own fade from full.
    property real _flashLevel: 0

    // Rows leaving because playback moved past them rather than because anyone
    // took them out — see `markAdvanceRemoved` and the `remove` transition.
    // Keyed by uid for the reason `_flashUids` is, and for one more: the row is
    // named before it goes, so by the time the transition asks, its index means
    // someone else.
    property var _advanceUids: new Set()

    /* uid -> how far right the hand had pulled it, for rows that have just been
     * thrown out by their grip and are riding their exit.
     *
     * The exit throws a row off the right-hand edge from wherever it happens to
     * be, so holding the offset is the whole of what makes a pulled row carry
     * on from the hand rather than snapping back to the left to start again.
     * Keyed by uid for the reason `_advanceUids` is — the row is out of the
     * model by the time anything reads this, so its index means someone else.
     *
     * See `_flingOff`, which fills it, and `dismissForget`, which empties it.
     */
    property var _dismissedShift: ({})

    signal rowActivated(int row, int trackId)

    // Raised by the context menu. The page decides where that goes — this
    // component knows about track lists, not about navigation.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)
    // A row has been queued for import and the import list is worth looking at.
    // Where that list lives is the page's business, not this component's.
    signal importPageRequested()
    // A copy happened and it left no visible trace. This component has no Toast,
    // so the page it sits on owns the confirmation. `text` is what was copied.
    signal copied(string text)
    // An artist row was activated. Only a search in "best" or "artists" mode
    // ever produces one; every other list is tracks throughout and never raises
    // this. Where it goes is the page's business — see the note on
    // generateBackingRequested.
    signal artistActivated(int artistId)

    // And an album row, which only the "albums" search mode produces.
    signal albumActivated(int albumId)

    // Read the active presentation: Qt reports no ListView items while its
    // delegate is disabled. Both counts notify without walking the model.
    readonly property int count: gridMode && songGrid ? songGrid.count : list.count

    // ------------------------------------------------------------- geometry

    // The two fixed regions either side of the columns. Read by the header as
    // well as the rows, so they cannot disagree.
    readonly property int leadWidth: (showIndex ? Catalog.NUM_W + Theme.gap : 0)
                                     + (showArt ? artworkSize + Theme.gap : 0)
    readonly property int trailWidth: Catalog.MORE_W + Theme.gap

    // What is left for the columns and the gaps between them. The gaps depend
    // on how many columns end up rendered, which is what the layout is
    // deciding, so it subtracts those itself.
    readonly property real _rawBudget:
        list.width - 2 * sideMargin - leadWidth - trailWidth

    function _pushBudget() {
        if (columnLayout)
            columnLayout.setBudget(_rawBudget, Theme.gap);
    }

    // Width alone is not enough to drive this: the queue flips `showIndex`
    // between queue and history, and Browse swaps the whole layout object
    // between library and web search — both change the budget without the list
    // ever changing width.
    on_RawBudgetChanged: _pushBudget()
    onColumnLayoutChanged: _pushBudget()

    readonly property int columnHeaderHeight: !gridMode && columnLayout && list.count > 0 ? 24 : 0

    // The Library puts its whole overview in `listHeader`, and a column header
    // pinned above that would sit on top of a hero panel. There it rides the
    // overview down and sticks at the top instead; everywhere else there is
    // nothing to ride and it simply docks.
    readonly property bool _sticky: listHeader !== null

    // Viewport y of the band reserved for the header. A ListView header lives
    // at negative content coordinates — that is what originY records — so its
    // bottom edge is at originY + height - contentY, and the reserved band
    // starts one header-height above that. Clamped at 0, it pins.
    readonly property real _headerY: _sticky && list.headerItem
        ? Math.max(0, list.originY + list.headerItem.height
                      - columnHeaderHeight - list.contentY)
        : 0

    // ------------------------------------------------------------ reordering
    //
    // Dragging a row by its grip does not touch the model. Every row offsets
    // itself from the slot the view gave it, so the whole gesture is arithmetic
    // on a few numbers, and one moveRows() commits at the end — by which point
    // the rows are already sitting where the model is about to put them, so the
    // handover is invisible.
    //
    // Committing as the pointer crossed each row would be the obvious
    // alternative and is the wrong trade: every move runs a queue mutation and
    // a full re-read of the id vector, so passing ten rows with three selected
    // would be thirty of them.

    // Source rows, ascending. Empty when nothing is being dragged.
    property var _dragRows: []
    // The row whose grip was grabbed, and where that row sits inside _dragRows.
    property int _dragAnchor: -1
    property int _anchorK: 0
    // The pointer, in screen coordinates. The grip's MouseArea rides the row it
    // is shifting, so a local delta would feed back into itself: move the row
    // down by d and a stationary pointer reports mouse.y - d, which reads as
    // the drag stalling. Global coordinates are the fixed frame it needs — and
    // they cost nothing, because mouse.y compensates for the shift exactly, so
    // mapToGlobal of it is stable.
    property real _dragPressY: 0
    property real _dragPointerY: 0
    property real _dragContentY0: 0
    // The same pair on the other axis, for the dismiss gesture below. Global
    // for the same reason, and now doubly so: the grip rides `rowCard`, which
    // is the item the horizontal offset is applied to.
    property real _dragPressX: 0
    property real _dragPointerX: 0

    /* Which of the two things this drag turned out to be.
     *
     * The grip does two jobs and a drag may only ever be doing one of them. The
     * choice is read off the first few pixels of movement and then fixed for as
     * long as the button is down: a drag reorders or it removes, never both and
     * never one after the other. Letting go and grabbing again is what changes
     * its mind.
     *
     * Everything downstream keys off this rather than off the two axes, so a
     * reorder carries no sideways offset at all and a removal displaces nothing
     * — neither has to be talked out of the other's half of the gesture.
     */
    property string _dragMode: ""            // "reorder" | "dismiss" | ""
    readonly property bool _reordering: _dragMode === "reorder"
    readonly property bool _dismissing: _dragMode === "dismiss"

    // The release glide is in flight: the block animates to its slot rather
    // than following a pointer that has let go.
    property bool _dropping: false
    // The other one: a pull that did not go far enough, returning to its row.
    property bool _springing: false
    // Either. The pointer is gone and the gesture is finishing itself.
    readonly property bool _releasing: _dropping || _springing
    // The window in which the model takes over from the offsets. Every Behavior
    // is off inside it, and the view's own move transitions are held to zero, so
    // the swap does not animate a move that has already visually happened.
    property bool _committing: false

    readonly property bool _dragging: _dragRows.length > 0

    // How far the anchor has travelled from its own slot. Scrolling moves the
    // slot rather than the offset, so folding contentY in here is what lets the
    // edge auto-scroll below work without anyone writing to _dragPointerY.
    readonly property real _dragY:
        (_dragPointerY - _dragPressY) + (list.contentY - _dragContentY0)

    /* The first row this list will let anything be moved to, or picked up from.
     *
     * The playing track is queue row 0, and "queue[0] is what is playing" is an
     * invariant the backend, the player bar and the Now Playing page all read
     * off the same index. So it is not a drop target and nothing may land on it.
     *
     * That is a rule about the queue and no other list. A playlist row has no
     * positional invariant to protect — playing a playlist copies its ids into
     * the queue, and the two go their own way from there — so every row of one
     * is fair game, including whichever happens to be playing.
     *
     * `numberSlot.canDrag` reads this too, so the row that may not be landed on
     * is exactly the row that offers no grip. One rule, asked twice.
     */
    readonly property int _minDropRow:
        (mode === "queue" && playerVM.has_current_track) ? 1 : 0
    readonly property int _maxDropRow:
        Math.max(_minDropRow, list.count - _dragRows.length)

    // _dragY, bounded to the travel that keeps the block inside the list, so it
    // meets resistance at either end instead of sliding off one.
    readonly property real _dragYClamped: {
        if (!_dragging)
            return 0
        var head = _dragAnchor - _anchorK
        return Math.max((_minDropRow - head) * rowHeight,
                        Math.min((_maxDropRow - head) * rowHeight, _dragY))
    }

    // Where the head of the block lands, indexed against the list with the
    // block already taken out — the space moveRows() reads its argument in, and
    // the only one that stays unambiguous when the selection straddles the drop
    // point.
    //
    // Math.round is what moves the neighbours: crossing a row's midpoint bumps
    // this by one, and every row it passed flips its offset by a whole rowHeight.
    readonly property int _dropDest: {
        if (!_dragging)
            return -1
        var head = _dragAnchor - _anchorK
                   + Math.round(_dragYClamped / rowHeight)
        return Math.max(_minDropRow, Math.min(_maxDropRow, head))
    }

    // --------------------------------------------------------------- dismiss
    //
    // The same grip, pulled the other way. A block pulled to the right is thrown
    // out of the queue rather than moved within it, which puts a second removal
    // on the gesture the hand is already making — the only other one is a
    // context menu.
    //
    // Nothing here displaces a row, because a drag in this mode is not a
    // reorder: the block slides out of its own slots and the rest of the list
    // sits still. That is what lets the release be an ordinary removal rather
    // than a handover — see `_flingOff`.
    //
    // The pull has two regimes and one boundary between them. Short of the
    // boundary the card meets a resistance that grows until it will not come
    // any further; past it the card snaps across and tracks the pointer
    // exactly, which is the gesture saying it has changed meaning. The boundary
    // is symmetric: coming back over it puts the resistance back, from wherever
    // the pointer now is, and a release there removes nothing.

    // A pull is never perfectly horizontal either, and the first few pixels of
    // one should not have the card twitching out of its row.
    readonly property real _dismissSlack: 6
    // The furthest a resisted pull ever reaches.
    readonly property real _dismissTravel: 44
    // The pointer travel that snaps it over. The gap between this and the
    // travel above is the size of the snap, and the snap is what has to be
    // unmistakable — it is the whole of the warning that a release now removes.
    readonly property real _dismissThreshold: 110

    readonly property real _dragX: _dismissing
        ? Math.max(0, (_dragPointerX - _dragPressX) - _dismissSlack)
        : 0

    // A quadratic that arrives at `_dismissTravel` with zero slope exactly at
    // the threshold: the card starts out following the pointer at about 0.8 of
    // its speed, is progressively held back, and stops dead where the snap is.
    readonly property real _dragXResisted: {
        var u = Math.min(1, _dragX / _dismissThreshold)
        return _dismissTravel * u * (2 - u)
    }

    readonly property bool _dismissArmed: _dismissing && _dragX >= _dismissThreshold

    /* How far across the two regimes the card currently is, and the reason the
     * snap can be eased without the tracking that follows it being eased too.
     *
     * A Behavior on the card's own offset would smooth every pointer move, and
     * "follows the mouse exactly" is the requirement. So the easing lives here,
     * on one number, and the offset below is an unbuffered function of it: at 1
     * the resisted term is pinned at its maximum and the offset is `_dragX`
     * itself, with nothing between the hand and the card.
     */
    property real _snap: _dismissArmed ? 1 : 0
    Behavior on _snap {
        NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
    }

    readonly property real _dragXOffset:
        _dragXResisted + _snap * (_dragX - _dragXResisted)

    /* The spring back from a pull that did not go far enough.
     *
     * An explicit animation rather than a Behavior on the card, and that is the
     * whole point of it. A Behavior would have to be switched on at the moment
     * the target changes, and both would be reading the same flag in the same
     * change propagation — QML does not order those two, so the card would
     * sometimes be written before the Behavior was armed and blink home instead
     * of gliding. Driving one number here leaves nothing to race.
     */
    property real _springX: 0
    NumberAnimation {
        id: springBack
        target: root
        property: "_springX"
        to: 0
        duration: Theme.normal
        easing.type: Theme.easing
        // The drag is not over until the card is home. Clearing it any earlier
        // would drop `_dragRows` out from under `_cardShiftFor` mid-glide.
        onFinished: {
            root._springing = false
            root._clearDrag()
        }
    }

    // The horizontal offset for one row. Pointer-driven while the pull is on,
    // animation-driven while it springs back, and nothing at all in the other
    // mode — a reorder never moves a card sideways.
    function _cardShiftFor(index) {
        if (!_dismissing || _dragRows.indexOf(index) < 0)
            return 0
        return _springing ? _springX : _dragXOffset
    }

    // Where a row sits relative to the slot the view gave it, mid-drag. A plain
    // function rather than a property because it is per-row; called from a
    // binding it still captures what it reads, the same way cellText() does.
    function _shiftFor(index) {
        // A removal is not a reorder, so it moves nothing vertically: the block
        // stays on its own rows and the rest of the list sits still.
        if (!_reordering)
            return 0

        var m = _dragRows.length
        var k = _dragRows.indexOf(index)

        if (k >= 0) {
            // A member of the block. On the way down it heads for its final
            // slot; under the pointer it stacks around the anchor and travels
            // with it, and that gathering is what makes three scattered rows
            // read as one object rather than three.
            if (_dropping)
                return (_dropDest + k - index) * rowHeight
            return _dragYClamped + ((_dragAnchor + k - _anchorK) - index) * rowHeight
        }

        // Everything else is displaced by wherever it ends up once the block is
        // lifted out and put back. Counting the block rows above it first is
        // what gets a straddling selection right — without that, rows on the
        // far side of the drop point land one out.
        var above = 0
        for (var i = 0; i < m; i++)
            if (_dragRows[i] < index)
                above++
        var rest = index - above
        return ((rest < _dropDest ? rest : rest + m) - index) * rowHeight
    }

    // The rows a drag starting on `row` carries: the whole selection when `row`
    // is part of a multi-row one, otherwise just `row`. The playing track is
    // filtered out, so selecting everything and dragging moves what it can
    // rather than refusing.
    function _blockFor(row) {
        if (selectedIds.size <= 1 || !listVM || !selectedIds.has(listVM.uidAt(row)))
            return [row]
        var rows = selectedRows().filter(function (r) { return r >= root._minDropRow })
        return rows.indexOf(row) >= 0 ? rows : [row]
    }

    // `mode` is fixed for the life of the drag — see `_dragMode`. It is decided
    // by the grip, which is the only thing that can see which way the hand went
    // first.
    function _beginDrag(row, globalY, globalX, mode) {
        var block = _blockFor(row)
        _dragMode = mode
        _dragRows = block
        _dragAnchor = row
        _anchorK = block.indexOf(row)
        _dragPressY = globalY
        _dragPointerY = globalY
        _dragPressX = globalX
        _dragPointerX = globalX
        _dragContentY0 = list.contentY
    }

    function _clearDrag() {
        // Stopping the timer is what makes this safe to call while a drop is
        // still gliding: an abandoned drag takes the commit down with it rather
        // than leaving one queued against state that has since been cleared.
        dropTimer.stop()
        springBack.stop()
        _dropping = false
        _springing = false
        _dragMode = ""
        _dragRows = []
        _dragAnchor = -1
        _anchorK = 0
    }

    function _endDrag(commit) {
        if (!_dragging)
            return

        if (_dismissing) {
            // Let go past the boundary, so the gesture was a removal. Below it,
            // the card goes home and nothing else happens — there is no reorder
            // waiting to be committed, because this drag was never one.
            if (commit && _dismissArmed)
                _flingOff()
            else
                _springHome()
            return
        }

        if (!commit) {
            // Offsets fall back to zero with their Behaviors live, so an
            // abandoned drag springs back rather than blinking back.
            _clearDrag()
            return
        }
        _dropping = true
        dropTimer.restart()
    }

    /* Throw the block out, and let the ordinary exit carry it.
     *
     * The removal is sent here and now, exactly as the context menu sends it, so
     * the rows leave through `remove` and the list closes up through
     * `removeDisplaced` — the same departure, from the same two transitions.
     *
     * The one thing the gesture adds is where the rows start from. They are
     * already out to the right, under the hand that threw them, and the exit
     * animates the delegate's own `x` on top of whatever offset they are
     * holding — so it carries on from there instead of yanking them back to the
     * left edge to begin. `_dismissedShift` is what holds that offset once the
     * drag state behind it is gone.
     */
    function _flingOff() {
        var rows = _dragRows
        if (!listVM || typeof listVM.removeAtRows !== "function") {
            _springHome()
            return
        }

        // Read the uids out before the rows go: afterwards there is no row left
        // to read one off. Same reason `advance_removed` is emitted first.
        //
        // Merged rather than replaced, and keyed by uid rather than by row,
        // because both of those stop meaning anything the moment the model
        // changes — and a second throw inside the forget window must not strand
        // the first one mid-flight.
        var held = {}
        for (var key in _dismissedShift)
            held[key] = _dismissedShift[key]
        for (var i = 0; i < rows.length; i++)
            held[listVM.uidAt(rows[i])] = _dragXOffset
        _dismissedShift = held

        _clearDrag()
        listVM.removeAtRows(rows)
        dismissForget.restart()
    }

    // A pull that did not go far enough. The card returns to its row under
    // `springBack`, which clears the drag when it lands.
    function _springHome() {
        // Nothing to glide: a pull inside the dead zone never left the row.
        if (_dragXOffset < 0.5) {
            _clearDrag()
            return
        }
        // Assigned before the flag that makes anything read it, so there is no
        // frame in which the card sees a stale zero and blinks home.
        _springX = _dragXOffset
        _springing = true
        springBack.from = _springX
        springBack.start()
    }

    // The block glides to its slot, and then the model takes over. The two have
    // to be the same picture at the instant of the swap, which is what
    // _committing buys: offsets go to zero and the rows take their real
    // positions with every Behavior off, so nothing animates the same move
    // twice.
    Timer {
        id: dropTimer
        interval: Theme.normal
        onTriggered: {
            // Read both out before clearing — they are derived from the drag
            // state and go undefined with it.
            var rows = root._dragRows
            var dest = root._dropDest
            root._committing = true
            root._clearDrag()
            if (root.listVM && typeof root.listVM.moveRows === "function")
                root.listVM.moveRows(rows, dest)
            commitSilence.restart()
        }
    }

    /* How long `_committing` outlives the commit itself.
     *
     * Clearing it on the next line would be enough for the Behaviors, which
     * fire the instant a property changes. It is not enough for the view's
     * `move` transition: the model change is queued and the transition is not
     * built until the next polish, by which point a flag cleared synchronously —
     * or from Qt.callLater, or from a zero-interval Timer, all of which run
     * first — reads false, and the block would spring back to its old slots and
     * glide the same move a second time.
     *
     * So it is measured in frames rather than taste: three at 60Hz, and still
     * far shorter than the gap between a drop and anything else that could
     * reorder this list, since the only other thing that reorders it is a
     * double-click the user has not had time to make.
     */
    Timer {
        id: commitSilence
        interval: 50
        onTriggered: root._committing = false
    }

    // The counterpart of `advanceForget`, and housekeeping for the same reason:
    // a uid is never reused, so a stale entry cannot match a later row. The
    // interval only has to outlast the exit it is holding an offset for.
    Timer {
        id: dismissForget
        interval: Theme.slow
        onTriggered: root._dismissedShift = ({})
    }

    // Reaching past the viewport. A queue longer than the window is the normal
    // case, so a drag that cannot leave the visible rows cannot do the job.
    // _dragY already folds contentY in, so the block stays under the cursor
    // while the list moves beneath it.
    //
    // Reordering only. A removal travels sideways and has nowhere further down
    // the list to reach for, so scrolling under one would only drag the queue
    // about while the hand was busy elsewhere.
    Timer {
        id: edgeScroll
        interval: 16
        repeat: true
        running: root._reordering && !root._releasing
                 && list.contentHeight > list.height
        onTriggered: {
            var y = list.mapFromGlobal(0, root._dragPointerY).y
            var zone = root.rowHeight
            var step = 0
            if (y < zone)
                step = -Math.max(2, (zone - y) / 4)
            else if (y > list.height - zone)
                step = Math.max(2, (y - (list.height - zone)) / 4)
            if (step === 0)
                return
            var span = Math.max(0, list.contentHeight - list.height)
            list.contentY = Math.max(list.originY,
                                     Math.min(list.originY + span,
                                              list.contentY + step))
        }
    }

    // ------------------------------------------------------------- selection

    function _syncFromModel() {
        deselectTimer.stop()
        selectionAnchor = -1
        if (listVM && typeof listVM.selectedUids === "function")
            selectedIds = new Set(listVM.selectedUids())
        else
            selectedIds = new Set()
    }

    // Re-read a selection the backend changed on its own: a track played
    // through leaves the queue and loses its selection, and one pulled back out
    // of history returns as a new instance that was never selected. Without
    // this the mirror below keeps a uid that no longer exists anywhere, and the
    // details panel goes on reporting a track that is not selected any more.
    //
    // Unlike _syncFromModel this keeps the shift-click anchor unless the row it
    // names has actually gone, and leaves deselectTimer alone — it re-checks
    // membership when it fires, for exactly this case.
    function _resyncSelection() {
        if (!listVM || typeof listVM.selectedUids !== "function")
            return
        var next = new Set(listVM.selectedUids())
        // Our own writes come back through here. Bailing on an unchanged set
        // keeps a click from reporting its selection to the sidebar twice.
        if (next.size === selectedIds.size
                && Array.from(next).every(function (u) { return selectedIds.has(u) }))
            return
        selectedIds = next          // onSelectedIdsChanged -> reportSelection()
        if (selectionAnchor >= 0 && !next.has(selectionAnchor))
            selectionAnchor = -1
    }

    Connections {
        target: root.listVM
        // Not every list is a TrackListModel: the import candidates model has
        // no such signal.
        ignoreUnknownSignals: true
        function onSelectionChanged() { root._resyncSelection() }
        function onModelReset() { root.reportSelection() }
        function onRowsInserted() { root.reportSelection() }
        function onRowsRemoved() { root.reportSelection() }
        function onRowsMoved() { root.reportSelection() }
        function onLayoutChanged() { root.reportSelection() }
    }

    function selectedTrackIds() {
        if (!listVM)
            return []
        var ids = []
        for (var i = 0; i < listVM.rowCount(); i++) {
            if (selectedIds.has(listVM.uidAt(i))) {
                var tid = listVM.trackIdAt(i)
                if (tid !== undefined && tid !== null && tid > 0)
                    ids.push(tid)
            }
        }
        return ids
    }

    // Row indices of the selection, ascending. The model answers this in one
    // call when it can; ImportViewModel is not a TrackListModel and cannot, so
    // fall back to walking.
    function selectedRows() {
        if (!listVM)
            return []
        if (typeof listVM.selectedRows === "function")
            return listVM.selectedRows()
        var rows = []
        for (var i = 0; i < listVM.rowCount(); i++)
            if (selectedIds.has(listVM.uidAt(i)))
                rows.push(i)
        return rows
    }

    function reportSelection() {
        var shapes = ({})
        var ids = []
        var report = active && listVM && typeof sidebarVM !== "undefined"
        if (listVM && selectedIds.size > 0) {
            var previousRow = -2
            var previousUid = -1
            var n = listVM.rowCount()
            for (var i = 0; i < n; i++) {
                var uid = listVM.uidAt(i)
                if (!selectedIds.has(uid))
                    continue
                var shape = 1
                if (previousRow === i - 1) {
                    shape |= 2
                    shapes[previousUid] |= 4
                }
                shapes[uid] = shape
                previousRow = i
                previousUid = uid
                if (report) {
                    var tid = listVM.trackIdAt(i)
                    if (tid !== undefined && tid !== null && tid > 0)
                        ids.push(tid)
                }
            }
        }
        _selectionShapes = shapes
        if (report)
            sidebarVM.setSelection(ids)
    }

    function _rowOfUid(uid) {
        for (var i = 0; i < listVM.rowCount(); i++)
            if (listVM.uidAt(i) === uid)
                return i
        return -1
    }

    function _selectRange(fromUid, toUid) {
        var a = _rowOfUid(fromUid)
        var b = _rowOfUid(toUid)
        if (a < 0 || b < 0)
            return
        var set = new Set()
        for (var i = Math.min(a, b); i <= Math.max(a, b); i++)
            set.add(listVM.uidAt(i))
        selectedIds = set
        if (typeof listVM.setSelectedMultiple === "function")
            listVM.setSelectedMultiple(Array.from(set))
    }

    // A plain click on the only selected row deselects it — but not straight
    // away: clicking a row that is already selected and then clicking again is
    // how you play it, so the deselect holds back long enough for a second
    // click to arrive and call it off.
    //
    // Long enough to sit out an ordinary double click, and no longer. The
    // platform's interval is the ceiling rather than the value: it is a
    // generous upper bound — 900ms on this machine — and a row that stays lit
    // for the better part of a second after you click it reads as broken. The
    // cap is safe because a double click slower than it is not lost, only
    // deselected between its two presses and put back on the second; see
    // rowMouse.onDoubleClicked.
    //
    // The size and membership are checked again on the way out because the
    // selection can change from anywhere — a menu action, the queue moving on —
    // without anyone thinking to stop a timer.
    Timer {
        id: deselectTimer
        interval: Math.min(Application.styleHints.mouseDoubleClickInterval, 250)
        property var uid: -1
        onTriggered: {
            if (root.selectedIds.size === 1 && root.selectedIds.has(uid))
                root.clearSelection()
        }
    }

    function handleClick(uid, ctrl, shift) {
        deselectTimer.stop()
        if (shift && selectionAnchor >= 0) {
            _selectRange(selectionAnchor, uid)
        } else if (ctrl) {
            var set = new Set(selectedIds)
            if (set.has(uid))
                set.delete(uid)
            else
                set.add(uid)
            selectedIds = set
            selectionAnchor = uid
            if (typeof listVM.setSelectedMultiple === "function")
                listVM.setSelectedMultiple(Array.from(set))
        } else if (selectedIds.size === 1 && selectedIds.has(uid)) {
            // Clicking the one row that is selected: arm the deselect and
            // change nothing, so the wait is invisible until it pays out.
            deselectTimer.uid = uid
            deselectTimer.restart()
            return
        } else {
            selectedIds = new Set([uid])
            selectionAnchor = uid
            if (typeof listVM.setSelectedExclusive === "function")
                listVM.setSelectedExclusive(uid)
        }
    }

    function clearSelection() {
        deselectTimer.stop()
        selectedIds = new Set()
        selectionAnchor = -1
        if (listVM && typeof listVM.clearSelection === "function")
            listVM.clearSelection()
    }

    // --------------------------------------------------------------- actions

    // Play one or more rows. In the queue, position is the identity — the same
    // track can sit in it twice — so queue rows go by index and everything
    // else by track id. Either way the tracks move to the front of the queue
    // in the order given; nothing they were sitting behind is disturbed.
    function playRows(rows, trackIds) {
        // Playing is never a deselect, whichever way it was asked for — this
        // also covers the artwork button, which takes its own click and never
        // reaches handleClick.
        deselectTimer.stop()
        if (mode === "queue")
            playerVM.play_queue_rows(rows)
        else if (trackIds.length > 0)
            playerVM.play_from_ids(trackIds)
        rowActivated(rows.length > 0 ? rows[0] : -1,
                     trackIds.length > 0 ? trackIds[0] : -1)
    }

    function playRow(row, trackId) {
        playRows([row], trackId > 0 ? [trackId] : [])
    }

    function openTrackMenu(uid, trackId, row, title) {
        deselectTimer.stop()
        if (!selectedIds.has(uid))
            handleClick(uid, false, false)
        _menuUid = uid
        _menuTrackId = trackId
        _menuRow = row
        _menuTitle = title
        _menuTrackIds = selectedTrackIds()
        _menuRows = selectedRows()
        _menuActions = (typeof pluginsVM !== "undefined" && pluginsVM && listVM
                        && _rowsForMenu().length <= 1)
                       ? pluginsVM.actionsFor("track", listVM, uid) : []
        rowMenu.popup()
    }

    // Every menu item acts on the whole selection. Right-clicking outside the
    // selection collapses it to that row first (see openMenu), so the snapshot
    // is already the right answer in both cases; the fallbacks only cover a
    // model that cannot report a selection at all.
    function _idsForMenu() {
        return _menuTrackIds.length > 0 ? _menuTrackIds
                                        : (_menuTrackId > 0 ? [_menuTrackId] : [])
    }

    function _rowsForMenu() {
        return _menuRows.length > 0 ? _menuRows
                                    : (_menuRow >= 0 ? [_menuRow] : [])
    }

    // Only rows that are actually in the library. Import candidates carry no id
    // until they are stashed, and there is nothing to hang an instrumental on
    // until they are.
    function _libraryIdsForMenu() {
        return _idsForMenu().filter(function (id) { return Number(id) > 0 })
    }

    onListVMChanged: {
        _syncFromModel()
        list.positionViewAtBeginning()
        _pushVisibleRange()
    }
    // Registering here is what lets Escape and a press on the ambient
    // background reach this list without every view in between forwarding a
    // signal for it. The guard on the way out matters: a view change
    // re-evaluates both tables' `active` bindings in no particular order, and
    // the outgoing one must not clear a registration the incoming one has
    // already made.
    onActiveChanged: {
        if (active) {
            TrackSelection.table = root
            reportSelection()
        } else if (TrackSelection.table === root) {
            TrackSelection.table = null
        }
    }

    onSelectedIdsChanged: reportSelection()

    // Tell the backend which rows are on screen so it can fill in the fields
    // those rows need. Without this, columns stay blank while scrolling — and
    // the backend has no other way to tell the rows the user has landed on from
    // the thousands a scrollbar drag went past, so it reads all of them.
    //
    // Called on every contentY change, and deliberately not throttled. The
    // backend decides "swept past" versus "being read" from how far the range
    // moved between two reports, so dropping samples would make ordinary
    // scrolling look like a jump and defer its data for no reason. Each call is
    // one slot invocation doing set arithmetic — `list.count` is a C++ property
    // read rather than a call into Python, which is what keeps it that cheap.
    function _pushVisibleRange() {
        if (!listVM || typeof listVM.setVisibleRange !== "function")
            return
        var n = root.count
        if (n <= 0)
            return
        // Row 0 sits at content y 0 whether or not there is a header: a header
        // occupies negative content coordinates, which is what originY records.
        // So this arithmetic needs no adjusting for one.
        var view = gridMode ? songGrid : list
        if (!view)
            return
        var stride = gridMode ? songGrid.cellHeight : rowHeight
        var columns = gridMode ? songGrid.columns : 1
        var first = Math.max(0, (Math.floor(view.contentY / stride) - 4) * columns)
        var last = Math.min(n - 1, (Math.ceil((view.contentY + view.height) / stride) + 4) * columns)
        listVM.setVisibleRange(first, last)
    }

    // Keep the first visible song in view when changing presentation. Only the
    // current presentation creates delegates; selection stays on this owner.
    onGridModeChanged: {
        var row = gridMode ? Math.max(0, Math.floor(list.contentY / rowHeight))
                           : songGrid ? Math.max(0, Math.floor(songGrid.contentY / songGrid.cellHeight) * songGrid.columns) : 0
        Qt.callLater(function() {
            var view = root.gridMode ? songGrid : list
            if (view && view.count > 0)
                view.positionViewAtIndex(Math.min(row, view.count - 1), GridView.Beginning)
            root._pushVisibleRange()
        })
    }
    onRowHeightChanged: _pushVisibleRange()

    Component.onCompleted: {
        _syncFromModel()
        _pushVisibleRange()
    }

    // ---------------------------------------------------------------- reveal

    /* Point at rows that have just been added to the end of the list.
     *
     * A list that accumulates keeps its scroll position when rows arrive, which
     * is what makes the arrival invisible: the new rows land below the fold and
     * nothing says so. This raises them to the top and washes them in the
     * accent for a moment, so what just came in — and how much of it — is
     * legible without counting.
     *
     * Opt-in. Every other list this component draws either replaces its
     * contents outright or is reordered by the user, and neither wants this;
     * only the caller that knows an append happened calls it.
     */
    function revealAdded(firstRow, count) {
        if (!listVM || count <= 0 || firstRow < 0)
            return
        revealTimer.firstRow = firstRow
        revealTimer.count = count
        revealTimer.restart()
    }

    /* Where the list has to sit for row *firstRow* to read as the top of what
     * just arrived.
     *
     * One row of lead-in, so the last of the old rows stays on screen above the
     * first of the new ones and the seam between them reads.
     *
     * Row 0 sits at content y 0 whether or not there is a header — a header
     * lives at negative content coordinates, which is what originY records — so
     * this needs no adjusting for one, the same as `_pushVisibleRange`.
     *
     * The clamp is what handles a tail too short to lift: when there are not
     * enough rows below to raise these to the top, no scroll position puts them
     * there, so the list goes as far as it can and they stay where they are, at
     * the bottom.
     */
    function _revealTargetY(firstRow) {
        var minY = list.originY
        var maxY = list.originY + list.contentHeight - list.height
        return Math.max(minY, Math.min((firstRow - 1) * root.rowHeight,
                                       Math.max(minY, maxY)))
    }

    // The work, one frame late. Twice over: `count` is updated the instant the
    // rows are inserted but the ListView does not recompute contentHeight until
    // it next lays out, and the pairing flow raises `importPageRequested` in
    // the same breath as the insert, so the page this list is on may still be
    // fading in when the call arrives.
    Timer {
        id: revealTimer
        interval: 16
        property int firstRow: 0
        property int count: 0
        onTriggered: {
            var last = Math.min(firstRow + count, list.count) - 1
            if (last < firstRow)
                return

            // By uid, not by row: see `_flashUids`.
            var next = new Set()
            for (var row = firstRow; row <= last; row++)
                next.add(root.listVM.uidAt(row))
            root._flashUids = next

            var target = root._revealTargetY(firstRow)

            revealScroll.stop()
            if (Math.abs(target - list.contentY) > 0.5) {
                revealScroll.from = list.contentY
                revealScroll.to = target
                revealScroll.start()
            }

            flashAnim.restart()
        }
    }

    // Glides rather than jumps: the point is to show the user where the list
    // went, which an instant reposition does not. Writing contentY directly is
    // how this file scrolls (see the edge-scroll timer and the origin pin).
    NumberAnimation {
        id: revealScroll
        target: list
        property: "contentY"
        duration: Theme.slow
        easing.type: Theme.easing
    }

    // Up fast, hold, then a long fade back to nothing. No `from` on either leg:
    // a second import arriving while the first is still fading picks up from
    // wherever the level is rather than snapping to full.
    SequentialAnimation {
        id: flashAnim
        NumberAnimation {
            target: root; property: "_flashLevel"
            to: 1; duration: Theme.fast
        }
        PauseAnimation { duration: Theme.slow }
        NumberAnimation {
            target: root; property: "_flashLevel"
            to: 0; duration: 900
            easing.type: Theme.easing
        }
        onFinished: root._flashUids = new Set()
    }

    // ------------------------------------------------------------- departure

    /* Name a row that is about to leave under its own steam.
     *
     * Removal normally means the user removed something, and the list says so
     * by throwing the row off the right-hand edge. A track that simply finished
     * did not go anywhere the user sent it, so it fades where it sits instead —
     * see the `remove` transition, which is where that decision is spent.
     *
     * Must be called *before* the row goes: it is named by uid, and once it is
     * out of the model there is no row left to read one off.
     */
    function markAdvanceRemoved(uid) {
        // Reassigned rather than mutated — QML does not see a Set change under
        // it (see `clearSelection`).
        var next = new Set(_advanceUids)
        next.add(uid)
        _advanceUids = next
        advanceForget.restart()
    }

    // A uid is a per-row instance id and is never reused, so a stale entry
    // cannot match a later row — forgetting is housekeeping, not correctness.
    // The one thing the interval must do is outlast the exit it guards, so a
    // row is never forgotten while still on its way out.
    Timer {
        id: advanceForget
        interval: Theme.slow
        onTriggered: root._advanceUids = new Set()
    }

    // One corner mask for every row. Per-row masks leave a random handful of
    // rows with no artwork while scrolling — see CoverArt.qml.
    readonly property var rowArtMask: list.artMask

    // The same, but round, for artist rows. A second mask rather than a
    // parameterised one because a mask is a rendered texture, not a value: the
    // whole point of sharing is that one texture is built and handed out, so a
    // radius that varied per row would mean a texture per row and the blank-art
    // problem back again.
    CornerMask {
        id: rowPortraitMask
        width: root.artworkSize; height: root.artworkSize
        radius: 21
    }

    // ----------------------------------------------------------------- list

    // In sticky mode the caller's header is wrapped so that a band the height
    // of the column header is reserved *inside* the scrolling content. The real
    // header then floats over that band, so it reads as part of the overview
    // until it pins.
    Component {
        id: _headerShell
        Column {
            width: list.width
            Loader {
                width: list.width
                sourceComponent: root.listHeader
            }
            Item {
                width: 1
                height: root.columnHeaderHeight
            }
        }
    }

    LibraryList {
        id: list
        // Named so a test can find the row list itself rather than one of the
        // menus, which are ListViews too.
        objectName: "trackList"
        visible: !root.gridMode
        anchors.fill: parent
        // Docked mode reserves the header's height here instead; sticky mode
        // reserves it inside the header component above.
        anchors.topMargin: root._sticky ? 0 : root.columnHeaderHeight
        rowHeight: root.rowHeight
        artSize: root.artworkSize
        artworkRadius: 7

        // The column header collapses when the last row goes — and `count`
        // reaches zero while that row is still on its way out, which would
        // re-anchor the list from under it mid-glide. Eased, it travels with it.
        Behavior on anchors.topMargin {
            NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
        }
        model: root.listVM
        header: root._sticky ? _headerShell : null
        /* Rows are pooled by LibraryList rather than rebuilt.
         *
         * This delegate is expensive — on the order of fifty QObjects, among
         * them a MultiEffect, which is an offscreen render pass of its own, and
         * a Repeater building a cell per visible column. Without pooling every
         * row that crosses the viewport edge is constructed from nothing and
         * the one leaving is destroyed, which is affordable at a wheel notch a
         * time and is not affordable when a scrollbar drag sweeps the whole
         * list past in a second.
         *
         * The cost of pooling is that a row's state is no longer guaranteed
         * fresh: anything written to a delegate imperatively, rather than
         * bound, survives into whichever row next takes that object. The exit
         * transition above writes three such things. See `ListView.onReused`.
         */
        // Widened while a row is being dragged. The dragged row's *slot* stays
        // where it was in content coordinates while the edge auto-scroll runs,
        // so a long drag can carry it outside the ordinary buffer — and if the
        // view recycles the delegate holding the mouse grab, the gesture dies
        // mid-air. Two screens either side covers any reasonable drag; the
        // ListView.onPooled below catches the rest.
        //
        // Twelve rows rather than six now that rows are pooled: the buffer's
        // cost used to be paid in construction, so it was kept tight, and with
        // pooling it is a handful of already-built objects held in reserve.
        cacheBuffer: root._dragging ? Math.round(root.rowHeight * 12 + height * 2)
                                    : root.rowHeight * 12

        // A Flickable must not spend the gesture on scrolling while a row is
        // being dragged out of it.
        interactive: !root._dragging

        /* A removed row goes, and the list closes up behind it.
         *
         * Taking a row out of a list of near-identical rows is otherwise silent
         * — the row is simply not there any more, and on a long list it is easy
         * to lose which one went, or how many.
         *
         * Every departing row fades. What differs is whether it also travels,
         * and that is the difference between being got rid of and being over. A
         * row the user removed is thrown off the right-hand edge: `list.width`
         * clears it exactly, because the delegate is exactly that wide, and
         * `clip` cuts it off on the way rather than letting it draw over the
         * rest of the page. A track that played through was sent nowhere, so it
         * has no direction to leave in — it stays put and only fades.
         *
         * A row pulled off the edge by its grip needs nothing of its own here.
         * It is a removal like the context menu's and leaves through this same
         * animation — it simply starts further along, because the delegate is
         * still holding the offset the hand let go at. See `_dismissedShift`.
         *
         * `ViewTransition.item` is this row — the view instantiates the
         * transition once per departing row, so the test is per row and not per
         * pass.
         */
        remove: Transition {
            id: rowExit

            // Read here on the transition rather than down on an animation: the
            // view fills the ViewTransition values in immediately before it
            // builds each row's animations, so a binding at this level is
            // per-row and is read at the one moment it is right. `rowUid` and
            // not `uid`, because by now the row is out of the model and the
            // role behind `uid` has nothing left to answer with.
            readonly property bool underOwnSteam: {
                var it = rowExit.ViewTransition.item
                return !!it && root._advanceUids.has(it.rowUid)
            }

            // Over the rows closing up beneath it. A row fading in place is
            // overlapped for the whole transition by the one rising into its
            // slot, and underneath that it reads as being swallowed rather than
            // as fading. Clobbering the delegate's own `z` costs nothing: that
            // binding is on `inDragBlock`, which is reading an index of -1 by
            // now, and the row is on its way to being destroyed. 3 clears the
            // 2 a dragged row floats at.
            PropertyAction { property: "z"; value: 3 }

            ParallelAnimation {
                NumberAnimation {
                    property: "x"
                    to: rowExit.underOwnSteam ? 0 : list.width
                    // Zero is the view's way of saying "do not move this".
                    duration: rowExit.underOwnSteam ? 0 : Theme.normal
                    // Away, not into place. Theme.easing decelerates, which
                    // reads as arriving somewhere; leaving accelerates.
                    easing.type: Easing.InCubic
                }
                // Spent before a travelling row reaches the edge, so the clip
                // never has to cut a solid card in half — and the whole of the
                // exit for one that stays.
                NumberAnimation {
                    property: "opacity"
                    to: 0
                    duration: Theme.normal
                    easing.type: Theme.easing
                }
            }
        }

        // The rows below glide up together into the space, in unison with the
        // fade above. `removeDisplaced` rather than the blanket `displaced`: a
        // reorder displaces rows too, and that one is already animated by hand —
        // the delegate's Translate and the `_committing` window around
        // `moveRows` exist precisely so nothing animates that move twice. This
        // fires on removal only, so the drag machinery keeps its own timing.
        removeDisplaced: Transition {
            NumberAnimation {
                properties: "x,y"
                duration: Theme.normal
                easing.type: Theme.easing
            }
        }

        /* A row that arrives fades in where it lands, and the rows below it
         * glide down to make room.
         *
         * The other half of the departure above, and just as silent without it:
         * the list is one row longer and nothing said so, or where. Opacity
         * only — the row was inserted at a position, and the position is the
         * information, so travelling in from an edge would only argue with it.
         *
         * This is what makes Previous legible on the queue tab, and it serves
         * Play next, Add to queue, and a track reaching the top of History. A
         * list that replaces its contents outright — search, library, playlists,
         * all of which go through `set_ids` — resets rather than inserts, and Qt
         * routes a reset to `populate`, so none of them fade a page of rows in.
         */
        add: Transition {
            NumberAnimation {
                property: "opacity"
                from: 0
                to: 1
                duration: Theme.normal
                easing.type: Theme.easing
            }
        }

        // `addDisplaced`, never `displaced` — the same reason as above. An
        // append displaces nothing, so the import list pays for none of this.
        addDisplaced: Transition {
            NumberAnimation {
                properties: "x,y"
                duration: Theme.normal
                easing.type: Theme.easing
            }
        }

        /* A row travels to the front, and the rows it passes close up behind it.
         *
         * Playing a track from partway down the queue is the case this is for.
         * It used to reach the view as a reset — every row rebuilt, and no way
         * to tell that the track now at the top is the one that was at row 7.
         * Watching it travel is the whole of the explanation.
         *
         * Whatever was playing goes at the same time: fading out of row 0 if it
         * was listened to and has gone to history, or gliding down a slot if it
         * was not, in which case it never left and there is nothing to fade.
         * Both are handled by the transitions above — this pair only has to
         * carry the rows that are staying.
         *
         * Zero while `_committing`, which is a drag being handed over to the
         * model. The block is already sitting where it is going, put there by
         * the delegate's own Translate offsets; animating the model's version of
         * the same move would take it back to where it started and re-fly it.
         * See the `commitSilence` Timer for why that flag has to outlast the
         * commit by a few frames.
         */
        move: Transition {
            NumberAnimation {
                properties: "x,y"
                duration: root._committing ? 0 : Theme.normal
                easing.type: Theme.easing
            }
        }

        moveDisplaced: Transition {
            NumberAnimation {
                properties: "x,y"
                duration: root._committing ? 0 : Theme.normal
                easing.type: Theme.easing
            }
        }

        // Clicking a row while the list is still moving picks it, rather than
        // being spent stopping the list.
        //
        // A Flickable with speed on filters the next press away from its
        // children and keeps it — the touch gesture for halting a flick. On a
        // mouse that just loses the click you meant, and after a wheel scroll,
        // which leaves the view coasting, that is most of them. A press delay
        // makes the Flickable hold the press instead of eating it: if no drag
        // follows, it replays it to the row on release, so the click lands
        // after all. `preventStealing` on the row does not help — the capture
        // happens before that is consulted.
        //
        // Only while the list is moving, and only a millisecond of it. A press
        // delay that is always on costs double click: the replayed press
        // arrives after its own release, and the double click that should
        // follow never reaches the row, so double clicking a track stops
        // playing it. A still list does not need the delay — nothing is
        // filtering presses away from the rows — so it does not pay for one.
        pressDelay: moving ? 1 : 0
        boundsBehavior: Flickable.StopAtBounds
        currentIndex: -1
        highlightFollowsCurrentItem: false

        onContentYChanged: root._pushVisibleRange()
        onHeightChanged: root._pushVisibleRange()

        // A hand on the list outranks the reveal. `dragging` is true only for a
        // real grab — a programmatic scroll moves the view without it — so this
        // cannot cancel the animation on its own movement. The flash is left to
        // run: it is what says which rows are new, and the user taking over the
        // scrolling does not make that less worth saying.
        onDraggingChanged: {
            if (dragging)
                revealScroll.stop()
        }
        onCountChanged: {
            // The queue can move on under a drag — a track ends, next() fires,
            // and every index the gesture is holding means something else.
            // There is nothing sensible to salvage, so abandon it.
            root._endDrag(false)
            root._pushVisibleRange()
        }

        // Keep the top the top while the header settles.
        //
        // A header lives at negative content coordinates, so originY is exactly
        // minus its height — and the Library's overview does not reach its full
        // height until its own children have laid out, well after the view has
        // settled at the top of a header that was still empty. The view holds
        // contentY through that, which leaves it a whole overview below the top
        // with the column header clipping the first row.
        //
        // Sitting at the old originY is what "at the top" means, so a view that
        // was follows the change. One the user has scrolled is somewhere else
        // by definition and is left where they put it.
        property real _lastOriginY: 0
        onOriginYChanged: {
            if (Math.abs(contentY - _lastOriginY) < 0.5)
                contentY = originY
            _lastOriginY = originY
        }

        ScrollBar.vertical: ScrollBar {
            id: bar
            policy: ScrollBar.AsNeeded
            width: 9
            anchors.right: parent.right
            anchors.rightMargin: 2
            contentItem: Rectangle {
                implicitWidth: 5
                radius: 3
                color: bar.pressed ? Theme.alpha(Theme.text, 0.5)
                     : (bar.hovered ? Theme.alpha(Theme.text, 0.34)
                                    : Theme.alpha(Theme.text, 0.18))
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
            background: Item {}
        }

        // Clicking anywhere in the list that is not a row drops the selection:
        // below the last row, above the first, and the strip either side of the
        // cards, none of which has anything in it to hit.
        //
        // It has to sit inside contentItem, and it has to say so: a child
        // declared here lands beside contentItem rather than in it, and an item
        // with a negative z is offered the press *after* its own parent — so out
        // there the Flickable, which accepts every press it is given, would take
        // the click first and nothing below the rows would ever deselect. In
        // here the parent is a plain Item that accepts nothing, so the press
        // falls past the rows to this.
        //
        // Which means content coordinates: pinning it to contentY keeps it over
        // the viewport whatever the rows are doing underneath.
        MouseArea {
            parent: list.contentItem
            x: 0
            y: list.contentY
            width: list.width
            height: list.height
            z: -1
            acceptedButtons: Qt.LeftButton
            onClicked: root.clearSelection()
        }

        delegate: root.gridMode ? null : trackRowDelegate

        property Component trackRowDelegate: Item {
            id: rowItem

            required property int index
            required property var uid
            required property var trackId
            required property var title
            required property var artist
            required property var album
            required property var duration
            required property var coverHash
            required property var coverUrl
            // Where this row's own id points, worked out by the backend from
            // whichever enabled plugin declares it — "" when nothing does.
            required property var externalUrl
            // What that id is called, for the SOURCE column of a download row.
            required property var sourceLabel
            // `notFound` is deliberately absent. It is the raw fact — this row
            // matched nothing — and the row no longer draws from raw facts: two
            // states share it and need opposite treatment. `importState` is the
            // backend's reading of it, and reading it twice is what this change
            // was about. The role is still served for any GUI that wants it.
            required property var isDownload
            required property var importState
            required property var sourceFormat

            // What kind of thing this row is. Only search in a mixed mode ever
            // answers anything but "track", and every other list leaves it null
            // — which `isArtist` and `isAlbum` both read as false, so nothing
            // else changes.
            //
            // Branched on rather than inferred from a missing trackId: a web
            // search result has no trackId either, and it is emphatically a track.
            //
            // This is the *only* artist or album field declared here. The others
            // are read from `model` inside ArtistRow and AlbumRow, which exist
            // only on rows that are one. The distinction is not tidiness: the
            // model is Python, so every required property is a call across that
            // boundary for every row the view paints, and eight of them would be
            // paid by every queue, playlist and library row forever to serve a
            // list shape none of them can have.
            required property var rowKind

            readonly property bool isArtist: String(rowKind || "") === "artist"
            readonly property bool isAlbum: String(rowKind || "") === "album"
            // Neither: the ordinary case, and what the columns draw for.
            readonly property bool isTrackRow: !isArtist && !isAlbum

            // The whole row, for cells whose field is only known at runtime.
            // Reading model[key] this way still registers demand with the
            // backend and still updates when the fetch lands, so a column
            // switched on in the menu fills itself in.
            required property var model

            width: ListView.view.width
            height: root.rowHeight

            /* What this row was, kept for as long as it is still on screen.
             *
             * The view drops a removed row's `index` to -1 and then holds the
             * row alive for the length of its exit transition — so for those few
             * frames the row is still being drawn while everything it knows
             * about itself has gone wrong at once. Its `index` names nothing,
             * and its role-backed properties are reading a model row that is no
             * longer there. Left alone, a row would renumber itself to 0 and
             * shed its playing accent on the way out.
             *
             * `rowIndex` is the last index it really held. `rowUid` is the same
             * for its uid, and is a plain int taken at birth rather than the
             * role — the exit transition has to ask which row this is *after*
             * the model has let go of it, which is exactly when the role has
             * stopped answering.
             */
            property int _lastIndex: -1
            property int rowUid: -1
            onIndexChanged: if (index >= 0) _lastIndex = index
            onUidChanged: if (uid !== undefined && uid !== null) rowUid = Number(uid)

            // Take on the identity of whatever row this object is now serving.
            // Called from both ends of the delegate's life because a pooled row
            // has two: it is completed once and reused any number of times, and
            // only the first of those fires Component.onCompleted.
            function _adopt() {
                _lastIndex = index
                rowUid = Number(uid)
            }

            Component.onCompleted: _adopt()

            /* The row is being handed a different track, so nothing about it
             * should animate.
             *
             * Every binding that reads the row's identity re-evaluates at once
             * when a pooled delegate is reused, and a Behavior turns each of
             * those into an animation *from the value the previous track left*.
             * A selected row's accent drains out of an unselected one; the
             * playing row's title colour bleeds off a track that is not
             * playing. Once per recycled row, which during the fast scroll this
             * pooling exists to make possible is every row on screen.
             *
             * Held for one turn of the event loop, which is long enough for the
             * new values to land as facts rather than as transitions.
             * `Qt.callLater` coalesces on the function it is given, and this one
             * is the same object every time for a given row, so a reuse storm
             * still queues at most one call per delegate.
             */
            property bool _settling: false
            function _endSettle() { _settling = false }
            readonly property bool leaving: index < 0
            readonly property int rowIndex: index >= 0 ? index : _lastIndex

            // A row on its way out must not take a click. Its uid is already out
            // of the model and its index is gone, so every handler on it would
            // act either on nothing or on whichever row inherited the slot.
            // `enabled` is input only — it costs the departing row no paint.
            enabled: !leaving

            // This row is one of the ones currently being dragged, so it floats
            // over its neighbours instead of under them.
            readonly property bool inDragBlock: root._dragRows.indexOf(index) >= 0
            z: inDragBlock ? 2 : 0

            // This row has been thrown off the edge and is riding its exit. It
            // is out of the model, so `index` names nothing and the live offset
            // has let go of it — the one it was thrown from is held by uid
            // instead, and the exit animates on top of it.
            readonly property bool dismissed:
                root._dismissedShift.hasOwnProperty(rowUid)

            // How far off to the right the card is. On `rowCard` rather than on
            // the Translate below, because the bed it is pulled off has to stay
            // where it is while it goes.
            readonly property real cardShift:
                dismissed ? root._dismissedShift[rowUid]
                          : root._cardShiftFor(index)

            // The reorder offset. A Translate rather than a y: a vertical
            // ListView positions its delegates itself and would overwrite one —
            // and this has to carry the playing row's accent bar along with the
            // card, which a shift applied to either alone would not.
            transform: Translate {
                y: root._shiftFor(rowItem.index)
                Behavior on y {
                    // The anchor row is the one under the cursor and must not
                    // lag it, so it is exempt until the pointer lets go.
                    // Everything else glides — including all of them on the way
                    // back from an abandoned drag, which is what the !_dragging
                    // term is for. Nothing animates during the commit frame, and
                    // nothing animates on the frame a pooled row is handed a new
                    // index: its shift changes to that row's, which is a fact
                    // about where it now is rather than a move it just made.
                    enabled: !root._committing
                             && !rowItem._settling
                             && (!root._dragging || root._releasing
                                 || rowItem.index !== root._dragAnchor)
                    NumberAnimation {
                        duration: Theme.normal
                        easing.type: Theme.easing
                    }
                }
            }

            // If the view recycles the row the gesture is holding, the drag has
            // lost its grip; leaving it live would leave the list frozen with
            // interactive false and nothing able to release it. Harmless during
            // a commit — that clears the drag before the model moves anything.
            //
            // Both endings, because a pooled row has two and they are not
            // interchangeable: a recycled row is never destroyed, so with
            // `reuseItems` on, Component.onDestruction alone would mean this
            // check quietly stopped running in the exact case it names.
            function _releaseGrip() {
                if (root._dragging && root._dragAnchor === rowItem.index)
                    root._endDrag(false)
            }

            Component.onDestruction: _releaseGrip()
            ListView.onPooled: _releaseGrip()

            /* Undo what the exit transition wrote, before this object is some
             * other row.
             *
             * A pooled delegate keeps every property that was assigned to it
             * rather than bound, and `remove` assigns three: opacity to 0, x to
             * `list.width` for a row thrown off the edge, and z to 3. Left as
             * they are, the next row to be handed this object arrives invisible,
             * or parked off the right-hand edge, or drawn over all its
             * neighbours — and permanently, because nothing later puts them
             * back.
             *
             * `z` is the one that needs care. It is set by a PropertyAction,
             * which does not merely change the value but *destroys the binding*
             * underneath it, so assigning 0 here would fix this row and leave
             * the next drag unable to raise it. It has to be given the binding
             * back, not a number.
             */
            ListView.onReused: {
                _settling = true
                opacity = 1
                x = 0
                z = Qt.binding(function () { return rowItem.inDragBlock ? 2 : 0 })
                gripMouse.armed = false
                gripMouse.pressY = 0
                gripMouse.pressX = 0
                _adopt()
                Qt.callLater(_endSettle)
            }

            readonly property int selectionShape: root._selectionShapes[uid] || 0
            readonly property bool selected: selectionShape !== 0
            // This row is one of the ones that just arrived — see `revealAdded`.
            readonly property bool justAdded: root._flashUids.has(uid)
            readonly property bool isPlaying: playerVM.has_current_track
                && (root.mode === "queue"
                    ? rowIndex === 0
                    : (Number(trackId) > 0 && Number(trackId) === playerVM.current_track_id))
            readonly property bool inLibrary: Number(trackId) > 0
            readonly property string titleText: String(title || "Unknown title")
            readonly property string artistText: String(artist || "")

            /* An import candidate that wants the user's attention, and can
             * receive it.
             *
             * Marked, not disabled. `tagsOnly` will import, it will just arrive
             * with the title and artist read off its own tags and none of the
             * album, artwork or release date a match would have brought.
             * `needsMetadata` will not import at all until it is paired with a
             * search result. Both are worth noticing before pressing Import
             * all, and invisible otherwise — the rows look exactly like matched
             * ones.
             *
             * Deliberately not every unmatched row. `unavailable` matched
             * nothing either, but there is nothing the user can do with it
             * except take it out of the list, and amber that cannot be answered
             * is a demand for attention rather than a use of it. Those fade
             * instead — see `stateOpacity`.
             *
             * `importState` is only ever served by the import model, so this
             * needs no mode check of its own; every other list leaves it
             * undefined.
             */
            readonly property bool needsAttention:
                importState === "tagsOnly" || importState === "needsMetadata"

            /* Rows with nothing left to ask of the user recede.
             *
             * Both ends of the list: `inLibrary` is finished with, `unavailable`
             * can never start. Neither should compete with the rows the Import
             * button is actually about. The same idiom the backing-track
             * workbench uses for its finished jobs. */
            readonly property real stateOpacity:
                  importState === "unavailable" ? 0.5
                : importState === "inLibrary" ? 0.65
                : 1.0

            // What clicking the artwork does, which is also which glyph the
            // hover overlay shows. A library row plays; a row that is not in
            // the library but has a link — most online results, and an import
            // candidate a plugin identified — opens it instead; a row that is
            // neither, like an unmatched import candidate, offers nothing at
            // all rather than a button that would silently fail.
            //
            // The playing row is excluded because its overlay would sit over
            // the artwork of the track already running.
            readonly property string artAction:
                  isPlaying ? ""
                : inLibrary ? "play"
                : (String(externalUrl || "") !== "" ? "external" : "")

            function triggerArtAction() {
                if (artAction === "play")
                    root.playRow(rowItem.index, Number(rowItem.trackId) || -1)
                else if (artAction === "external")
                    Qt.openUrlExternally(String(rowItem.externalUrl))
            }

            // On a list with no number column the playing row grows one, and
            // artwork and title shift right to make room. The title column
            // gives back exactly what the slot takes, so every column to its
            // right lands on the same x as on every other row — and the header,
            // which never shifts, stays aligned with all of them.
            readonly property int meterShift:
                (!root.showIndex && isPlaying) ? (Catalog.NUM_W + Theme.gap) : 0

            // Render a cell's value. This lives here rather than in the catalog
            // because a .pragma library file cannot see the Fmt singleton.
            function cellText(col) {
                var v = rowItem.model[col.field];
                if (col.renderer === "duration")
                    return Number(v) > 0 ? Fmt.duration(v) : "--:--";
                // Where this row's audio comes from, and what it costs to take
                // it in. The backend hands over the bare container format and
                // nothing else — the catalog holds no presentation — so the
                // words and the arrow are decided here.
                //
                // The arrow is the point: a row that will be transcoded is the
                // one thing in this list the user cannot otherwise find out
                // before pressing Import, and it is now the only cell in the
                // column with two names in it.
                if (col.renderer === "source") {
                    // A row with nothing to fetch has no source to name.
                    // Naming one for a track nothing can fetch would be the
                    // one cell on that row still claiming it will work.
                    if (rowItem.importState === "unavailable")
                        return "";
                    if (rowItem.isDownload === true)
                        return String(rowItem.sourceLabel || "Online");
                    if (v === undefined || v === null || v === "")
                        return "";
                    return String(v) === "opus"
                           ? "Opus"
                           : String(v).toUpperCase() + " → Opus";
                }
                if (v === undefined || v === null || v === "")
                    return "";
                if (col.renderer === "date")
                    return Fmt.releaseDate(v);
                return String(v);
            }

            // Declared here rather than in the cell so it is compiled once per
            // row instead of once per column, and so its bindings can still see
            // `rowItem`.
            Component {
                id: statusCell
                TrackStatusCell {
                    statusMode: root.statusMode
                    trackId: rowItem.trackId
                    uid: rowItem.uid
                    // The backend's own classification of this row, which is the
                    // same one the saving code acts on. Handed straight through
                    // rather than worked out here: what the cell offers and what
                    // Import all will do are one rule, and two copies of a rule
                    // is one copy too many.
                    importState: String(rowItem.importState || "")
                    sourceFormat: String(rowItem.sourceFormat || "")
                }
            }

            function openMenu() {
                root.openTrackMenu(rowItem.uid, Number(rowItem.trackId) || -1,
                                   rowItem.index, rowItem.titleText)
            }

            /* The bed the row is pulled off, in the hole it left.
             *
             * Only the strip the card has uncovered is drawn. An unselected
             * row's backdrop is `transparent`, so a full-width panel sitting
             * behind it would show straight through and turn the row red from
             * the first pixel of a pull — including one that was only ever a
             * reorder with a little sideways wobble in it.
             *
             * Which means the reveal is the whole of the staging, and it comes
             * out in the right order for free: the trash clears the clip while
             * the pull is still being resisted, and the word finishes at the
             * moment the card snaps across. So nothing here fades.
             *
             * A sibling of `rowCard` rather than a child, because it must not
             * take the horizontal offset. It does take the delegate's vertical
             * one, which is right — it belongs to the block and follows it up
             * and down the list.
             */
            Item {
                id: dismissBed
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.leftMargin: root.sideMargin - 8
                width: Math.min(rowItem.cardShift, rowCard.width)
                visible: width > 0.5 && opacity > 0
                clip: true

                // Gone the moment the hand lets go. The bed's whole job is
                // saying what a release would do, and once the release has
                // happened it is answering a question nobody is asking — while
                // sitting in the slot the row below is gliding up into. Quicker
                // than the row's own exit, so it clears rather than smears.
                opacity: rowItem.dismissed ? 0 : 1
                Behavior on opacity {
                    enabled: !rowItem._settling
                    NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
                }

                Rectangle {
                    width: rowCard.width
                    height: parent.height
                    radius: Theme.radius
                    color: Theme.dangerBed

                    Row {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.gap
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.gapSm

                        Icon {
                            anchors.verticalCenter: parent.verticalCenter
                            name: "trash"
                            size: 18
                            // Heavier than the 1.7 every other icon draws at.
                            // This one is read at a glance, mid-gesture, over a
                            // filled panel rather than the page — a hairline
                            // that is right in a toolbar goes thin there.
                            strokeWidth: 2.2
                            color: Theme.dangerBedInk
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "REMOVE"
                            color: Theme.dangerBedInk
                            font.family: Theme.fontFamily
                            // Bigger and heavier than the small-caps labels this
                            // used to borrow from. Those name a section you are
                            // reading past; this one has to land while the strip
                            // it is written on is still growing.
                            font.pixelSize: Theme.fzBody
                            font.weight: Font.Bold
                            font.letterSpacing: 1.0
                        }
                    }
                }
            }

            // Everything the row paints, and everything it answers to. The
            // delegate spans the full width of the list so rows tile without
            // gaps, but only this inset card is drawn — so hover, clicks and
            // the context menu are scoped to it, and the strip either side is
            // background like anywhere else: clicking it falls through to the
            // list, which deselects.
            //
            // The inset lives here rather than on the delegate because a
            // vertical ListView positions its delegates itself and would
            // overwrite their x.
            Item {
                id: rowCard
                anchors.fill: parent
                anchors.leftMargin: root.sideMargin - 8
                anchors.rightMargin: root.sideMargin - 8

                // The dismiss offset, on the card alone so the bed behind it
                // stays put and is uncovered rather than carried along.
                //
                // Deliberately without a Behavior. Every value this takes is
                // already either the pointer's or an animation's: the pull
                // follows the hand and must not lag it, the spring home is
                // `springBack`, and the throw is the exit transition animating
                // the delegate's own `x` on top of a held offset. A Behavior
                // here would have nothing left to smooth and one more thing to
                // race — see `_springX`.
                transform: Translate { x: rowItem.cardShift }

                // Hover is tracked by a handler on the card itself, not by rowMouse:
                // it has to stay true while the pointer is over one of the row's
                // buttons, and a sibling MouseArea would report a leave there.
                HoverHandler { id: rowHover }

                // ------------------------------------------------------ backdrop

                Rectangle {
                    anchors.fill: parent
                    // Interior rows are plain rectangles, including Qt's
                    // automatic antialiasing choice: no rounded geometry.
                    radius: rowItem.selectionShape === 7 ? 0 : Theme.radius
                    topLeftRadius: (rowItem.selectionShape & 2) ? 0 : radius
                    topRightRadius: topLeftRadius
                    bottomLeftRadius: (rowItem.selectionShape & 4) ? 0 : radius
                    bottomRightRadius: bottomLeftRadius
                    // Selection still wins: a marked row has to be selectable,
                    // and a tint the selection could not overpower would make it
                    // impossible to see what is picked. Below that the amber sits
                    // where "transparent" would, and brightens on hover the same
                    // way an ordinary row does.
                    color: rowItem.selected ? Theme.accentSoft
                         : rowItem.needsAttention
                           ? Theme.alpha(Theme.warning, rowHover.hovered ? 0.22 : 0.14)
                         : (rowHover.hovered ? Theme.alpha(Theme.text, 0.045)
                                             : "transparent")
                    // Unselected marked rows carry a hairline. Against the ambient
                    // backdrop a low-alpha wash can wash out on a bright frame;
                    // an edge does not, and it is what makes the band read as a
                    // deliberate marking rather than a gradient artefact.
                    border.width: !rowItem.selected && rowItem.needsAttention ? 1 : 0
                    border.color: Theme.alpha(Theme.warning, 0.38)

                    Behavior on color {
                        enabled: !rowItem._settling
                        ColorAnimation { duration: Theme.fast }
                    }
                }

                // The flash on a row that just arrived. Over the backdrop
                // rather than part of it, so hover, selection and the amber
                // marking carry on underneath and are simply revealed again as
                // this fades — a row selected before the import is still
                // selected, and looks it, the moment the wash is gone.
                //
                // A bare wash at nearly three times the selection's strength,
                // which reads as a different thing — and it is gone inside
                // two seconds either way.
                Rectangle {
                    anchors.fill: parent
                    radius: rowItem.selectionShape === 7 ? 0 : Theme.radius
                    topLeftRadius: (rowItem.selectionShape & 2) ? 0 : radius
                    topRightRadius: topLeftRadius
                    bottomLeftRadius: (rowItem.selectionShape & 4) ? 0 : radius
                    bottomRightRadius: bottomLeftRadius
                    color: Theme.alpha(Theme.accent, 0.28)
                    opacity: rowItem.justAdded ? root._flashLevel : 0
                    // Out of the scene graph when there is nothing to draw:
                    // every list in the app builds these, and only the import
                    // list ever flashes one. No Behavior either — `flashAnim`
                    // drives the level, and a Behavior on top would fight it.
                    visible: opacity > 0
                }

                // An artist is not a track and has no columns worth drawing: no
                // album, no duration, no key. Rather than blanking a dozen cells
                // it gets its own line — a round portrait and a name — which is
                // also what makes it recognisable as a different kind of result
                // at a glance while scrolling past.
                //
                // Overlaid on the same card as the track content and swapped for
                // it, so hover, selection and the context menu all keep working
                // without knowing which kind of row they are on.
                //
                // Behind a Loader rather than merely hidden: every list except a
                // mixed search is tracks throughout, and an invisible item still
                // costs a creation and a layout pass in every one of thousands of
                // delegates. Inactive, this is a few bytes.
                Loader {
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    active: rowItem.isArtist
                    visible: active
                    opacity: rowItem.stateOpacity

                    // Declared here rather than once at the top of the file,
                    // even though that would build one Component instead of one
                    // per delegate. A Component resolves ids in the scope it is
                    // *written* in, not the scope it is loaded into, so one
                    // declared beside the mask would find no `rowItem` and every
                    // artist row would come out blank.
                    sourceComponent: ArtistRow {
                        // Read off `model` rather than through required
                        // properties on the delegate, so these four roles are
                        // fetched only for rows that are actually artists.
                        artName: String(rowItem.model.artistName || "")
                        imageHash: String(rowItem.model.artistImageHash || "")
                        trackCount: Number(rowItem.model.artistTrackCount || 0)
                        artMask: rowPortraitMask
                    }
                }

                // The album face, on the same terms as the artist one above —
                // inactive in every list that is not an album search, and its
                // Component declared here so it can see `rowItem`.
                Loader {
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    active: rowItem.isAlbum
                    visible: active
                    opacity: rowItem.stateOpacity

                    sourceComponent: AlbumRow {
                        albumName: String(rowItem.model.albumName || "")
                        coverHash: String(rowItem.model.albumCoverHash || "")
                        trackCount: Number(rowItem.model.albumTrackCount || 0)
                        isComplete: Boolean(rowItem.model.albumIsComplete)
                        // The square mask, the one every cover already shares.
                        artMask: root.rowArtMask
                    }
                }

                // The 8px the card was inset by, given back — so the columns sit at
                // `sideMargin` from the edge of the list, where the header puts its
                // own cells.
                Row {
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    spacing: Theme.gap
                    visible: rowItem.isTrackRow

                    // The content fades, not the card: a receded row still has
                    // to show clearly when it is selected, and a selection wash
                    // dimmed along with everything on top of it would make
                    // picking out the dead rows to remove them harder than
                    // picking out any others.
                    opacity: rowItem.stateOpacity
                    Behavior on opacity {
                        enabled: !rowItem._settling
                        NumberAnimation { duration: Theme.normal }
                    }

                    // ------------------------------------------- index / meter
                    //
                    // A Row skips invisible children entirely, adding no spacing
                    // for them — which is what lets this slot vanish on lists with
                    // no numbers and reappear, gap and all, on the playing row.
                    Item {
                        id: numberSlot
                        width: root.showIndex || rowItem.isPlaying ? Catalog.NUM_W : 0
                        height: parent.height
                        visible: width > 0

                        // On a reorderable list the number doubles as the grab
                        // handle, and hovering the row fades it into a grip —
                        // the row, not the 26px slot, so the handle announces
                        // itself rather than waiting to be found.
                        //
                        // A row that cannot be landed on cannot be picked up
                        // either, which on the queue is slot 0: it is what is
                        // playing and it is staying there, so it keeps its meter
                        // and offers nothing to pull on. `_minDropRow` is where
                        // that rule lives, and it is a queue rule — on a
                        // playlist it is 0 and every row has a handle.
                        readonly property bool canDrag:
                            root.reorderable && rowItem.rowIndex >= root._minDropRow
                        readonly property bool gripShown:
                            canDrag && (rowHover.hovered || rowItem.inDragBlock)

                        /* With the artwork gone, its button moves here.
                         *
                         * The artwork is what a row is played from — hovering it
                         * raises a scrim and a glyph, and clicking it runs
                         * `triggerArtAction`. A list that hides the artwork would
                         * otherwise have no visible way to play a row at all, so
                         * the slot that was showing a number shows the action
                         * instead, and hands the click to the very same function.
                         *
                         * `artAction` is reused rather than re-derived, so the
                         * two placements can never disagree about what a row
                         * offers: it already excludes the playing row and already
                         * knows the difference between a row in the library and a
                         * web result that can only be opened where it came from.
                         *
                         * The grip wins when both could show. Reordering is a mode
                         * the user turned on deliberately, and a handle that a
                         * play button could displace would be a handle that moves
                         * under the hand reaching for it.
                         */
                        readonly property string playAction:
                            root.showArt ? "" : rowItem.artAction
                        readonly property bool playShown:
                            playAction !== "" && !gripShown && rowHover.hovered

                        Text {
                            anchors.centerIn: parent
                            visible: !rowItem.isPlaying && root.showIndex
                            opacity: numberSlot.gripShown || numberSlot.playShown ? 0 : 1
                            text: rowItem.rowIndex + 1
                            color: rowItem.selected ? Theme.text : Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                            font.features: ({ "tnum": 1 })
                            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                        }

                        Icon {
                            anchors.centerIn: parent
                            name: "grip"
                            size: 16
                            color: rowItem.inDragBlock ? Theme.accent
                                 : rowItem.selected ? Theme.text : Theme.dim
                            opacity: numberSlot.gripShown ? 1 : 0
                            visible: opacity > 0
                            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                        }

                        // The artwork's hover glyph, in the number's place. Same
                        // two names the overlay picks between, so a row reads the
                        // same either way round.
                        Icon {
                            anchors.centerIn: parent
                            name: numberSlot.playAction === "external" ? "external"
                                                                      : "play"
                            size: 16
                            color: rowItem.selected ? Theme.text : Theme.dim
                            opacity: numberSlot.playShown ? 1 : 0
                            visible: opacity > 0
                            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                        }

                        EqualizerBars {
                            anchors.centerIn: parent
                            // Yields to the grip rather than drawing under it.
                            // Only a playlist ever asks: on the queue the
                            // playing row has no handle, so `gripShown` is
                            // false there and the meter is never displaced.
                            visible: rowItem.isPlaying && !numberSlot.gripShown
                            playing: playerVM.is_playing
                            color: Theme.accent
                            maxHeight: 13
                        }

                        /* The play button's click, and only when there is one.
                         *
                         * Declared *before* gripMouse so it sits underneath it,
                         * which is the only order that works: the two are never
                         * live together — `playAction` is empty whenever the
                         * artwork is drawing its own button, and `canDrag`
                         * excludes it besides — but a disabled MouseArea still
                         * refuses events without swallowing them, so a press on a
                         * disabled gripMouse falls straight through to here.
                         *
                         * It carries no `cursorShape` on purpose. Qt takes the
                         * cursor from the topmost item under the pointer that
                         * declares one and does not skip disabled items, so an
                         * area on top of gripMouse that named any shape at all
                         * would speak over the handle's open hand. gripMouse names
                         * the shape for the whole slot instead — see below.
                         *
                         * Covers the 26px slot and nothing more, so the rest of
                         * the row still falls through to rowMouse and still
                         * selects. Pressing play is not a selection, which is the
                         * same bargain the artwork button strikes.
                         */
                        MouseArea {
                            anchors.fill: parent
                            enabled: numberSlot.playAction !== "" && !numberSlot.canDrag
                            acceptedButtons: Qt.LeftButton
                            onClicked: rowItem.triggerArtAction()
                        }

                        // The grab handle. It sits above the content Row while
                        // rowMouse is at z: -1, so a press here never reaches
                        // handleClick — grabbing a row neither selects it nor
                        // arms the deselect timer, which is exactly what lets a
                        // drag carry a selection without disturbing it.
                        MouseArea {
                            id: gripMouse
                            anchors.fill: parent
                            // Not while a release glide is still running:
                            // grabbing a second row inside those few frames
                            // would take the drag state over, and the move or
                            // the removal it was finishing would never happen.
                            enabled: numberSlot.canDrag && !root._releasing
                            acceptedButtons: Qt.LeftButton
                            // These rows live in a Flickable that would
                            // otherwise take the gesture and spend it scrolling.
                            preventStealing: true

                            /* The hand that says this is a handle.
                             *
                             * Deliberately *not* `visible: enabled`, which is
                             * what this used to carry. Qt finds the cursor by
                             * walking down to the topmost item under the
                             * pointer that has one, and it does not descend
                             * into hidden items — so a handle that hides itself
                             * whenever it is briefly disabled keeps dropping
                             * out of that walk. `enabled: false` already
                             * refuses the press, which is all hiding was ever
                             * buying; the shape below turns the cursor back to
                             * an arrow so a row with no grip still reads as
                             * having none.
                             *
                             * Read off `canDrag` rather than `enabled`, so the
                             * few frames of a release glide do not blink the
                             * cursor on a row that is plainly still a handle.
                             *
                             * And emphatically without `hoverEnabled`, which
                             * `cursorShape` does not need: Qt sets the cursor
                             * from the item under the pointer, not from hover
                             * events. Switching it on makes `positionChanged`
                             * fire for a mouse that is merely passing over —
                             * see the guard on it below.
                             *
                             * Being the topmost area over the slot, it answers
                             * for the whole of it and not just for itself — so
                             * the no-handle branch asks whether the play button
                             * underneath is showing before settling for an arrow.
                             * A pointing hand there is the same promise the
                             * artwork's own button makes when it is the one
                             * carrying the action.
                             */
                            cursorShape: numberSlot.canDrag
                                       ? (rowItem.inDragBlock ? Qt.ClosedHandCursor
                                                              : Qt.OpenHandCursor)
                                       : (numberSlot.playAction !== ""
                                          ? Qt.PointingHandCursor
                                          : Qt.ArrowCursor)

                            property real pressY: 0
                            property real pressX: 0
                            property bool armed: false

                            // Screen coordinates throughout. This area rides the
                            // row it is shifting — on both axes now, since the
                            // dismiss offset is applied to `rowCard` above it —
                            // so a local delta would cancel itself out. But
                            // mouse.x and mouse.y compensate for the shift
                            // exactly, which makes mapToGlobal of them stable.
                            onPressed: function (mouse) {
                                var g = mapToGlobal(mouse.x, mouse.y)
                                pressX = g.x
                                pressY = g.y
                                armed = false
                            }

                            onPositionChanged: function (mouse) {
                                /* A drag begins with a press, and nothing else.
                                 *
                                 * This handler reads its deltas from `pressX`
                                 * and `pressY`, which only mean anything once
                                 * `onPressed` has set them. Reached without a
                                 * button down — which is what `hoverEnabled`
                                 * above would cause — it measures the pointer
                                 * against a press that never happened, arms
                                 * instantly on the nonsense distance, and
                                 * reorders the queue under a hand that was only
                                 * passing over the handle.
                                 *
                                 * Cheaper than remembering not to turn hover on.
                                 */
                                if (!pressed)
                                    return

                                var g = mapToGlobal(mouse.x, mouse.y)
                                if (!armed) {
                                    var dx = g.x - pressX
                                    var dy = g.y - pressY
                                    // The same 5px the column header waits for,
                                    // so a press that was only ever a click
                                    // stays one. Either axis arms it: pulling a
                                    // row straight off the edge is a gesture in
                                    // its own right and must not need a wobble
                                    // to get going.
                                    if (Math.abs(dy) < 5 && Math.abs(dx) < 5)
                                        return
                                    armed = true
                                    /* And the same 5px decides which of the two
                                     * things this drag is, once and for good.
                                     *
                                     * Whichever way the hand went first.
                                     * Rightward and more sideways than vertical
                                     * is a pull off the list; everything else
                                     * reorders — including leftward, which has
                                     * nowhere to go as a removal and would be a
                                     * gesture that could not finish.
                                     */
                                    root._beginDrag(
                                        rowItem.index, pressY, pressX,
                                        (dx > 0 && dx > Math.abs(dy))
                                            ? "dismiss" : "reorder")
                                }
                                root._dragPointerY = g.y
                                root._dragPointerX = g.x
                            }

                            onReleased: {
                                if (armed)
                                    root._endDrag(true)
                                armed = false
                            }

                            onCanceled: {
                                if (armed)
                                    root._endDrag(false)
                                armed = false
                            }
                        }
                    }

                    // ------------------------------------------------- artwork
                    //
                    // Collapses the same way numberSlot does, and for the same
                    // reason it can: a Row skips invisible children entirely and
                    // adds no spacing for them, so the slot takes its gap with it
                    // rather than leaving a hole where the sleeve was. The badge
                    // and the hover overlay are children of it and go too.
                    Item {
                        width: root.showArt ? root.artworkSize : 0
                        height: parent.height
                        visible: width > 0

                        CoverArt {
                            id: rowArt
                            anchors.centerIn: parent
                            width: root.artworkSize
                            height: width
                            radius: 7
                            tier: 128
                            maskSource: root.rowArtMask
                            hash: String(rowItem.coverHash || "")
                            url: String(rowItem.coverUrl || "")
                            placeholderText: rowItem.titleText
                            showPlaceholderGlyph: false
                        }

                        // Attention badge, pinned to the artwork's corner.
                        //
                        // Over the art rather than beside it because the art is the
                        // first thing read on a row and, on a row that matched
                        // nothing, the least trustworthy: what it shows is whatever
                        // was embedded in the user's own file, not a match Kroma
                        // stands behind.
                        //
                        // It carries the same glyph the status cell does, so the
                        // two ends of a wide row agree rather than each telling
                        // half the story: the badge is what is legible while
                        // scanning down the artwork, the cell is what is legible
                        // when reaching for the button.
                        //
                        // Below the hover overlay in z, so hovering to play or open
                        // a row still darkens the badge with everything else rather
                        // than leaving it floating over a dimmed thumbnail.
                        Rectangle {
                            objectName: "unmatchedBadge"
                            visible: rowItem.needsAttention
                            x: rowArt.x + rowArt.width - width + 4
                            y: rowArt.y - 4
                            width: 18
                            height: 18
                            radius: 9
                            color: Theme.warning
                            border.width: 2
                            // The row's own backdrop, so the badge reads as sitting
                            // on the row rather than punched out of the artwork.
                            border.color: Theme.bg

                            Icon {
                                anchors.centerIn: parent
                                name: rowItem.importState === "needsMetadata"
                                      ? "search" : "warning"
                                size: 12
                                // `strokeWidth` is in grid units, so it scales with
                                // `size` — the default 1.7 comes out at well under a
                                // pixel this small and the glyph collapses into a
                                // smudge. 2.8 lands back on a visible line.
                                strokeWidth: 2.8
                                color: Theme.warningInk
                            }
                        }

                        // Hover overlay turns the artwork into a button — see
                        // rowItem.artAction for which one, and for the rows that
                        // get none.
                        Rectangle {
                            anchors.fill: rowArt
                            radius: rowArt.radius
                            color: Theme.imageScrim
                            opacity: rowHover.hovered && rowItem.artAction !== "" ? 1 : 0
                            visible: opacity > 0
                            Behavior on opacity { NumberAnimation { duration: Theme.fast } }

                            Icon {
                                anchors.centerIn: parent
                                name: rowItem.artAction === "external" ? "external" : "play"
                                size: 16
                                color: Theme.imageInk
                            }
                        }

                        // Fills the artwork rather than the whole slot, so the
                        // padding either side still selects the row like the rest
                        // of it does. Disabled on a row with no action, which lets
                        // the click through to rowMouse below and keeps the click
                        // doing exactly what the absent glyph promises.
                        MouseArea {
                            anchors.fill: rowArt
                            enabled: rowItem.artAction !== ""
                            acceptedButtons: Qt.LeftButton
                            cursorShape: rowItem.artAction !== "" ? Qt.PointingHandCursor
                                                                  : Qt.ArrowCursor
                            onClicked: rowItem.triggerArtAction()
                        }
                    }

                    // ------------------------------------------ title / artist
                    //
                    // The elastic column: it has no stored width and takes whatever
                    // the data columns leave. On the playing row of a numberless
                    // list it also gives back the width of the meter slot, so
                    // everything to its right stays put.
                    Column {
                        width: root.columnLayout
                               ? Math.max(20, root.columnLayout.titleWidth - rowItem.meterShift)
                               : parent.width - x - trailing.width - Theme.gap
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3

                        Text {
                            width: parent.width
                            text: rowItem.titleText
                            elide: Text.ElideRight
                            color: rowItem.isPlaying ? Theme.accent : Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzMed
                            font.weight: Font.DemiBold
                            Behavior on color {
                                enabled: !rowItem._settling
                                ColorAnimation { duration: Theme.fast }
                            }
                        }

                        Text {
                            width: parent.width
                            text: rowItem.artistText
                            elide: Text.ElideRight
                            visible: text !== ""
                            color: Theme.dim
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }
                    }

                    // -------------------------------------------- the columns
                    //
                    // Structure and widths are deliberately separate: the Repeater's
                    // model changes only when a column is switched on, reordered or
                    // dropped, while widths are a map that is reassigned freely. A
                    // resize drag therefore re-evaluates width bindings and rebuilds
                    // no delegates at all.
                    Repeater {
                        id: cells
                        model: root.columnLayout ? root.columnLayout.renderedColumns : []

                        delegate: Item {
                            id: cell

                            required property var modelData
                            readonly property var colDef: modelData

                            width: {
                                var w = root.columnLayout.renderedWidths[colDef.key];
                                return (w !== undefined && w > 0) ? w : colDef.pref;
                            }
                            height: parent.height

                            Text {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                visible: cell.colDef.renderer !== "status"
                                text: cell.colDef.renderer === "status"
                                      ? "" : rowItem.cellText(cell.colDef)
                                elide: Text.ElideRight
                                horizontalAlignment:
                                      cell.colDef.align === "right" ? Text.AlignRight
                                    : cell.colDef.align === "centre" ? Text.AlignHCenter
                                    : Text.AlignLeft
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzSmall
                                font.features: cell.colDef.numeric ? ({ "tnum": 1 }) : ({})
                            }

                            // Only the status column pays for this; every other cell
                            // is the single Text above.
                            Loader {
                                anchors.fill: parent
                                active: cell.colDef.renderer === "status"
                                        && root.statusMode !== ""
                                sourceComponent: statusCell
                            }
                        }
                    }

                    // ------------------------------------------------ trailing
                    Item {
                        id: trailing
                        width: Catalog.MORE_W
                        height: parent.height

                        IconButton {
                            anchors.centerIn: parent
                            icon: "more"
                            iconSize: 16
                            diameter: Catalog.MORE_W
                            opacity: rowHover.hovered ? 1 : 0
                            enabledAction: rowHover.hovered
                            tooltip: ""
                            onClicked: rowItem.openMenu()
                            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                        }
                    }
                }

                // Below the content Row (z: -1) so the download and overflow
                // buttons receive their own clicks instead of this swallowing them.
                MouseArea {
                    id: rowMouse
                    anchors.fill: parent
                    z: -1
                    acceptedButtons: Qt.LeftButton | Qt.RightButton

                    onClicked: function (event) {
                        if (event.button === Qt.RightButton) {
                            rowItem.openMenu()
                            return
                        }
                        // One press opens an artist or an album, where a track
                        // needs two. The asymmetry is the point: selecting a
                        // track is a step towards playing or editing several,
                        // but there is nothing a *set* of artists or records
                        // does, so a click that only selected one would be a
                        // click that did nothing.
                        if (rowItem.isArtist) {
                            deselectTimer.stop()
                            root.artistActivated(Number(rowItem.model.artistId) || -1)
                            return
                        }
                        if (rowItem.isAlbum) {
                            deselectTimer.stop()
                            root.albumActivated(Number(rowItem.model.albumId) || -1)
                            return
                        }
                        root.handleClick(rowItem.uid,
                                         event.modifiers & Qt.ControlModifier,
                                         event.modifiers & Qt.ShiftModifier)
                    }
                    onDoubleClicked: function (event) {
                        if (event.button !== Qt.LeftButton)
                            return
                        // A double click slower than the pause will have dropped
                        // the selection between the two presses. The second press
                        // says the row was the target all along, so put it back —
                        // which is what lets the pause be short.
                        deselectTimer.stop()
                        if (!rowItem.selected)
                            root.handleClick(rowItem.uid, false, false)
                        // No artist or album branch here, deliberately: the first
                        // of the two clicks has already opened the page through
                        // onClicked and this row is no longer on screen.
                        // Anything written for either here would be unreachable.
                        if (root.webMode && !rowItem.inLibrary)
                            return
                        root.playRow(rowItem.index, Number(rowItem.trackId) || -1)
                    }
                }
            }

            // Left accent bar on the playing row.
            Rectangle {
                anchors.left: rowCard.left
                anchors.verticalCenter: parent.verticalCenter
                width: 3
                height: rowItem.isPlaying ? 26 : 0
                radius: 2
                color: Theme.accent
                Behavior on height { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
            }
        }
    }

    Loader {
        id: songGridLoader
        anchors.fill: parent
        anchors.leftMargin: root.sideMargin
        anchors.rightMargin: root.sideMargin
        active: root.gridAvailable || root.gridMode
        onLoaded: root._pushVisibleRange()
        sourceComponent: LibraryGrid {
            objectName: "songGridView"
            labelHeight: 62
            delegate: SongTile { table: root }
            tileSize: root.tileSize
            model: root.gridMode ? root.listVM : null
            visible: root.gridMode
            onViewportChanged: if (root.gridMode && root.songGrid) root._pushVisibleRange()
        }
    }

    // ---------------------------------------------------------- column header

    TrackColumnHeader {
        id: columnHeader
        visible: root.columnLayout !== null && root.columnHeaderHeight > 0
        y: root._headerY
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.columnHeaderHeight
        z: 3

        layout: root.columnLayout
        sideMargin: root.sideMargin
        leadWidth: root.leadWidth
        trailWidth: root.trailWidth
        // Only paint a background once it is actually over the rows.
        pinned: root._sticky && root._headerY <= 0.5
        onWheelDelta: root._sticky ? list.wheelHandler.handleWheel : null
    }

    // ------------------------------------------------------------ empty state
    //
    // A sibling of the list, not a child: items declared inside a Flickable are
    // parented to its contentItem, which with no rows is zero-high — so an
    // empty state in there has nothing to centre itself in. Sitting out here it
    // centres in the viewport, minus whatever the header has not yet scrolled
    // out of the way.
    //
    // `_headerY` already carries the sticky arithmetic; adding the reserved
    // band gives the first y below the column header in both modes, and both
    // terms are zero when there is no header at all.
    Item {
        anchors.fill: parent
        anchors.topMargin: root._headerY + root.columnHeaderHeight
        // Faded in rather than switched on. `count` reaches zero the moment the
        // model lets go of the last row, which is while that row is still
        // gliding out — an empty state arriving at full strength underneath it
        // reads as two lists at once.
        //
        // Nothing to say and nothing to draw: a caller that sets `emptyText` to
        // "" wants silence, and the glyph alone used to go on speaking. That is
        // how a `disc` sat in the middle of the import panel through every scan.
        objectName: "trackEmptyState"
        opacity: root.count === 0 && root.emptyText !== "" && !root.loading
                 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

        Column {
            anchors.centerIn: parent
            spacing: Theme.gapSm

            Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                name: root.mode === "search" ? "search" : "disc"
                size: 34
                color: Theme.alpha(Theme.text, 0.18)
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.emptyText
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }
        }
    }

    property string emptyText: "Nothing here yet"

    // ------------------------------------------------------- loading state
    //
    // The other thing an empty list can mean. `emptyText` answers "there is
    // nothing"; this answers "there is nothing *yet*", which wants the opposite
    // treatment — not a sentence in the middle of a void, but the void filled
    // with the shape of what is coming.
    //
    // A sibling of the list for the same reason the empty state is one, and
    // sharing its offset so the bars start where the rows will. Gated on
    // `count` as well as on the flag: a caller that leaves `loading` set while
    // rows land would otherwise draw placeholders behind real content.
    // Deliberately unnamed. Six tables live in the one window, so an objectName
    // here would be six objects answering to it and `findChild` returning
    // whichever was built first — which is how this was caught. A test asking
    // about one table's loading state asks that table, through `loading`.
    SkeletonRows {
        anchors.fill: parent
        anchors.topMargin: root._headerY + root.columnHeaderHeight
        rowHeight: root.rowHeight
        sideMargin: root.sideMargin
        maxRows: root.loadingCount
        active: root.loading && root.count === 0
    }

    // How many rows the work in flight is going to produce, when that is known.
    // 0 while it is not — a scan has not counted the folder yet — and the
    // placeholders fill the panel instead.
    property int loadingCount: 0

    // Whether the list is waiting on work rather than genuinely empty. Off by
    // default: a list with nothing in it and nothing coming is the common case,
    // and it already has `emptyText` to say so.
    property bool loading: false

    // ------------------------------------------------------------ row menu

    NocturneMenu {
        id: rowMenu

        MenuRow {
            text: root._idsForMenu().length > 1
                  ? "Play " + root._idsForMenu().length + " tracks"
                  : (root.mode === "queue" ? "Play" : "Play now")
            iconName: "play"
            enabled: root._menuTrackId > 0 || root.mode === "queue"
            onTriggered: root.playRows(root._rowsForMenu(), root._idsForMenu())
        }

        MenuRow {
            text: "Play next"
            iconName: "next"
            visible: root.mode !== "queue"
            height: visible ? implicitHeight : 0
            enabled: root._idsForMenu().length > 0
            // The whole selection, in display order: rows 4 and 8 land in
            // queue slots 1 and 2.
            onTriggered: queueVM.insertNext(root._idsForMenu())
        }

        MenuRow {
            text: root._idsForMenu().length > 1 ? "Add all to queue" : "Add to queue"
            iconName: "plus"
            visible: root.mode !== "queue"
            height: visible ? implicitHeight : 0
            enabled: root._idsForMenu().length > 0
            onTriggered: queueVM.append_track_ids(root._idsForMenu())
        }

        MenuSeparator {
            padding: 0
            topPadding: 4
            bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        MenuRow {
            text: "Add to playlist…"
            iconName: "playlist"
            enabled: root._idsForMenu().length > 0
            onTriggered: addToPlaylist.openFor(root._idsForMenu())
        }

        MenuRow {
            text: root._libraryIdsForMenu().length > 1
                  ? "Generate backing tracks" : "Generate backing track"
            iconName: "mic"
            // Web results are not in the library and import candidates are not
            // in it yet, so neither has anything to attach an instrumental to.
            visible: root.mode !== "search" && root._libraryIdsForMenu().length > 0
            height: visible ? implicitHeight : 0
            onTriggered: root.generateBackingRequested(root._libraryIdsForMenu())
        }

        MenuSeparator {
            padding: 0
            topPadding: 4
            bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        MenuRow {
            text: root._rowsForMenu().length > 1
                  ? "Remove " + root._rowsForMenu().length + " from queue"
                  : "Remove from queue"
            iconName: "x"
            visible: root.mode === "queue"
            height: visible ? implicitHeight : 0
            onTriggered: queueVM.removeSelected()
        }

        MenuRow {
            text: "Remove from playlist"
            iconName: "x"
            visible: root.mode === "playlist"
            height: visible ? implicitHeight : 0
            onTriggered: playlistTracksVM.removeSelectedFromPlaylist()
        }

        // Off the album, not out of the library — the same distinction the
        // playlist row above makes, and the reason both say "remove from"
        // rather than "delete".
        MenuRow {
            text: "Remove from this album"
            iconName: "x"
            visible: root.mode === "album"
            height: visible ? implicitHeight : 0
            onTriggered: albumTracksVM.removeSelectedFromAlbum()
        }

        MenuRow {
            text: root._importRemovable > 1
                  ? "Remove " + root._importRemovable + " from list"
                  : "Remove from list"
            iconName: "x"
            // The import tab is the one view with no `mode` of its own — it
            // borrows "library" — so it is the status column that names it.
            //
            // Nothing is deleted here: these rows are candidates that have not
            // been saved, so this is the same kind of taking-out-of-a-list as
            // the two above, and wears the same icon rather than the trash.
            visible: root.statusMode === "import"
            height: visible ? implicitHeight : 0
            // Disabled, not hidden, when the only row selected is the one being
            // written: the item is still the answer to "how do I get rid of
            // this", and vanishing would read as the tab not offering it.
            enabled: root._importRemovable > 0
            onTriggered: {
                importVM.removeSelected()
                // The import model is not a TrackListModel and emits no
                // selectionChanged, so `_resyncSelection` never runs for it and
                // the table would keep uids for rows that are gone — leaving
                // the details panel showing one of them. Same reason
                // confirmDelete clears below.
                root.clearSelection()
            }
        }

        MenuRow {
            text: "Edit"
            iconName: "edit"
            // One track only. The editor holds a single track's data, and the
            // per-track tools it exists to grow into have no batch meaning —
            // so a multi-row selection gets no offer rather than a bad one.
            // The id check also drops web results and import candidates, which
            // have no library row behind them yet.
            visible: root._idsForMenu().length === 1 && root._menuTrackId > 0
            height: visible ? implicitHeight : 0
            onTriggered: root.editTrackRequested(root._menuTrackId)
        }

        MenuRow {
            id: importLocalRow
            text: "Import local file…"
            iconName: "stash"
            // For the case where you already have the audio and Kroma has the
            // name for it: an untagged file plus the result you just found.
            //
            // Web results only, and only ones that are not in the library
            // already — a row with a green tick has both halves and nothing to
            // join. One row at a time, because one file gets one track's
            // metadata; `_menuRows` rather than `_idsForMenu()` because a web
            // result has no library id to be counted by.
            visible: root.mode === "search" && root.webMode
                     && root._menuTrackId <= 0
                     && root._menuRows.length <= 1
            height: visible ? implicitHeight : 0
            onTriggered: {
                // Taken now rather than when the dialog is accepted: the picker
                // outlives the menu, and by the time it closes `_menuUid` may
                // belong to whatever was right-clicked next.
                root._importMeta =
                    (root.listVM && typeof root.listVM.webResult === "function")
                    ? root.listVM.webResult(root._menuUid) : null
                if (root._importMeta)
                    importFileDialog.open()
            }
        }

        MenuRow {
            text: "Delete from library"
            iconName: "trash"
            danger: true
            // Deliberately unavailable on a multi-row selection. Deleting from
            // the library erases the audio file too, and offering that behind
            // one click for a selection the user may not have fully checked is
            // how libraries get destroyed by accident.
            visible: root._idsForMenu().length === 1
            height: visible ? implicitHeight : 0
            enabled: root._menuTrackId > 0
            onTriggered: confirmDelete.open()
        }
    }

    /* What the enabled plugins offer for the right-clicked row — open it where
     * it came from, copy its id — resolved by `pluginsVM.actionsFor`, which
     * leaves out any action this row cannot fill in. Each becomes a menu row,
     * inserted just above Import local file…: a Menu has no Repeater, so an
     * Instantiator makes the rows and `insertItem` places them. */
    property var _menuActions: []

    Instantiator {
        model: root._menuActions
        delegate: MenuRow {
            required property var modelData
            text: String(modelData.label || "")
            iconName: String(modelData.iconName || "")
            iconSource: String(modelData.iconUrl || "")
            onTriggered: root._runAction(modelData)
        }
        onObjectAdded: (index, object) => {
            var at = rowMenu.count;
            for (var i = 0; i < rowMenu.count; ++i) {
                if (rowMenu.itemAt(i) === importLocalRow) { at = i; break; }
            }
            rowMenu.insertItem(at, object);
        }
        onObjectRemoved: (index, object) => rowMenu.removeItem(object)
    }

    function _runAction(action) {
        if (!action)
            return;
        if (action.kind === "openUrl") {
            Qt.openUrlExternally(String(action.value));
        } else if (action.kind === "copy") {
            clipboardProxy.text = String(action.value);
            clipboardProxy.selectAll();
            clipboardProxy.copy();
            clipboardProxy.deselect();
            root.copied(String(action.value));
        }
    }

    // How many selected rows the import list would actually give up — the
    // selection, less the row being written right now, which Remove leaves
    // alone. Zero anywhere that is not the import tab.
    //
    // Guarded and hoisted out of the menu row because `text` and `enabled`
    // bindings are evaluated for every TrackTable in the app, not only the one
    // on screen, and only one of them has an import list behind it.
    readonly property int _importRemovable:
        root.statusMode === "import" && typeof importVM !== "undefined"
        ? importVM.removableSelectedCount : 0

    AddToPlaylistDialog {
        id: addToPlaylist
    }

    // QML has no clipboard API; a read-only TextInput's own copy() is the
    // standard way to reach one. Nothing to see here — it exists for that call
    // alone, which works whether or not the item is drawn.
    TextInput {
        id: clipboardProxy
        visible: false
        readOnly: true
    }

    // The audio for a web result, off the user's own disk. Same filters as the
    // Library page's importer, so both doors into the import list accept the
    // same files; the authoritative set is MEDIA_EXTENSIONS in the backend.
    FileDialog {
        id: importFileDialog
        title: "Choose the audio file for “" + root._menuTitle + "”"
        fileMode: FileDialog.OpenFile
        nameFilters: ["Audio files (*.aac *.flac *.m4a *.mp3 *.ogg *.opus *.wav)",
                      "All files (*)"]
        onAccepted: {
            importVM.importFileAs(selectedFile, root._importMeta)
            root.importPageRequested()
        }
    }

    // Only ever reached with a single row selected — the menu item that opens
    // it is hidden otherwise.
    ConfirmDialog {
        id: confirmDelete
        title: "Delete from library?"
        body: "“" + root._menuTitle + "” will be permanently removed "
              + "from your library, along with its audio file."
        confirmText: "Delete"
        danger: true
        onConfirmed: {
            playerVM.deleteTrack(root._menuTrackId)
            root.clearSelection()
        }
    }
}
