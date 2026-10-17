import QtQuick
import Nocturne
import "../../components/controls"
import "../../components/tracks"

// Finding music: in the library, or from whatever online sources the enabled
// plugins offer.
//
// Library searches run as you type; online searches wait for Enter, because
// each one is a network round trip. This page only ever shows what you asked for —
// browsing the whole library is the Library page's job.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null
    property bool globalSearch: false
    property alias query: field.text
    readonly property string searchPlaceholder: root.webMode
        ? "Search " + root.sourceLabel + " — press Enter"
        : "Search songs, artists, albums…"
    property var submittedQueries: ({})

    // Both the standalone page and the window toolbar use this input path.
    function editQuery(text) {
        field.text = text
        if (!root.webMode) debounce.restart()
    }
    function submitQuery(text) {
        field.text = text
        debounce.stop()
        if (!root.webMode || text.trim() !== "") {
            if (root.webMode && !searchVM.isSearching)
                submittedQueries[searchVM.currentSource] = text.trim()
            searchVM.text_search(text.trim())
        }
    }
    function restoreQuery(text, source, mode) {
        var needsRefresh = debounce.running || text !== field.text
            || source !== searchVM.currentSource || mode !== searchVM.searchMode
        debounce.stop()
        field.text = text
        searchVM.setSource(source || "library")
        searchVM.searchMode = mode || "best"
        if (!root.webMode) {
            if (needsRefresh) root._runLibrary()
        } else if (!searchVM.isSearching
            && submittedQueries[searchVM.currentSource] !== text.trim()) {
            // Keep matching source results cached. An older online query is
            // restored as a draft: Enter refreshes it, with no automatic I/O
            // and no newer query's results shown under an older query's text.
            searchVM.clear_results()
        }
    }

    readonly property bool webMode: !searchVM.search_in_database

    // The online sources the enabled plugins offer — `[{key, label, icon,
    // plugin}]` — and which one is on screen. Empty on a plain install, and
    // then there is no source pill at all: the library is the only answer.
    readonly property var sources: searchVM.sources || []
    readonly property var sourceKeys:
        ["library"].concat(sources.map(function (s) { return s.key }))
    readonly property string sourceLabel: {
        for (var i = 0; i < sources.length; ++i) {
            if (sources[i].key === searchVM.currentSource)
                return sources[i].label
        }
        return ""
    }

    // How many of the current results can actually be played. Recomputed when
    // the results change rather than bound to a slot call, which would never
    // re-evaluate on its own.
    property int playableCount: 0

    // Forwarded up to the window, which owns navigation.
    // Only database results can raise it — a web result has no library row yet,
    // and the menu entry checks for one.
    signal editTrackRequested(int trackId)

    // An artist result was pressed. Only library search in a mixed mode produces
    // those rows.
    signal openArtistRequested(int artistId)

    // A row that was an album. Same journey, different page.
    signal openAlbumRequested(int albumId)

    // The pill's segments and the modes behind them, in one place. They were two
    // literals — the labels and the values — and keeping them lined up by eye is
    // how a fourth segment silently selects the third mode.
    readonly property var modes: ["best", "artists", "tracks", "albums"]
    readonly property var modeLabels: ["Best match", "Artists", "Tracks", "Albums"]

    // A web result has been paired with a local file. The import list it landed
    // in lives on the Library page, so the window is asked to go there.
    signal importPageRequested()

    // An empty box means an empty page. text_search("") clears the results,
    // which is the whole of it.
    function _runLibrary() {
        searchVM.text_search(field.text.trim())
    }

    Timer {
        id: debounce
        interval: 260
        onTriggered: if (!root.webMode) root._runLibrary()
    }

    Connections {
        target: searchVM
        function onSearch_results_changed() {
            root.playableCount = searchVM.get_result_ids().length
        }
        function onSearch_mode_changed() {
            modePill.currentIndex = root.modes.indexOf(searchVM.searchMode)
        }
        function onSearch_in_database_changed() {
            sourcePill.currentIndex = Math.max(0, root.sourceKeys.indexOf(searchVM.currentSource))
            if (!root.webMode && field.text.trim() === "")
                root._runLibrary()
        }
        function onError_occurred(message) {
            toast.show(message, true)
        }
        function onDownload_completed(uid, trackId) {
            toast.show("Added to your library", false)
        }
        function onDownload_failed(uid, error) {
            toast.show("Download failed: " + error, true)
        }
        // Not an error: downloads run one at a time and one is already going.
        // The button that asked is dimmed, so this is the rare route in — a
        // click that beat the binding, or a GUI that never dimmed anything.
        function onDownload_busy(uid) {
            toast.show("One download at a time — this one is still going", false)
        }
    }

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Search"
        subtitle: root.webMode
                  ? "Search " + root.sourceLabel + " and download to your library"
                  : (field.text.trim() === ""
                     ? "Search your library — the full list lives in Library"
                     : Fmt.count(table.count, "result"))

        // What to look for, and where to look. Two pills rather than one
        // combined control: the source is about which world you are searching,
        // the kind is about what counts as an answer, and they compose — you can
        // want artists from the library while the web still means tracks.
        //
        // The kind pill is hidden online, where the question has no meaning: an
        // online source is searched for songs, and nothing here downloads an
        // artist.
        //
        // Albums are a segment of their own and never part of Best match. That
        // list answers "which recording did you mean"; a record is a different
        // question, and one asked deliberately — which is what picking the
        // segment is.
        Pill {
            id: modePill
            anchors.verticalCenter: parent.verticalCenter
            visible: !root.webMode
            options: root.modeLabels
            currentIndex: 0
            onSelected: function (index) {
                searchVM.searchMode = root.modes[index]
            }
        }

        // Labels from the plugins' own manifests. Rebuilt when one is turned
        // on or off; if the one on screen goes, the backend falls back to the
        // library and says so through `search_in_database_changed`.
        Pill {
            id: sourcePill
            objectName: "searchSourcePill"
            anchors.verticalCenter: parent.verticalCenter
            visible: root.sources.length > 0
            options: ["Library"].concat(root.sources.map(function (s) { return s.label }))
            currentIndex: Math.max(0, root.sourceKeys.indexOf(searchVM.currentSource))
            onSelected: function (index) {
                searchVM.setSource(root.sourceKeys[index] || "library")
            }
        }
    }

    // The backend's default is "tracks", so that a GUI which never heard of
    // artist rows cannot be handed one. This GUI has, and opens on Best match —
    // set once here rather than bound, because searchMode is writable and a
    // binding into it would be destroyed the first time the pill assigned to it.
    Component.onCompleted: searchVM.searchMode = "best"

    Item {
        id: searchRow
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        height: root.globalSearch && !actions.visible ? 0 : 40

        NocturneField {
            id: field
            visible: !root.globalSearch
            anchors.left: parent.left
            anchors.right: actions.left
            anchors.rightMargin: Theme.gapSm
            height: parent.height
            iconName: "search"
            placeholder: root.webMode
                         ? "Search " + root.sourceLabel + " — press Enter"
                         : "Filter your library…"

            onEdited: function (text) { root.editQuery(text) }
            onAccepted: function (text) { root.submitQuery(text) }
        }

        Row {
            id: actions
            visible: !root.webMode && root.playableCount > 0
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.gapSm

            // Counted from the result ids rather than the row count: in a mixed
            // list some of those rows are artists, and "Play all" over a page of
            // nothing but artists would be a button that does nothing.
            // get_result_ids() already skips them.
            AppButton {
                text: "Play all"
                iconName: "play"
                variant: "soft"
                visible: !root.webMode && root.playableCount > 0
                onClicked: playerVM.replace_queue_and_play(searchVM.get_result_ids())
            }

            AppButton {
                text: "Shuffle"
                iconName: "shuffle"
                variant: "ghost"
                visible: !root.webMode && root.playableCount > 1
                onClicked: {
                    var ids = searchVM.get_result_ids()
                    for (var i = ids.length - 1; i > 0; i--) {
                        var j = Math.floor(Math.random() * (i + 1))
                        var t = ids[i]; ids[i] = ids[j]; ids[j] = t
                    }
                    playerVM.replace_queue_and_play(ids)
                }
            }
        }
    }

    // --------------------------------------------------------------- results

    TrackTable {
        id: table
        anchors.top: searchRow.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        sideMargin: Theme.gapXl

        mode: "search"
        listVM: searchVM
        active: root.active
        webMode: root.webMode
        // Two profiles, not one: an online result carries title, artist,
        // album, duration and an id and nothing else, so there is far less to
        // offer — plus the status cell, which only makes sense out here where
        // a row might not be in the library yet.
        columnLayout: !root.columnLayouts ? null
                    : (root.webMode ? root.columnLayouts.webSearch
                                    : root.columnLayouts.search)
        statusMode: root.webMode ? "web" : ""
        opacity: searchVM.isSearching ? 0.4 : 1
        emptyText: root.webMode
                   ? "Search " + root.sourceLabel + " to find something new"
                   : (field.text.trim() === ""
                      ? "Search your library" : "No tracks match that")

        onEditTrackRequested: function (id) { root.editTrackRequested(id) }
        onImportPageRequested: root.importPageRequested()
        onArtistActivated: function (id) { root.openArtistRequested(id) }
        onAlbumActivated: function (id) { root.openAlbumRequested(id) }
        onCopied: toast.show("Copied to clipboard", false)

        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    }

    // Searching indicator, floated over the (dimmed) results.
    Row {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: searchRow.bottom
        anchors.topMargin: Theme.gapXl
        spacing: Theme.gapSm
        visible: searchVM.isSearching

        Rectangle {
            width: 16
            height: 16
            radius: 8
            color: "transparent"
            border.width: 2
            border.color: Theme.alpha(Theme.accent, 0.3)
            anchors.verticalCenter: parent.verticalCenter

            Rectangle {
                width: 5; height: 5; radius: 2.5
                color: Theme.accent
                x: parent.width / 2 - 2.5
                y: -1
            }
            RotationAnimator on rotation {
                running: searchVM.isSearching
                loops: Animation.Infinite
                from: 0; to: 360; duration: 900
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Searching…"
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }
    }

    Toast { id: toast }
}
