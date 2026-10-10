import QtQuick
import QtQuick.Controls
import Nocturne
import "../components"

// Playlists: a grid of covers that opens into a detail page with its own hero.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation to the hidden pages.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)
    property int openId: -1

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
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

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
        }

        GridView {
            id: grid
            anchors.top: gridHeader.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: Theme.gapXl
            anchors.rightMargin: Theme.gapXl
            anchors.topMargin: Theme.gapSm
            clip: true
            model: playlistsVM

            readonly property int columns: Math.max(2, Math.floor(width / 200))
            cellWidth: Math.floor(width / columns)
            cellHeight: cellWidth + 54

            // One corner mask for every cover in the grid. Every cell is the
            // same size, so a mask each is pure waste — and per-cell mask
            // layers leave random covers blank while scrolling (CoverArt.qml).
            readonly property int artSize: Math.max(1, cellWidth - 30)
            CornerMask {
                id: gridArtMask
                parent: grid
                width: grid.artSize
                height: grid.artSize
                radius: Theme.radius
            }

            ScrollBar.vertical: ScrollBar { width: 7 }

            delegate: Item {
                id: cell
                required property int index
                required property var playlistId
                required property var name
                required property var coverHash
                required property var trackCount
                required property var totalDuration

                width: grid.cellWidth
                height: grid.cellHeight

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 7
                    radius: Theme.radiusLg
                    color: cellMouse.containsMouse
                           ? Theme.alpha(Theme.text, 0.06) : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }

                Column {
                    anchors.fill: parent
                    anchors.margins: 15
                    spacing: 11

                    Item {
                        width: parent.width
                        height: width

                        CoverArt {
                            id: cellArt
                            anchors.fill: parent
                            radius: Theme.radius
                            tier: 512
                            maskSource: gridArtMask
                            hash: String(cell.coverHash || "")
                            placeholderText: String(cell.name || "")
                            scale: cellMouse.containsMouse ? 1.03 : 1.0
                            Behavior on scale {
                                NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
                            }
                        }

                        // Play button floats in on hover.
                        Rectangle {
                            width: 44
                            height: 44
                            radius: 22
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 10
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: Theme.accent }
                                GradientStop { position: 1.0; color: Theme.accentAlt }
                            }
                            opacity: cellMouse.containsMouse ? 1 : 0
                            scale: cellMouse.containsMouse ? 1 : 0.8
                            visible: opacity > 0 && Number(cell.trackCount) > 0

                            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                            Behavior on scale {
                                NumberAnimation { duration: Theme.normal; easing.type: Easing.OutBack }
                            }

                            Icon {
                                anchors.centerIn: parent
                                anchors.horizontalCenterOffset: 1
                                name: "play"
                                size: 17
                                color: Theme.accentInk
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    playlistTracksVM.load(cell.playlistId)
                                    playAfterLoad.playlistId = cell.playlistId
                                    playAfterLoad.restart()
                                }
                            }
                        }
                    }

                    Column {
                        width: parent.width
                        spacing: 3

                        Text {
                            width: parent.width
                            text: String(cell.name || "")
                            elide: Text.ElideRight
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzBody
                            font.weight: Font.DemiBold
                        }

                        Text {
                            width: parent.width
                            text: Fmt.count(Number(cell.trackCount) || 0, "track")
                                  + (Number(cell.totalDuration) > 0
                                     ? " · " + Fmt.longDuration(cell.totalDuration) : "")
                            elide: Text.ElideRight
                            color: Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                        }
                    }
                }

                MouseArea {
                    id: cellMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    cursorShape: Qt.PointingHandCursor
                    z: -1
                    onClicked: function (event) {
                        if (event.button === Qt.RightButton) {
                            root._armMenu(cell.playlistId, cell.name,
                                          cell.coverHash, cell.trackCount, false)
                            cellMenu.popup()
                        } else {
                            root.open(cell.playlistId, cell.coverHash)
                        }
                    }
                }
            }

            // Empty state
            Column {
                anchors.centerIn: parent
                spacing: Theme.gap
                visible: grid.count === 0
                width: 320

                Icon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    name: "playlist"
                    size: 40
                    color: Theme.alpha(Theme.text, 0.16)
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: "No playlists yet"
                    color: Theme.dim
                    font.family: Theme.displayFamily
                    font.pixelSize: Theme.fzMed
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: "Group tracks however you like — by mood, by set, by anything."
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                }
                AppButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "Create your first playlist"
                    iconName: "plus"
                    variant: "filled"
                    onClicked: createDialog.openBlank()
                }
            }
        }

        SmoothWheel { target: grid }
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
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

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
