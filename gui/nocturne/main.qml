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

    // Restore the main collection destinations or Home (13). Pages opened
    // inside a tab need a loaded entity, so what is saved is the tab's root —
    // see navigate() — and the work pages need an import session, so they are
    // not reopened from only a saved index.
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

    // Where finishing on a workbench returns to. Never a workbench itself.
    property int previousViewIndex: 0
    readonly property var workbenchViews: [6, 8]

    // ---------------------------------------------------------------- tabs
    //
    // Every rail entry is a tab, and a tab can open pages inside itself: Songs
    // the track editor, Artists an artist and then its editor, Albums an album
    // and then its editor, Playlists a playlist. Whatever you open lands in the
    // tab it belongs to, wherever you asked for it from, and the rail lights
    // that tab. The back arrow on such a page goes up one level inside the
    // tab; the title bar's history is how you get back to anything else.
    //
    // The pages keep their own view indices, so each stays alive behind the
    // others with its state and scroll position. This says which tab each one
    // belongs to — a root view, the Library face, and how deep it sits. The
    // playlist sits inside PlaylistsView rather than in the stack, one level
    // into view 3.
    readonly property var tabPages: ({
        7:  { root: 4, mode: 0, depth: 1 },
        9:  { root: 4, mode: 1, depth: 1 },
        10: { root: 4, mode: 1, depth: 2 },
        11: { root: 4, mode: 2, depth: 1 },
        12: { root: 4, mode: 2, depth: 2 }
    })

    function tabRootOf(view) {
        var page = tabPages[view]
        return page ? page.root : view
    }
    // A route's tab. View 4 is three tabs, told apart by the Library face.
    function tabKeyOf(route) {
        var page = tabPages[route.view]
        if (page) return page.root + ":" + page.mode
        if (route.view !== 4) return String(route.view)
        return "4:" + (route.libraryMode !== undefined ? route.libraryMode
                                                        : Theme.prefs.libraryMode)
    }
    function depthOf(route) {
        var page = tabPages[route.view]
        if (page) return page.depth
        if (route.view !== 3) return 0
        var playlist = route.entityId !== undefined ? route.entityId : playlistsPage.openId
        return playlist > 0 ? 1 : 0
    }

    // The page each Library tab was last left on, so going back to the tab
    // finds it as you left it — as the Playlists tab always has, by keeping
    // its open playlist. A view index only: the entity is whichever one the
    // tab's shared view model has loaded. Session-only.
    property var tabTops: ({})

    // Which way the navigation in progress goes: +1 deeper into a tab, -1 back
    // up it, 0 to another tab. Set before the page changes — see StackSlide.
    property int navDirection: 0

    function pageLoaded(view) {
        if (view === 7) return editVM.hasTrack
        if (view === 9 || view === 10) return artistVM.hasArtist
        if (view === 11 || view === 12) return albumVM.hasAlbum
        return true
    }

    // What the rail and the tab shortcuts call. Choosing the tab you are on
    // takes you to its root, the way a tab bar does; choosing another returns
    // to the page you left it on, if that page still has something to show.
    function selectTab(view, mode) {
        if (view === 4 && mode === undefined) mode = Theme.prefs.libraryMode
        var root = { view: view, entityId: 0 }
        if (view === 4) root.libraryMode = mode
        var key = tabKeyOf(root)
        if (key === tabKeyOf({ view: viewIndex })) {
            navigate(root)
        } else if (view === 3) {
            go(3)
        } else {
            var top = tabTops[key]
            if (top !== undefined && top !== view && pageLoaded(top)) go(top)
            else navigate(root)
        }
    }

    // The back arrow on a page inside a tab: one level up, in the same tab.
    function goUp(view) {
        if (view === 3) navigate({ view: 3, entityId: 0 })
        else if (view === 10 && artistVM.hasArtist) go(9)
        else if (view === 12 && albumVM.hasAlbum) go(11)
        else if (tabPages[view]) goLibrary(tabPages[view].mode)
    }

    // An editor's back arrow, which is leaving its edits behind: with nothing
    // in the tab to come back to, staged changes would be stranded in the
    // view model. Ask first. Moving to another tab, or anywhere through
    // history, keeps them — the tab keeps its editor until you come back.
    function leaveEditor(view) {
        var dirty = view === 7 ? editVM.isDirty
                  : view === 10 ? artistVM.isDirty
                  : view === 12 ? albumVM.isDirty : false
        if (!dirty) {
            goUp(view)
            return
        }
        discardEdits.pendingView = view
        discardEdits.open()
    }

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
    function openPlaylist(id) { if (id > 0) navigate({view: 3, entityId: id}) }

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
        } else if (viewIndex === 3 && playlistsPage.openId > 0) {
            route.entityId = playlistsPage.openId
            route.label = "Playlist · " + playlistTracksVM.playlistName
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
        if (index === 3) route.entityId = Math.max(0, playlistsPage.openId)
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
        var oldPlaylistId = Math.max(0, playlistsPage.openId)
        var playlistChange = route.view === 3 && route.entityId !== undefined
            && oldPlaylistId !== entityId
        if (historyIndex === undefined && route.view === viewIndex
            && !trackChange && !artistChange && !albumChange && !playlistChange
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
        var oldTab = tabKeyOf(oldRoute)
        var oldDepth = depthOf(oldRoute)
        var oldTrackId = editVM.trackId
        var oldArtistId = artistVM.artistId
        var oldAlbumId = albumVM.albumId
        if (trackChange) editVM.load(entityId)
        if (artistChange) artistVM.load(entityId)
        if (albumChange) albumVM.load(entityId)
        if (playlistChange && entityId > 0) playlistTracksVM.load(entityId)
        if ((route.view === 7 && !editVM.hasTrack)
            || ((route.view === 9 || route.view === 10) && !artistVM.hasArtist)
            || ((route.view === 11 || route.view === 12) && !albumVM.hasAlbum)
            || (playlistChange && entityId > 0 && !playlistTracksVM.hasPlaylist)) {
            // A removed history item must not empty the profile we were on.
            if (trackChange && oldTrackId > 0) editVM.load(oldTrackId)
            if (artistChange && oldArtistId > 0) artistVM.load(oldArtistId)
            if (albumChange && oldAlbumId > 0) albumVM.load(oldAlbumId)
            if (playlistChange && entityId > 0 && oldPlaylistId > 0)
                playlistTracksVM.load(oldPlaylistId)
            navigationToast.show("This item is no longer in your library", true)
            return
        }
        if ((route.view === 11 || route.view === 12) && albumTracksVM.albumId !== entityId)
            albumTracksVM.load(entityId)
        if (route.view === 4 && route.libraryMode !== undefined)
            Theme.prefs.libraryMode = route.libraryMode
        if (route.view === 2 && route.query !== undefined)
            browsePage.restoreQuery(route.query, route.source, route.mode)

        var newTab = tabKeyOf(route)
        var newDepth = depthOf(route)
        navDirection = newTab !== oldTab ? 0
                     : newDepth > oldDepth ? 1 : newDepth < oldDepth ? -1 : 0
        playlistsPage.stackDirection = navDirection
        if (playlistChange) {
            if (entityId > 0) playlistsPage.open(entityId)
            else playlistsPage.back()
        }
        if (route.view !== viewIndex) {
            if (workbenchViews.indexOf(viewIndex) < 0) previousViewIndex = viewIndex
            sidebarVM.setSelection([])
            viewIndex = route.view
            // A page inside a tab is saved as the tab: it needs an entity to
            // show, and only the root can be restored from a number.
            Theme.prefs.navIndex = tabRootOf(viewIndex)
        }
        // After the switch, so the Library behind the page — no longer the
        // one on screen — changes face without refreshing a wall nobody sees.
        var page = tabPages[route.view]
        if (page) Theme.prefs.libraryMode = page.mode
        if (route.view === 4 || page) tabTops[newTab] = route.view
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
    ConfirmDialog {
        id: discardEdits
        objectName: "discardEditsDialog"
        property int pendingView: -1
        title: "Discard unsaved edits?"
        body: "Your changes haven't been saved. Going back throws them away."
        confirmText: "Discard"
        cancelText: "Keep editing"
        danger: true
        onConfirmed: {
            if (pendingView === 7) editVM.revert()
            else if (pendingView === 10) artistVM.revert()
            else if (pendingView === 12) albumVM.revert()
            mainWindow.goUp(pendingView)
        }
    }
    Toast { id: navigationToast }
    Connections {
        target: pinsVM
        function onError(message) { navigationToast.show(message, true) }
    }

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
        // The tab, not the page: an artist lights Artists, an editor its tab.
        currentIndex: mainWindow.tabRootOf(mainWindow.viewIndex)
        libraryMode: Theme.prefs.libraryMode
        reducedMotion: Theme.prefs.reducedMotion
        expandedWidth: settingsVM.sessionValue("window.sidebarWidth", 240)
        collapsed: settingsVM.sessionValue("window.sidebarCollapsed", false)
        maximumWidth: Math.min(360, mainWindow.width - 380
            - (mainWindow.infoOpen ? 240 : 0))
        onNavigated: function (index) { mainWindow.selectTab(index) }
        onLibraryNavigated: function (mode) { mainWindow.selectTab(4, mode) }
        pinModel: pinsVM
        activePinType: mainWindow.viewIndex === 9 || mainWindow.viewIndex === 10 ? "artist"
            : mainWindow.viewIndex === 11 || mainWindow.viewIndex === 12 ? "album"
            : mainWindow.viewIndex === 3 ? "playlist" : ""
        activePinId: activePinType === "artist" ? artistVM.artistId
            : activePinType === "album" ? albumVM.albumId
            : activePinType === "playlist" ? playlistsPage.openId : -1
        onPinActivated: function(kind, id) {
            if (kind === "track") playerVM.playEntries([{id: id}])
            else if (kind === "artist") mainWindow.openArtist(id)
            else if (kind === "album") mainWindow.openAlbum(id)
            else if (kind === "playlist") mainWindow.openPlaylist(id)
        }
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
        // only the current one is visible, cross-fading on change. Each is sized
        // by SizeWhileVisible rather than anchored, so a sidebar drag or window
        // resize relays out the page on screen and not every page behind it.
        Item {
            id: stack
            anchors.fill: parent

            Item {
                id: homePage
                objectName: "homeView"
                visible: mainWindow.viewIndex === 13
            }
            SizeWhileVisible { target: homePage; width: stack.width; height: stack.height }

            NowPlayingView {
                id: nowPlayingPage
                active: mainWindow.viewIndex === 0
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDetailsRequested: mainWindow.infoOpen = true
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SizeWhileVisible { target: nowPlayingPage; width: stack.width; height: stack.height }
            QueueView {
                id: queuePage
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
            SizeWhileVisible { target: queuePage; width: stack.width; height: stack.height }
            BrowseView {
                id: browsePage
                globalSearch: true
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
            SizeWhileVisible { target: browsePage; width: stack.width; height: stack.height }
            PlaylistsView {
                id: playlistsPage
                objectName: "playlistsView"
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 3
                opacity: active ? 1 : 0
                visible: opacity > 0
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onOpenPlaylistRequested: function (id) { mainWindow.openPlaylist(id) }
                onDone: mainWindow.goUp(3)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SizeWhileVisible { target: playlistsPage; width: stack.width; height: stack.height }
            LibraryView {
                id: libraryPage
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 4
                opacity: active ? 1 : 0
                visible: opacity > 0
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onEditArtistRequested: function(id) { mainWindow.editArtist(id) }
                onEditAlbumRequested: function(id) { mainWindow.editAlbum(id) }
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onOpenAlbumRequested: function (id) { mainWindow.openAlbum(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                transform: StackSlide { current: libraryPage.active; direction: mainWindow.navDirection }
            }
            SizeWhileVisible { target: libraryPage; width: stack.width; height: stack.height }
            SettingsView {
                id: settingsView
                active: mainWindow.viewIndex === 5
                opacity: active ? 1 : 0
                visible: opacity > 0
                onBackupRequested: function (thenUpdate) { mainWindow.openBackup(thenUpdate) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SizeWhileVisible { target: settingsView; width: stack.width; height: stack.height }
            // Not in the rail. You get here by asking for a backing track that
            // does not exist yet — from the transport's mic button or a track's
            // context menu — and not otherwise. It is a workbench for one job,
            // not somewhere to browse to.
            BackingTrackView {
                id: backingPage
                active: mainWindow.viewIndex === 6
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.go(mainWindow.previousViewIndex)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SizeWhileVisible { target: backingPage; width: stack.width; height: stack.height }
            // The Tools destination also opens when a source is chosen in the
            // Library header, or a web result is paired with a local file.
            ImportView {
                id: importPage
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 8
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.go(mainWindow.previousViewIndex)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SizeWhileVisible { target: importPage; width: stack.width; height: stack.height }
            // Pages opened inside a tab, from here down. Each is declared after
            // the page it opens from, so the deeper page is the one painted on
            // top while the two cross over.
            //
            // The Songs tab's editor. You get here by choosing Edit on a single
            // track, from any list that shows one.
            TrackEditView {
                id: trackEditPage
                objectName: "trackEditView"
                active: mainWindow.viewIndex === 7
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.leaveEditor(7)
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onOpenAlbumRequested: function (id) { mainWindow.openAlbum(id) }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                transform: StackSlide { current: trackEditPage.active; direction: mainWindow.navDirection }
            }
            SizeWhileVisible { target: trackEditPage; width: stack.width; height: stack.height }
            // One artist's catalogue, inside the Artists tab. You arrive from the
            // wall, a search result, a track's artist name, or the details panel
            // — always from something that mentioned the artist.
            ArtistView {
                id: artistPage
                objectName: "artistView"
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 9
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.goUp(9)
                onEditArtistRequested: function (id) { mainWindow.editArtist(id) }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                transform: StackSlide { current: artistPage.active; direction: mainWindow.navDirection }
            }
            SizeWhileVisible { target: artistPage; width: stack.width; height: stack.height }
            // One album's running order, inside the Albums tab. Reached from the
            // albums wall, or from anything that named the album.
            AlbumView {
                id: albumPage
                objectName: "albumView"
                columnLayouts: trackColumns
                active: mainWindow.viewIndex === 11
                opacity: active ? 1 : 0
                visible: opacity > 0
                onDone: mainWindow.goUp(11)
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onEditAlbumRequested: function (id) { mainWindow.editAlbum(id) }
                onEditTrackRequested: function (id) { mainWindow.editTrack(id) }
                onGenerateBackingRequested: function (ids) {
                    mainWindow.generateBackingFor(ids)
                }
                onImportPageRequested: mainWindow.go(8)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                transform: StackSlide { current: albumPage.active; direction: mainWindow.navDirection }
            }
            SizeWhileVisible { target: albumPage; width: stack.width; height: stack.height }
            ArtistEditView {
                id: artistEditPage
                objectName: "artistEditView"
                active: mainWindow.viewIndex === 10
                opacity: active ? 1 : 0
                visible: opacity > 0
                // Up to the artist's page. A split leaves no artist to go back
                // to, so that lands on the Artists wall instead — see goUp().
                onDone: mainWindow.leaveEditor(10)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                transform: StackSlide { current: artistEditPage.active; direction: mainWindow.navDirection }
            }
            SizeWhileVisible { target: artistEditPage; width: stack.width; height: stack.height }
            AlbumEditView {
                id: albumEditPage
                objectName: "albumEditView"
                active: mainWindow.viewIndex === 12
                opacity: active ? 1 : 0
                visible: opacity > 0
                // Up to the album's page, or the Albums wall if it is gone.
                onDone: mainWindow.leaveEditor(12)
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                transform: StackSlide { current: albumEditPage.active; direction: mainWindow.navDirection }
            }
            SizeWhileVisible { target: albumEditPage; width: stack.width; height: stack.height }
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

    // Either edge moving, for anything that should hold its size until it
    // stops rather than rebuild on every frame of the drag.
    Binding {
        target: LiveResize
        property: "active"
        value: nav.moving || infoResize.dragging || infoResize.animating
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
    Shortcut { sequence: "Ctrl+1"; onActivated: mainWindow.selectTab(0) }
    Shortcut { sequence: "Ctrl+2"; onActivated: mainWindow.selectTab(1) }
    Shortcut { sequence: "Ctrl+3"; onActivated: mainWindow.selectTab(2) }
    Shortcut { sequence: "Ctrl+4"; onActivated: mainWindow.selectTab(3) }
    Shortcut { sequence: "Ctrl+5"; onActivated: mainWindow.selectTab(4) }
    Shortcut { sequence: "Ctrl+6"; onActivated: mainWindow.selectTab(5) }
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
