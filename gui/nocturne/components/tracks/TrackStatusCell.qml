import QtQuick
import Nocturne

/*
 * The STATUS cell: what will happen to this row, and — where there is one — the
 * button that makes it happen.
 *
 * An import row's state is not worked out here. The backend classifies it once
 * and hands it over as `importState`, and this file's whole job is to draw it.
 * That is deliberate: what this cell offers and what `stashAll` will actually do
 * are two statements of one rule, and the previous version kept its own copy of
 * `_is_stashable` — five terms over four fields, free to drift the day any of
 * them moved. The classification is one function now, in
 * `ImportViewModel._state`, and everything here reads its answer. What is left
 * local is one list of which states carry a button, which a delegate cannot
 * avoid needing; see `actionable` for what holds it to the backend's.
 *
 * The six states, and what each is telling the user to do:
 *
 *   inLibrary       a tick, in `positive`. Done; nothing to do.
 *   download        a button. The audio is fetched by a plugin's audio source.
 *   stash           a button. The file is already here and is filed as it is.
 *   tagsOnly        a button, in `warning`. It will import, but from its own
 *                   tags — no album, artwork or release date. Worth noticing
 *                   before pressing Import all, and invisible otherwise.
 *   needsMetadata   a magnifier, faded. There is nothing to name the track
 *                   after, so nothing to press: the fix is to find the track in
 *                   Browse and pair it with this file, which importFileAs
 *                   accepts for exactly this row.
 *   unavailable     an x, faded. No enabled plugin can fetch the track and
 *                   there is no file either. The only move is to take it out
 *                   of the list — or to turn on a plugin that can.
 *
 * Two rules hold the set together, and both are about not lying:
 *
 * COLOUR IS SEVERITY, AND NEVER THE ACCENT. `Theme.accent` is one of six hues
 * the user picks, and two of them (`verdant`, `gold`) are a green and an amber —
 * an accent-coloured state would mean "done" under one theme and "careful" under
 * another. State draws only on `positive`, `warning`, `danger` and the neutral
 * tones. Accent stays what it is everywhere else in Nocturne: the colour of
 * something you can interact with, on hover.
 *
 * FADED MEANS YOU CANNOT ACT. The two states with no button are the two that are
 * dimmed, so "there is nothing to press here" is legible before the pointer
 * arrives. Fading is done with `opacity` on the item and never with
 * `Theme.alpha` on an icon's colour: `Icons.svg` strips the alpha channel
 * (see `_hex`), so a colour asked for at 55% renders at 100% and the marking
 * silently does not happen.
 *
 * `busy` overrides all of it — a spinner, while a download or a stash is in
 * flight. Two things raise it: this row's own button, and the import queue
 * arriving at this row, which is how "Import all" shows which file it is on.
 *
 * Web rows are the one thing still worked out here, because a web result has no
 * `importState` — SearchViewModel serves a different list. They also have a rule
 * of their own: while any track is downloading, every *other* row's button dims,
 * because downloads run one at a time. That limit belongs to
 * SearchViewModel.downloadRow and is enforced there; the dimming only makes
 * it visible before the user runs into it. Both kinds of row are keyed by
 * `uid` — which source a web row came from, and what it is called there, is
 * the backend's business.
 */
