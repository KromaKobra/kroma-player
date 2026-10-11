import QtQuick
import QtQuick.Controls
import QtQuick.Window
import Nocturne
import "components/artwork"
import "components/controls"
import "components/dialogs"
import "components/playback"
import "components/shell"
import "components/tracks"
import "views/editing"
import "views/importing"
import "views/library"
import "views/playback"
import "views/settings"

// Nocturne — the official interface for Kroma Player.
//
// Layout: a frameless window with its own title bar, a navigation rail down the
// left, the active view in the middle, an optional details panel on the right,
// and the transport pinned to the bottom. Behind all of it sits the playing
// track's artwork, blurred.
Window {
    id: mainWindow

    // Frameless so the title bar can be part of the design. TitleBar and
    // ResizeGrips restore dragging and resizing.
    flags: Qt.Window | Qt.FramelessWindowHint

    width: settingsVM.sessionValue("window.width", 1280)
    height: settingsVM.sessionValue("window.height", 780)
    minimumWidth: 940
    minimumHeight: 600
    visible: true
    color: Theme.bg
    title: "Kroma Player"

    Binding {
        target: typeof artworkPaletteVM !== "undefined" ? artworkPaletteVM : null
        property: "coverHash"
        value: Theme.prefs.followArtwork ? playerVM.current_track_cover_hash : ""
    }

    Binding {
        target: Theme
        property: "artworkPalette"
        value: typeof artworkPaletteVM !== "undefined" ? artworkPaletteVM.palette : ({})
    }

    // ------------------------------------------------------------------ state

    // Restore the main collection destinations or Home (13). Editors and
    // work pages need loaded entities or an import session, so do not reopen
    // them from only a saved index.
    property int viewIndex: Theme.prefs.navIndex === 13 ? 13
        : Math.max(0, Math.min(Theme.prefs.navIndex, 5))
    // Restored from session.json alongside the geometry it belongs with. It
    // used to be stored twice — here and as a Theme pref — and only the Theme
    // copy was ever read back, so the two could disagree without anything
    // noticing.
    property bool infoOpen: settingsVM.sessionValue("window.infoOpen", false)
    property real infoWidth: settingsVM.sessionValue("window.infoWidth", 320)

    readonly property real infoEffectiveWidth:
        infoResize.targetWidth

    // Where Back on a hidden page returns to. Never one of those pages itself.
    property int previousViewIndex: 0

    readonly property var hiddenViews: [6, 7, 8, 9, 10, 11, 12]

    // Whether the artist and album slots are up. Set by opening one and cleared
    // only by Back on its page: an artist you went to look at stays in the rail
    // while you step over to the Library and back, the way the page you were
    // just on ought to. Tying the slot to "you are standing on the page" instead
    // would take it away the moment you looked at anything else, which leaves no
    // way back short of finding the artist again.
    property bool artistOpen: false
    property bool albumOpen: false

    // The track lists' column layouts, instantiated once and handed down, so a
    // layout survives switching tabs and the queue and history keep sharing
    // one. Handed the store rather than reaching for `Kroma.Settings` itself,
    // so a layout can be driven by something else in a test or a fork.
    // The id is deliberately not `columnLayouts`: the views expose a property of
    // that name, and `columnLayouts: columnLayouts` would be ambiguous at best.
    ColumnLayouts {
        id: trackColumns
        settings: settingsVM
    }

    // Send the user to the backing-track workbench with a batch loaded.
    function generateBackingFor(ids) {
        if (!ids || ids.length === 0)
            return
        backingVM.enqueue(ids)
        go(6)
    }

    function editTrack(id) { if (id > 0) navigate({view: 7, entityId: id}) }
    function openArtist(id) { if (id > 0) navigate({view: 9, entityId: id}) }
    function editArtist(id) { if (id > 0) navigate({view: 10, entityId: id}) }
    function openAlbum(id) { if (id > 0) navigate({view: 11, entityId: id}) }
    function editAlbum(id) { if (id > 0) navigate({view: 12, entityId: id}) }

    // Start an import from a source chosen somewhere else — the Library header
    // is the only such place today. The page is put up first and raises its own
    // dialog, so the file chooser is answered against the page that will show
    // the result rather than the one you happened to be standing on.
    function startImport(kind) {
        go(8)
        importPage.chooseSource(kind)
    }

    NavigationHistory { id: navigationHistory; objectName: "navigationHistory" }
    readonly property var viewLabels: ["Now Playing", "Queue", "Search", "Playlists",
        "Library", "Settings", "Backing tracks", "Edit track", "Import", "Artist",
        "Edit artist", "Album", "Edit album", "Home"]

    // Capture before loading another entity: all profile/editor views reuse a
    // single view model. History owns IDs and labels, never those mutable models.
    function currentRoute() {
        var route = {view: viewIndex, entityId: 0, label: viewLabels[viewIndex]}
        if (viewIndex === 4) {
            route.libraryMode = Theme.prefs.libraryMode
            route.label = ["Songs", "Artists", "Albums"][route.libraryMode]
        } else if (viewIndex === 7) {
            route.entityId = editVM.trackId
            route.label = "Edit track · " + editVM.title
        } else if (viewIndex === 9 || viewIndex === 10) {
            route.entityId = artistVM.artistId
            route.label += " · " + artistVM.name
        } else if (viewIndex === 11 || viewIndex === 12) {
            route.entityId = albumVM.albumId
            route.label += " · " + albumVM.name
        } else if (viewIndex === 2) {
            route.query = browsePage.query
            route.source = searchVM.currentSource
            route.mode = searchVM.searchMode
            if (route.query.trim()) route.label += " · " + route.query.trim()
        }
        route.key = route.view + ":" + route.entityId + ":"
            + (route.libraryMode === undefined ? "" : route.libraryMode)
        return route
    }

    function go(index) {
        var route = {view: index}
        if (index === 7) route.entityId = editVM.trackId
        if (index === 9 || index === 10) route.entityId = artistVM.artistId
        if (index === 11 || index === 12) route.entityId = albumVM.albumId
        navigate(route)
    }
    function goLibrary(mode) { navigate({view: 4, libraryMode: mode}) }
    function visitHistory(index) {
        if (index < 0 || index >= navigationHistory.entries.length) return
        navigate(navigationHistory.entries[index], index)
    }

    function navigate(route, historyIndex, discardApproved) {
        if (!route || route.view < 0 || route.view >= viewLabels.length) return
        var entityId = route.entityId || 0
        var trackChange = route.view === 7 && editVM.trackId !== entityId
        var artistChange = (route.view === 9 || route.view === 10)
            && artistVM.artistId !== entityId
        var albumChange = (route.view === 11 || route.view === 12)
            && albumVM.albumId !== entityId
        if (historyIndex === undefined && route.view === viewIndex
            && !trackChange && !artistChange && !albumChange
            && (route.libraryMode === undefined || route.libraryMode === Theme.prefs.libraryMode)
            && route.query === undefined) return
        if (!discardApproved && ((trackChange && editVM.isDirty)
            || (artistChange && artistVM.isDirty) || (albumChange && albumVM.isDirty))) {
            replaceEditor.pendingRoute = route
            replaceEditor.pendingIndex = historyIndex === undefined ? -1 : historyIndex
            replaceEditor.open()
            return
        }
        var oldRoute = currentRoute()
        var oldTrackId = editVM.trackId
        var oldArtistId = artistVM.artistId
        var oldAlbumId = albumVM.albumId
        if (trackChange) editVM.load(entityId)
        if (artistChange) artistVM.load(entityId)
        if (albumChange) albumVM.load(entityId)
        if ((route.view === 7 && !editVM.hasTrack)
            || ((route.view === 9 || route.view === 10) && !artistVM.hasArtist)
            || ((route.view === 11 || route.view === 12) && !albumVM.hasAlbum)) {
            // A removed history item must not empty the profile we were on.
            if (trackChange && oldTrackId > 0) editVM.load(oldTrackId)
            if (artistChange && oldArtistId > 0) artistVM.load(oldArtistId)
            if (albumChange && oldAlbumId > 0) albumVM.load(oldAlbumId)
            navigationToast.show("This item is no longer in your library", true)
            return
        }
        if (route.view === 9 || route.view === 10) artistOpen = true
        if (route.view === 11 || route.view === 12) {
            albumOpen = true
            if (albumTracksVM.albumId !== entityId) albumTracksVM.load(entityId)
        }
        if (route.view === 4 && route.libraryMode !== undefined)
            Theme.prefs.libraryMode = route.libraryMode
        if (route.view === 2 && route.query !== undefined)
            browsePage.restoreQuery(route.query, route.source, route.mode)

        if (route.view !== viewIndex) {
            if (hiddenViews.indexOf(viewIndex) < 0) previousViewIndex = viewIndex
            sidebarVM.setSelection([])
            viewIndex = route.view
            Theme.prefs.navIndex = viewIndex
        }
        navigationHistory.update(oldRoute)
        if (historyIndex !== undefined && historyIndex >= 0) {
            navigationHistory.cursor = historyIndex
            navigationHistory.update(currentRoute())
        } else navigationHistory.push(currentRoute())
    }

    ConfirmDialog {
        id: replaceEditor
        objectName: "replaceEditorDialog"
        property var pendingRoute: null
        property int pendingIndex: -1
        title: "Discard unsaved edits?"
        body: "Opening another item replaces the edits staged for the current item."
        confirmText: "Discard edits"
        danger: true
        onConfirmed: mainWindow.navigate(pendingRoute, pendingIndex, true)
    }
    Toast { id: navigationToast }

    // Typing in a field must not trigger the transport shortcuts, so the
    // single-key bindings live in Keys handlers on the focus scope below rather
    // than in global Shortcut objects.

    // -------------------------------------------------------------- backdrop

    AmbientBackdrop {
        anchors.fill: parent
        hash: playerVM.current_track_cover_hash
        intensity: Theme.prefs.ambient ? 1.0 : 0.0
    }

    // Pressing the background deselects. Everything the backdrop shows through
    // reads as background, so rather than enumerate those places this sits
    // behind the entire window and picks up whatever nothing in front of it
    // claimed — buttons, scroll bars and track rows all take their own clicks
    // and never reach here.
    //
    // The three regions that are chrome rather than background — the title bar,
    // the transport and the details panel — consume background presses and
    // swallow their own strays, each with a sink of its own at the bottom of
    // its stack. The navigation rail deliberately has none: it is background
    // like the rest.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: TrackSelection.clear()
    }

    // One material per sidebar, reaching the top of the window. The controls
    // below start below the toolbar; the toolbar's drag region remains above.
    SidebarSurface {
        objectName: "navigationSurface"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: player.top
        width: nav.width
        Rectangle {
            anchors.right: parent.right
            width: 1; height: parent.height
            color: Theme.prefs.ambient ? Theme.line : Theme.lineSoft
        }
    }
    SidebarSurface {
        objectName: "detailsSurface"
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: player.top
        width: infoPanel.width
        visible: width > 0
        Rectangle {
            anchors.left: parent.left
            width: 1; height: parent.height
            color: Theme.prefs.ambient ? Theme.line : Theme.lineSoft
        }
    }
    // ------------------------------------------------------------------ chrome

    TitleBar {
        id: titleBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        objectName: "titleBar"
        z: 10
        targetWindow: mainWindow
        sidebarWidth: nav.width
        sidebarToggleControl: nav.headerToggle
        canGoBack: navigationHistory.canGoBack
        canGoForward: navigationHistory.canGoForward
        historyEntries: navigationHistory.entries
        historyCursor: navigationHistory.cursor
        searchPlaceholder: browsePage.searchPlaceholder
        onBackRequested: mainWindow.visitHistory(navigationHistory.cursor - 1)
        onForwardRequested: mainWindow.visitHistory(navigationHistory.cursor + 1)
        onHistoryRequested: function (index) { mainWindow.visitHistory(index) }
        onHistoryOpened: navigationHistory.update(mainWindow.currentRoute())
        onSearchEdited: function (text) {
            mainWindow.go(2)
            browsePage.editQuery(text)
        }
        onSearchAccepted: function (text) {
            mainWindow.go(2)
            browsePage.submitQuery(text)
        }
        onSearchDismissed: content.forceActiveFocus()
    }

    Binding {
        target: titleBar
        property: "searchText"
        value: browsePage.query
    }

    NavRail {
        id: nav
        objectName: "navRail"
        anchors.left: parent.left
        anchors.top: titleBar.bottom
        anchors.bottom: player.top
        currentIndex: mainWindow.viewIndex
        libraryMode: Theme.prefs.libraryMode
        reducedMotion: Theme.prefs.reducedMotion
        expandedWidth: settingsVM.sessionValue("window.sidebarWidth", 240)
        collapsed: settingsVM.sessionValue("window.sidebarCollapsed", false)
        maximumWidth: Math.min(360, mainWindow.width - 380
            - (mainWindow.infoOpen ? 240 : 0))
        // The editor keeps its slot only while there is something to lose, so
        // you can go look at the track list mid-edit and find your way back.
        // A saved track is no longer being edited, and the slot goes with it.
        showEdit: mainWindow.viewIndex === 7 || editVM.isDirty
        // Import is now a permanent destination in the Tools group.
        // A slot from the moment you open an artist until Back on that page
        // closes it, plus the usual unsaved-work reprieve. Not "while you are
        // on one of the artist pages": stepping over to the Library is not
        // done with the artist, and taking the slot away there would strand
        // the page you were just on. Back is the one thing that means finished
        // — see `artistOpen`.
        //
        // Two views, one slot: the editor is a mode of the artist page rather
        // than a separate destination, so it lights the same slot.
        showArtist: mainWindow.artistOpen || mainWindow.viewIndex === 9
                    || mainWindow.viewIndex === 10 || artistVM.isDirty
        artistLabel: artistVM.hasArtist ? artistVM.name : "Artist"
        // Where the slot goes. The editor while you are standing in it, or when
        // an unsaved edit is the only reason the slot is still up — that is
        // where the edit is. Otherwise the artist's page, including from
        // elsewhere in the app: the slot is how you get back to what you were
        // looking at, which is the page and not the form.
        artistView: mainWindow.viewIndex === 10 ? 10
                    : (mainWindow.artistOpen || mainWindow.viewIndex === 9
                       ? 9 : 10)
        // The album slot, on the same terms as the artist one above.
        showAlbum: mainWindow.albumOpen || mainWindow.viewIndex === 11
                   || mainWindow.viewIndex === 12 || albumVM.isDirty
        albumLabel: albumVM.hasAlbum ? albumVM.name : "Album"
        albumView: mainWindow.viewIndex === 12 ? 12
                   : (mainWindow.albumOpen || mainWindow.viewIndex === 11
                      ? 11 : 12)
        onNavigated: function (index) { mainWindow.go(index) }
        onLibraryNavigated: function (mode) { mainWindow.goLibrary(mode) }
        drawSurface: false
        headerHost: titleBar
        headerHovered: titleBar.sidebarHovered
    }

    // ------------------------------------------------------------------ views

    FocusScope {
        id: content
        objectName: "contentArea"
        anchors.left: nav.right
        anchors.right: infoPanel.left
        anchors.top: titleBar.bottom
        anchors.bottom: player.top
        focus: true

        // Every view stays alive so each keeps its scroll position and state;
        // only the current one is visible, cross-fading on change.
        Item {
            id: stack
            anchors.fill: parent

            Item {
                objectName: "homeView"
                anchors.fill: parent
                visible: mainWindow.viewIndex === 13
            }

            NowPlayingView {
                anchors.fill: parent
                active: mainWindow.viewIndex === 0
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDetailsRequested: mainWindow.infoOpen = true
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            QueueView {
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 1
                opacity: active ? 1 : 0
                visible: opacity > 0
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            BrowseView {
                id: browsePage
                globalSearch: true
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 2
                opacity: active ? 1 : 0
                visible: opacity > 0
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                // Pairing a web result with a local file fills the import list,
                // so that is where the work is finished, and where the user is
                // put.
                onImportPageRequested: mainWindow.go(8)
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onOpenAlbumRequested: function (id) { mainWindow.openAlbum(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            PlaylistsView {
                objectName: "playlistsView"
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 3
                opacity: active ? 1 : 0
                visible: opacity > 0
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            LibraryView {
                id: libraryPage
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 4
                opacity: active ? 1 : 0
                visible: opacity > 0
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onOpenAlbumRequested: function (id) { mainWindow.openAlbum(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SettingsView {
                id: settingsView
                anchors.fill: parent
                active: mainWindow.viewIndex === 5
                opacity: active ? 1 : 0
                visible: opacity > 0
                onBackupRequested: function (thenUpdate) { mainWindow.openBackup(thenUpdate) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            // Not in the rail. You get here by asking for a backing track that
            // does not exist yet — from the transport's mic button or a track's
            // context menu — and not otherwise. It is a workbench for one job,
            // not somewhere to browse to.
            BackingTrackView {
                anchors.fill: parent
                active: mainWindow.viewIndex === 6
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.go(mainWindow.previousViewIndex)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            // The Tools destination also opens when a source is chosen in the
            // Library header, or a web result is paired with a local file.
            ImportView {
                id: importPage
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 8
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.go(mainWindow.previousViewIndex)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            // Also not in the rail. You get here by choosing Edit on a single
            // track. Declared last so that when several hidden pages are up it
            // is the editor that paints over the others during the crossfade,
            // matching the order they sit in on the rail.
            TrackEditView {
                anchors.fill: parent
                active: mainWindow.viewIndex === 7
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.go(mainWindow.previousViewIndex)
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onOpenAlbumRequested: function (id) { mainWindow.openAlbum(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            // One artist's catalogue. Not in the rail: you arrive from a search
            // result, a track's artist name, or the details panel — always from
            // something that mentioned the artist.
            ArtistView {
                objectName: "artistView"
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 9
                opacity: active ? 1 : 0
                visible: opacity > 0
                // Back is what closes the artist: it puts the page away and
                // takes its rail slot with it. Anything else you do only moves
                // off the page and leaves it there to come back to.
                onDone: {
                    mainWindow.artistOpen = false
                    mainWindow.go(mainWindow.previousViewIndex)
                }
                onEditArtistRequested: function (id) { mainWindow.editArtist(id) }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            // Declared last, so that with several hidden pages up it is the
            // editor that paints over the others during the crossfade — the same
            // reason TrackEditView sits at the end of the track side.
            // One album's running order. Reached from the albums face of the
            // Library, or from anything that named the album.
            AlbumView {
                objectName: "albumView"
                anchors.fill: parent
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 11
                opacity: active ? 1 : 0
                visible: opacity > 0
                // Back closes the album, slot and all — see ArtistView above.
                onDone: {
                    mainWindow.albumOpen = false
                    mainWindow.go(mainWindow.previousViewIndex)
                }
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onEditAlbumRequested: function (id) { mainWindow.editAlbum(id) }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onImportPageRequested: mainWindow.go(8)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            ArtistEditView {
                anchors.fill: parent
                active: mainWindow.viewIndex === 10
                opacity: active ? 1 : 0
                visible: opacity > 0
                // Back lands on the artist's page, not on wherever the artist
                // was reached from. You got here from there, and both are hidden
                // pages, so `previousViewIndex` still names the last *permanent*
                // view and would skip the page you were just looking at.
                // A split leaves no artist to go back to, hence the fallback —
                // and no artist to keep a slot for either, so that closes it.
                onDone: {
                    if (artistVM.hasArtist) {
                        mainWindow.go(9)
                    } else {
                        mainWindow.artistOpen = false
                        mainWindow.go(mainWindow.previousViewIndex)
                    }
                }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            // Last of all, for the reason ArtistEditView is second-to-last: with
            // several hidden pages up it should be the editor that paints over
            // the others during the crossfade.
            AlbumEditView {
                anchors.fill: parent
                active: mainWindow.viewIndex === 12
                opacity: active ? 1 : 0
                visible: opacity > 0
                // Back lands on the album's page, not on wherever the album was
                // reached from — both are hidden pages, so `previousViewIndex`
                // names the last *permanent* view and would skip the page you
                // were just looking at.
                onDone: {
                    if (albumVM.hasAlbum) {
                        mainWindow.go(11)
                    } else {
                        mainWindow.albumOpen = false
                        mainWindow.go(mainWindow.previousViewIndex)
                    }
                }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
        }

        // Transport keys, and the keyboard half of deselecting. These are on
        // the focus scope, not global shortcuts, so a focused text field
        // consumes them first — which for Escape is the point: it clears the
        // field it is typed in and only reaches the page once that field is
        // already empty.
        //
        // A retime session borrows four of these keys for as long as it runs.
        // It is the one moment in the application where a transport key is not
        // a transport key, and it belongs here rather than in NowPlayingView
        // because a field the user is typing in still has to win first — which
        // is exactly what putting it on the focus scope buys.
        readonly property bool retiming: typeof lyricVM !== "undefined"
                                         && lyricVM.is_retiming

        Keys.onPressed: function (event) {
            switch (event.key) {
            case Qt.Key_Escape:
                if (content.retiming)
                    lyricVM.cancelRetime()
                else
                    TrackSelection.clear()
                event.accepted = true; break
            case Qt.Key_Space:
                if (content.retiming)
                    lyricVM.markLine()
                else
                    playerVM.toggle_playback()
                event.accepted = true; break
            case Qt.Key_Backspace:
                if (content.retiming) {
                    lyricVM.undoMark(); event.accepted = true
                }
                break
            case Qt.Key_Return:
            case Qt.Key_Enter:
                if (content.retiming) {
                    lyricVM.finishRetime(); event.accepted = true
                }
                break
            case Qt.Key_Right:
                if (event.modifiers & Qt.ControlModifier)
                    playerVM.next()
                else
                    playerVM.seek(Math.min(playerVM.duration, playerVM.position + 5))
                event.accepted = true; break
            case Qt.Key_Left:
                if (event.modifiers & Qt.ControlModifier)
                    playerVM.previous()
                else
                    playerVM.seek(Math.max(0, playerVM.position - 5))
                event.accepted = true; break
            case Qt.Key_Up:
                playerVM.set_volume(Math.min(1, playerVM.volume + 0.05))
                event.accepted = true; break
            case Qt.Key_Down:
                playerVM.set_volume(Math.max(0, playerVM.volume - 0.05))
                event.accepted = true; break
            }
        }
    }

    // ------------------------------------------------------------ info panel

    InfoPanel {
        id: infoPanel
        objectName: "infoPanel"
        anchors.right: parent.right
        anchors.top: titleBar.bottom
        anchors.bottom: player.top
        width: infoResize.displayedWidth
        open: mainWindow.infoOpen
        drawSurface: false
        clip: true
        visible: width > 0
        onEditRequested: function (id) { mainWindow.editTrack(id) }
    }

    // Same direct manipulation and spring as the left sidebar, mirrored.
    SidebarResizeHandle {
        id: infoResize
        objectName: "infoResizeHandle"
        x: Math.min(infoPanel.x - width / 2, mainWindow.width - width)
        anchors.top: infoPanel.top
        anchors.bottom: infoPanel.bottom
        expandedWidth: mainWindow.infoWidth
        collapsed: !mainWindow.infoOpen
        collapsedWidth: 0
        minimumExpandedWidth: 240
        maximumWidth: Math.min(520, mainWindow.width - nav.width - 380)
        collapseThreshold: 156
        rightSidebar: true
        reducedMotion: Theme.prefs.reducedMotion
        onResizeCommitted: function (preferredWidth, collapse) {
            mainWindow.infoWidth = preferredWidth
            mainWindow.infoOpen = !collapse
        }
    }

    // ---------------------------------------------------------------- player

    PlayerBar {
        id: player
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        infoOpen: mainWindow.infoOpen
        onInfoToggled: mainWindow.infoOpen = !mainWindow.infoOpen
        onArtworkClicked: mainWindow.go(0)
        onBackingToggled: playerVM.toggleBackingTrack()
        onBackingGenerateRequested:
            mainWindow.generateBackingFor([playerVM.current_track_id])
    }

    // ------------------------------------------------------------- shortcuts

    Shortcut {
        sequence: "Ctrl+B"
        onActivated: nav.toggle()
    }

    Shortcut {
        sequence: "Ctrl+F"
        onActivated: titleBar.focusSearch()
    }
    Shortcut { sequence: "Alt+Left"; onActivated: mainWindow.visitHistory(navigationHistory.cursor - 1) }
    Shortcut { sequence: "Alt+Right"; onActivated: mainWindow.visitHistory(navigationHistory.cursor + 1) }
    Shortcut { sequence: "Ctrl+1"; onActivated: mainWindow.go(0) }
    Shortcut { sequence: "Ctrl+2"; onActivated: mainWindow.go(1) }
    Shortcut { sequence: "Ctrl+3"; onActivated: mainWindow.go(2) }
    Shortcut { sequence: "Ctrl+4"; onActivated: mainWindow.go(3) }
    Shortcut { sequence: "Ctrl+5"; onActivated: mainWindow.go(4) }
    Shortcut { sequence: "Ctrl+6"; onActivated: mainWindow.go(5) }
    Shortcut { sequence: "Ctrl+I"; onActivated: mainWindow.infoOpen = !mainWindow.infoOpen }

    // -------------------------------------------------------- blocked download

    // A download can be refused before anything starts — nothing enabled can
    // fetch the row, the plugin that can is waiting on its terms, or a program
    // it needs is missing — and the backend says which. The answer is nearly
    // always in Settings → Plugins, so the dialog offers to go there. It lives
    // here rather than in a view because both the browser and the import list
    // can raise it, and because a toast is the wrong shape for something with
    // an action attached.
    function openSettings(tab) {
        mainWindow.go(5)
        settingsView.openTab(tab)
    }

    ConfirmDialog {
        id: downloadBlocked
        title: "This can't be downloaded yet"
        body: ""
        confirmText: "Open plugins"
        cancelText: "Close"
        onConfirmed: mainWindow.openSettings("Plugins")

        function openWith(reason) {
            body = String(reason || "")
                   || "No enabled plugin can fetch this. Install or turn one on in "
                      + "Settings → Plugins."
            open()
        }
    }

    Connections {
        target: typeof searchVM !== "undefined" ? searchVM : null
        function onDownload_blocked(uid, reason) { downloadBlocked.openWith(reason) }
    }

    Connections {
        target: typeof importVM !== "undefined" ? importVM : null
        function onDownload_blocked(uid, reason) { downloadBlocked.openWith(reason) }
    }

    // ---------------------------------------------------------------- plugins

    // A plugin's terms, wherever turning it on was asked from. The backend
    // hands over the text it will record agreement to, and only Agree answers.
    ConsentDialog {
        id: consentDialog
        objectName: "consentDialog"
        onAgreed: function (pluginId) { pluginsVM.acceptConsent(pluginId) }
    }

    Connections {
        target: typeof pluginsVM !== "undefined" ? pluginsVM : null
        function onConsent_required(pluginId, name, text) {
            consentDialog.openFor(pluginId, name, text)
        }
        function onPlugin_crashed(name, reason) {
            pluginNotice.title = name + " stopped"
            pluginNotice.body = String(reason || "") + "\n\nIt will be started again "
                + "the next time it is needed. If it keeps stopping, it can be "
                + "turned off in Settings → Plugins."
            pluginNotice.open()
        }
    }

    ConfirmDialog {
        id: pluginNotice
        confirmText: "Open plugins"
        cancelText: "Close"
        onConfirmed: mainWindow.openSettings("Plugins")
    }

    // Once, for a library from before plugins: it holds ids that nothing
    // enabled understands, which reads as features having vanished. Nothing
    // has — the ids are all still there — and this says so, and where to go.
    ConfirmDialog {
        id: upgradeNotice
        objectName: "pluginUpgradeNotice"
        title: typeof pluginsVM !== "undefined" ? pluginsVM.notice : ""
        body: typeof pluginsVM !== "undefined" ? pluginsVM.noticeDetail : ""
        confirmText: "Open plugins"
        cancelText: "Not now"
        onConfirmed: {
            pluginsVM.dismissNotice()
            mainWindow.openSettings("Plugins")
        }
        onClosed: if (typeof pluginsVM !== "undefined" && pluginsVM.notice !== "")
                      pluginsVM.dismissNotice()
    }

    Timer {
        interval: 1500
        running: typeof pluginsVM !== "undefined" && pluginsVM.notice !== ""
        repeat: false
        onTriggered: upgradeNotice.open()
    }

    // ------------------------------------------------------------- an update

    // Whether the backup dialog was opened on the way to an update, so closing
    // it should bring the update back. Cleared when the backup dialog closes.
    property bool updateAfterBackup: false

    // One shared dialog keeps Settings and update prompts in their own context.
    function openBackup(thenUpdate) {
        updateAfterBackup = !!thenUpdate
        backupDialog.openDialog()
    }

    BackupDialog {
        id: backupDialog
        objectName: "backupDialog"
        onNotify: function (message, isError) { backupToast.show(message, isError) }
        onClosed: {
            if (!mainWindow.updateAfterBackup)
                return
            mainWindow.updateAfterBackup = false
            if (typeof updatesVM !== "undefined" && updatesVM.updateAvailable)
                updateDialog.openDialog()
        }
    }

    Toast { id: backupToast }

    // Told once, with the whole decision in one card: what the release is, the
    // advice to back up first, and a button that downloads, installs and
    // restarts. A toast is the wrong shape for something the user is meant to
    // act on, and Settings → System is a page nobody opens to find out their
    // build is old — though it offers the same button for anyone who does.
    UpdateDialog {
        id: updateDialog
        onBackupRequested: mainWindow.openBackup(true)
    }

    Connections {
        target: typeof updatesVM !== "undefined" ? updatesVM : null
        function onUpdate_available(version) { updateDialog.openDialog() }
    }

    // An update the previous launch handed to the installer did not take. Said
    // once the window has settled, like the plugin notice above, rather than
    // left for the user to find in Settings.
    Timer {
        interval: 1500
        running: typeof updatesVM !== "undefined" && updatesVM.lastUpdateFailed
        repeat: false
        onTriggered: updateDialog.openDialog()
    }

    // Not in Component.onCompleted: the backend decides whether this runs at
    // all — the preference, and a 24 h gate that keeps a shared-IP office
    // inside GitHub's anonymous rate limit — but *when* is the interface's to
    // choose, and it must not be while the window is still drawing its first
    // frame. Four seconds is well past that and long before anyone has read
    // anything.
    Timer {
        interval: 4000
        running: typeof updatesVM !== "undefined"
        repeat: false
        onTriggered: updatesVM.checkOnStartup()
    }

    // ------------------------------------------------------------ window edges

    ResizeGrips {
        targetWindow: mainWindow
    }

    // ------------------------------------------------------------------ session

    Component.onCompleted: {
        navigationHistory.push(currentRoute())
        if (settingsVM.sessionValue("window.maximized", false))
            showMaximized()
    }

    // Where the window was, not how the user likes it — so `setSessionValue`,
    // into session.json. Keys are scoped to this GUI package, so they never
    // collide with the ones kroma writes under the same names.
    onClosing: function (close) {
        var maximized = visibility === Window.Maximized
        settingsVM.setSessionValue("window.maximized", maximized)
        if (!maximized) {
            settingsVM.setSessionValue("window.width", Math.round(width))
            settingsVM.setSessionValue("window.height", Math.round(height))
        }
        settingsVM.setSessionValue("window.infoOpen", infoOpen)
        settingsVM.setSessionValue("window.infoWidth", Math.round(infoWidth))
        settingsVM.setSessionValue("window.sidebarWidth", Math.round(nav.expandedWidth))
        settingsVM.setSessionValue("window.sidebarCollapsed", nav.collapsed)
        settingsVM.sync()
    }
}
