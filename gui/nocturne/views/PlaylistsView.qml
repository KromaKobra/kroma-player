import QtQuick
import QtQuick.Controls
import Nocturne
import "../components"

// The playlist collection opens into a detail page with its own hero.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation to the hidden pages.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)
    property int openId: -1
    readonly property bool listMode: !Theme.prefs.playlistGrid
    readonly property bool compact: Theme.prefs.playlistCompact
    readonly property int tileSize: Math.max(120, Math.min(300, Theme.prefs.playlistTileSize))

    // The cover travels with the click. playlistsVM exposes no row accessors,
    // and reading it back by Qt role number would hardcode a private detail.
    property string _openCoverHash: ""

    function open(playlistId, coverHash) {
        openId = playlistId
        _openCoverHash = String(coverHash || "")
        playlistTracksVM.load(playlistId)
    }
    function back() {
        openId = -1
        // Leaving the detail page is leaving its track list, so its selection
        // stops counting — the grid has nothing selected, which puts the
        // playing track back in the details panel. Same rule as switching
        // views; see main.qml's go().
        if (root.active)
            sidebarVM.setSelection([])
    }

    Connections {
        target: playlistsVM
        function onPlaylist_created(playlistId) {
            root.open(playlistId, "")
        }
    }

    // ================================================================== grid

    Item {
        id: gridPage
        anchors.fill: parent
        visible: opacity > 0
        opacity: root.openId < 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }

        ViewHeader {
            id: gridHeader
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            title: "Playlists"
            subtitle: grid.count > 0 ? Fmt.count(grid.count, "playlist") : "None yet"

            AppButton {
                anchors.verticalCenter: parent.verticalCenter
                text: "New playlist"
                iconName: "plus"
                variant: "filled"
                onClicked: createDialog.openBlank()
            }
            ViewOptionsButton {
                id: optionsButton
                objectName: "playlistViewOptionsButton"
                anchors.verticalCenter: parent.verticalCenter
                collectionName: "Playlist"
                onClicked: viewOptions.openFor(optionsButton)
            }
        }
        ViewOptionsMenu {
            id: viewOptions
            objectName: "playlistViewOptionsMenu"
            viewKey: "playlist"
            viewportWidth: grid.width
            MenuRow {
                text: "New playlist…"
                iconName: "plus"
                onTriggered: createDialog.openBlank()
            }

        }


        LibraryCollectionView {
            id: grid
            anchors.top: gridHeader.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: Theme.gapXl
            anchors.rightMargin: Theme.gapXl
            anchors.topMargin: Theme.gapSm
            model: playlistsVM
            viewObjectName: "playlistCollectionView"
            listMode: root.listMode
            compact: root.compact
            tileSize: root.tileSize
            labelHeight: 54
            delegate: PlaylistDelegate {
                onActivated: root.open(Number(playlistId), coverHash)
                onContextRequested: {
                    root._armMenu(playlistId, name, coverHash, trackCount, false)
                    cellMenu.popup()
                }
                onPlayRequested: {
                    playlistTracksVM.load(playlistId)
                    playAfterLoad.playlistId = playlistId
                    playAfterLoad.restart()
                }
            }
            LibraryEmptyState {
                anchors.centerIn: parent
                visible: grid.count === 0
                iconName: "playlist"
                title: "No playlists yet"
                description: "Group tracks however you like — by mood, by set, by anything."
                AppButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "Create your first playlist"
                    iconName: "plus"
                    variant: "filled"
                    onClicked: createDialog.openBlank()
                }
            }
        }

    }

    // Loading a playlist is synchronous, but the model needs a tick to settle
    // before its ids can be read back for playback.
    Timer {
        id: playAfterLoad
        property int playlistId: -1
        interval: 30
        onTriggered: {
            var ids = playlistTracksVM.trackIds()
            if (ids.length > 0)
                playerVM.replace_queue_and_play(ids)
        }
    }

    // One menu serves both pages. The grid arms it from the cell under the
    // cursor; the detail page arms it from the playlist it has open, so the two
    // entry points cannot drift apart.
    property int _menuId: -1
    property string _menuName: ""
    property string _menuCover: ""
    property int _menuCount: 0
    property bool _menuInDetail: false

    function _armMenu(id, name, cover, count, inDetail) {
        _menuId = id
        _menuName = String(name || "")
        _menuCover = String(cover || "")
        _menuCount = Number(count) || 0
        _menuInDetail = !!inDetail
    }

    function _openDetailMenu() {
        _armMenu(openId, playlistTracksVM.playlistName, _openCoverHash,
                 detailTable.count, true)
        cellMenu.popup()
    }

    NocturneMenu {
        id: cellMenu

        MenuRow {
            text: "Open"
            iconName: "chevronRight"
            // Nothing to open when the menu came from the open playlist itself.
            visible: !root._menuInDetail
            height: visible ? implicitHeight : 0
            onTriggered: root.open(root._menuId, root._menuCover)
        }
        MenuRow {
            text: "Rename…"
            iconName: "edit"
            onTriggered: createDialog.openRename(root._menuId, root._menuName)
        }
        MenuRow {
            text: "Export…"
            iconName: "download"
            onTriggered: exportDialog.openFor(root._menuId, root._menuName,
                                              root._menuCount)
        }
        MenuSeparator {
            padding: 0; topPadding: 4; bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }
        MenuRow {
            text: "Delete playlist"
            iconName: "trash"
            danger: true
            onTriggered: confirmDeletePlaylist.open()
        }
    }

    // ================================================================ detail

    Item {
        id: detailPage
        anchors.fill: parent
        visible: opacity > 0
        opacity: root.openId >= 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }

        // The same banner the artist and album pages wear — see DetailHero.
        DetailHero {
            id: hero
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right

            kicker: "PLAYLIST"
            title: playlistTracksVM.playlistName
            coverHash: root._openCoverHash
            trackCount: detailTable.count
            totalDuration: playlistTracksVM.totalDuration
            canPlay: detailTable.count > 0
            canShuffle: detailTable.count > 1
            showExport: true
            canExport: detailTable.count > 0
            backTooltip: "Back to playlists"

            onBackRequested: root.back()
            onPlayRequested: playerVM.replace_queue_and_play(playlistTracksVM.trackIds())
            onShuffleRequested: playerVM.replace_queue_and_play(
                                    hero.shuffled(playlistTracksVM.trackIds()))
            onExportRequested: exportDialog.openFor(root.openId,
                                                    playlistTracksVM.playlistName,
                                                    detailTable.count)
            onMenuRequested: root._openDetailMenu()
        }

        TrackTable {
            id: detailTable
            anchors.top: hero.bottom
            anchors.topMargin: Theme.gapSm
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            sideMargin: Theme.gapXl

            mode: "playlist"
            listVM: playlistTracksVM
            active: root.active && root.openId >= 0
            columnLayout: root.columnLayouts ? root.columnLayouts.playlist : null
            showIndex: true
            // A playlist is an order somebody chose, so its numbers double as
            // grab handles the same way the queue's do — drag to rearrange,
            // drag right to take a track out.
            reorderable: true
            emptyText: "This playlist is empty — add tracks from Browse or the Queue"
            onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
            onEditTrackRequested: function (id) { root.editTrackRequested(id) }
            onCopied: toast.show("Copied to clipboard", false)
        }
    }

    // ================================================================ dialogs

    NameDialog {
        id: createDialog
        onSubmitted: function (name, editId) {
            if (editId > 0)
                playlistsVM.renamePlaylist(editId, name)
            else
                playlistsVM.createPlaylist(name)
        }
    }

    ConfirmDialog {
        id: confirmDeletePlaylist
        title: "Delete this playlist?"
        body: "“" + root._menuName + "” will be removed. "
              + "The tracks stay in your library."
        confirmText: "Delete"
        danger: true
        onConfirmed: {
            if (root.openId === root._menuId)
                root.back()
            playlistsVM.deletePlaylist(root._menuId)
        }
    }

    ExportDialog {
        id: exportDialog
        onNotify: function (message, isError) { toast.show(message, isError) }
    }

    Toast { id: toast }
}