Item {
    id: root

    // "web" | "import" | ""
    property string statusMode: ""
    property var trackId: null
    property var uid: null

    /* The backend's classification of this row — the `importState` role. Empty
     * for any list that is not the import list. */
    property string importState: ""

    /* This row's container format, lower-case and dotless ("opus", "flac"), or
     * "" for a row with no file. Only used to say so in the tooltip: what it
     * costs to import is the SOURCE column's job to show. */
    property string sourceFormat: ""

    readonly property bool isWeb: statusMode === "web"

    readonly property bool inLibrary:
        isWeb ? Number(trackId) > 0 : importState === "inLibrary"

    /* Whether there is anything to press. For an import row this is the state;
     * for a web row it is the one question a web row has.
     *
     * This list is `ImportViewModel.SAVEABLE_STATES` said again, and it has to
     * be: a delegate draws without a round trip, so somebody local has to know
     * which states carry a button. What keeps the two honest is a test rather
     * than a comment — `test_the_cell_offers_a_button_exactly_when_the_backend
     * _accepts_one` loads this component and asks it and the backend the same
     * six questions. Adding a state without touching it fails there. */
    readonly property bool actionable:
        isWeb ? uid !== null
              : (importState === "download" || importState === "stash"
                 || importState === "tagsOnly")

    /* The state's own colour. Severity, never accent — see the note above. */
    readonly property color stateColor: {
        // `inLibrary` first: a web row reaches it through `trackId`, not through
        // `importState`, so testing the role alone would miss it.
        if (inLibrary)                       return Theme.positive;
        if (importState === "tagsOnly")      return Theme.warning;
        if (importState === "needsMetadata") return Theme.warning;
        if (importState === "unavailable")   return Theme.danger;
        return Theme.dim;
    }

    readonly property string stateIcon: {
        // Same ordering as `stateColor`, for the same reason.
        if (inLibrary)                       return "check";
        if (isWeb)                           return "download";
        if (importState === "download")      return "download";
        if (importState === "tagsOnly")      return "warning";
        if (importState === "needsMetadata") return "search";
        if (importState === "unavailable")   return "x";
        return "stash";
    }

    /* What the row is, and what to do about it. Every state answers, including
     * the two with no button — those are the ones where the user most needs
     * telling, since there is nothing to click to find out. */
    readonly property string stateTooltip: {
        if (inLibrary)
            return "Already in your library";
        if (isWeb)
            return otherDownloadRunning
                   ? "Another track is downloading — one at a time"
                   : "Download to library";
        if (importState === "download")
            return "Download to library";
        if (importState === "stash")
            return sourceFormat !== "" && sourceFormat !== "opus"
                   ? "Import your file — converted from "
                     + sourceFormat.toUpperCase()
                   : "Import your file";
        if (importState === "tagsOnly")
            return "Matched nothing — imports from its own tags, "
                   + "without the album, artwork or release date";
        if (importState === "needsMetadata")
            return "Has no title or artist — "
                   + "find it in Browse and pair it with this file";
        if (importState === "unavailable")
            return "Nothing can fetch this track — remove it from the list";
        return "";
    }

    /* Set by this row's own button, which has to show something before the
     * backend has had a turn. Cleared by whichever completion signal below
     * belongs to this row. Import rows only — a web row reads `downloading`
     * off the view model instead, which is strictly better and available to it
     * because downloads are one at a time and the backend says which one. */
    property bool working: false

    /* Whether this row is the download in flight.
     *
     * Same reasoning as `stashing` below, and the same fix: derived, not
     * latched, so scrolling the delegate away and back cannot lose the
     * spinner. `downloadingUid` updates during the click that starts it, so
     * this is already true by the time the button's handler returns. */
    readonly property bool downloading:
        isWeb
        && uid !== null
        && typeof searchVM !== "undefined"
        && Number(searchVM.downloadingUid) === Number(uid)

    /* A different track is downloading, so this row's button is not available.
     *
     * Downloads run one at a time; the backend refuses a second one outright
     * (SearchViewModel.downloadRow). Dimming the button here is only so the
     * refusal is visible before it is provoked — the rule is not enforced by
     * this binding, and a GUI that ignored it would simply be told no. */
    readonly property bool otherDownloadRunning:
        isWeb && !downloading
        && typeof searchVM !== "undefined"
        && searchVM.isDownloading

    /* Whether the import queue is on this row right now.
     *
     * Derived from the view model rather than latched from `stash_started`,
     * because this cell lives in a list delegate: scroll it past the cache
     * buffer and it is destroyed, scroll back and a fresh one appears with
     * every local flag at its default. A latched spinner would vanish partway
     * through a long import — worse, it would vanish from the one row that is
     * genuinely still working. Read off `stashingUid` it cannot drift.
     */
    readonly property bool stashing:
        statusMode === "import"
        && uid !== null
        && typeof importVM !== "undefined"
        && Number(importVM.stashingUid) === Number(uid)

    // Either this row asked and is waiting to be picked up, or the queue has
    // reached it. Both look the same, because to the user they are.
    readonly property bool busy: working || stashing || downloading

    // ------------------------------------------------------- nothing to press
    //
    // Done, or stuck. Either way there is no button, so the glyph carries the
    // whole message and a tooltip of its own carries the rest — an IconButton's
    // built-in one is not available to something that is not a button.

    Item {
        id: staticChip
        anchors.centerIn: parent
        width: 30
        height: 30
        visible: !root.busy && (root.inLibrary || !root.actionable)
        // `needsMetadata` only. The other two receding states — `inLibrary` and
        // `unavailable` — are faded by the row itself, and fading them twice
        // would take the glyph past legible. Applied here rather than to the
        // icon's colour: see the note at the top of this file.
        opacity: root.importState === "needsMetadata" ? 0.8 : 1.0

        Icon {
            anchors.centerIn: parent
            name: root.stateIcon
            size: root.inLibrary ? 17 : 15
            color: root.stateColor
        }

        MouseArea {
            id: staticHover
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.NoButton   // the row underneath keeps its clicks
        }

        NocturneTooltip {
            text: root.stateTooltip
            visible: root.stateTooltip !== "" && staticHover.containsMouse
        }
    }

    // ---------------------------------------------------------------- action

    IconButton {
        anchors.centerIn: parent
        visible: !root.inLibrary && root.actionable && !root.busy
        icon: root.stateIcon
        iconSize: 17
        diameter: 30
        // `tagsOnly` wears its state at rest, because a row that will import
        // with less than the others should say so without being hovered. The
        // two ordinary states sit in the neutral tone and light up on hover
        // like every other control in Nocturne.
        color: root.importState === "tagsOnly" ? Theme.warning : Theme.dim
        hoverColor: root.importState === "tagsOnly" ? Theme.warning : Theme.accent
        enabledAction: !root.otherDownloadRunning
        tooltip: root.stateTooltip
        onClicked: {
            if (root.isWeb) {
                // No optimistic flag: `downloading` picks this up from the view
                // model inside this call, and stays false if it was refused.
                searchVM.downloadRow(Number(root.uid));
            } else {
                root.working = true;
                importVM.stashTrack(Number(root.uid));
            }
        }
    }

    // --------------------------------------------------------------- working

    Item {
        id: spinner
        anchors.centerIn: parent
        width: 17
        height: 17
        visible: root.busy && !root.inLibrary

        Rectangle {
            anchors.fill: parent
            radius: width / 2
            color: "transparent"
            border.width: 2
            border.color: Theme.alpha(Theme.accent, 0.25)
        }
        Rectangle {
            width: 5
            height: 5
            radius: 2.5
            color: Theme.accent
            x: parent.width / 2 - width / 2
            y: -1
            transformOrigin: Item.Center
        }
        RotationAnimator on rotation {
            running: spinner.visible
            loops: Animation.Infinite
            from: 0
            to: 360
            duration: 900
        }
    }

    // ------------------------------------------------------------ completion

    // Only stashes need to be caught here. A web row's spinner is a binding on
    // `downloadingUid`, so it goes out by itself however the download ended —
    // finished, failed, or refused before it began.
    Connections {
        target: root.statusMode === "import" ? importVM : null
        function onStash_finished(uid) {
            if (Number(uid) === Number(root.uid))
                root.working = false;
        }
        function onStash_failed(uid, message) {
            if (Number(uid) === Number(root.uid))
                root.working = false;
        }
        // -1 is the whole batch, which includes this row.
        function onDownload_blocked(uid) {
            if (Number(uid) === Number(root.uid) || Number(uid) === -1)
                root.working = false;
        }
    }
}
