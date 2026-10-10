import QtQuick
import QtQuick.Controls
import QtQuick.Window
import Nocturne
import "components"
import "views"

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

    // ------------------------------------------------------------------ state

    // Indices 6 to 10 are the hidden pages — the backing-track workbench, the
    // track editor, the import page, an artist, and the artist editor — which
    // have no permanent rail slot. Clamped on restore to the last permanent view
    // so a session that ended on one does not reopen on a page with nothing
    // selected in the rail and nothing loaded to show.
    property int viewIndex: Math.min(Theme.prefs.navIndex, 5)
    // Restored from session.json alongside the geometry it belongs with. It
    // used to be stored twice — here and as a Theme pref — and only the Theme
    // copy was ever read back, so the two could disagree without anything
    // noticing.
    property bool infoOpen: settingsVM.sessionValue("window.infoOpen", false)
    property real infoWidth: settingsVM.sessionValue("window.infoWidth", 320)

    readonly property real infoEffectiveWidth:
        Math.max(240, Math.min(infoWidth, width - Theme.navWidth - 380))

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

    // Open one track in the editor. Single-track by design — every caller has
    // already established there is exactly one, see TrackTable's Edit entry.
    function editTrack(id) {
        if (!id || id <= 0)
            return
        editVM.load(id)
        go(7)
    }

    // Open one artist's page. The counterpart of editTrack, and reached the same
    // way: from a search result, from the artist name on a track row, from the
    // details panel, or from the Library page's artists face. Still not from the
    // rail — the Library is where you browse the collection, by track or by
    // artist, and a second top-level destination for the same thing would only
    // ask the user to remember which one they wanted.
    function openArtist(id) {
        if (!id || id <= 0)
            return
        artistVM.load(id)
        artistOpen = true
        go(9)
    }

    // Open the artist currently on the artist page for editing. Split out from
    // openArtist because looking at an artist and correcting one are different
    // intents, and the second is where the destructive action lives.
    function editArtist(id) {
        if (!id || id <= 0)
            return
        if (artistVM.artistId !== id)
            artistVM.load(id)
        artistOpen = true
        go(10)
    }

    // One album, in its running order. Reached from the Library page's albums
    // face or from something that named the album — never from the rail, for
    // the same reason the artist page is not: the Library is where you browse
    // the collection, and a second top-level destination for the same thing
    // would only ask which one you wanted.
    //
    // Two view models to load, not one: `albumVM` is the editor's staged copy
    // and drives the hero, while `albumTracksVM` holds the running order. They
    // are separate because one is edited and the other is dragged.
    function openAlbum(id) {
        if (!id || id <= 0)
            return
        albumVM.load(id)
        albumTracksVM.load(id)
        albumOpen = true
        go(11)
    }

    function editAlbum(id) {
        if (!id || id <= 0)
            return
        if (albumVM.albumId !== id)
            albumVM.load(id)
        albumOpen = true
        go(12)
    }

    // Start an import from a source chosen somewhere else — the Library header
    // is the only such place today. The page is put up first and raises its own
    // dialog, so the file chooser is answered against the page that will show
    // the result rather than the one you happened to be standing on.
    function startImport(kind) {
        go(8)
        importPage.chooseSource(kind)
    }

    function go(index) {
        // Going where you already are is not a view change, and must not be
        // treated as one: the clear below would land with no binding change to
        // make the page report its selection back.
        if (index === viewIndex)
            return

        if (hiddenViews.indexOf(viewIndex) < 0)
            previousViewIndex = viewIndex

        // The details panel belongs to whichever view is on screen: what is
        // selected there wins, and nothing selected there means the playing
        // track. Only a TrackTable ever reports a selection, so leaving the
        // old one in place would strand it on a page that has no track list at
        // all — Now Playing, Settings, the playlist grid — and the panel would
        // keep showing the last row clicked somewhere else. Clearing here is
        // what makes "nothing is selected on this page" true by default.
        //
        // Views that do have a list re-report theirs from TrackTable's
        // onActiveChanged, which the assignment below triggers synchronously,
        // so the panel is never painted in the intervening empty state.
        sidebarVM.setSelection([])
        viewIndex = index
        Theme.prefs.navIndex = index
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
    // the transport and the details panel — are opaque to the backdrop and
    // swallow their own strays, each with a sink of its own at the bottom of
    // its stack. The navigation rail deliberately has none: it is background
    // like the rest.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: TrackSelection.clear()
    }

    // ------------------------------------------------------------------ chrome

    TitleBar {
        id: titleBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        targetWindow: mainWindow
        caption: playerVM.has_current_track
                 ? "— " + playerVM.current_track_title : ""
    }

    NavRail {
        id: nav
        objectName: "navRail"
        anchors.left: parent.left
        anchors.top: titleBar.bottom
        anchors.bottom: player.top
        currentIndex: mainWindow.viewIndex
        // Present while you are on the workbench, and for as long as it still
        // holds jobs afterwards — so you can step away mid-batch and come back
        // to it without going through the mic button again. Clearing the list
        // is what makes it go away.
        showBacking: mainWindow.viewIndex === 6 || backingVM.jobCount > 0
        // The editor keeps its slot only while there is something to lose, so
        // you can go look at the track list mid-edit and find your way back.
        // A saved track is no longer being edited, and the slot goes with it.
        showEdit: mainWindow.viewIndex === 7 || editVM.isDirty
        // Same bargain for the import page: present while you are on it, and
        // afterwards for as long as there is a scan running or a list of
        // matches still waiting to be dealt with. Clear empties that list,
        // which is what takes the slot away.
        showImport: mainWindow.viewIndex === 8 || importVM.hasResults
                    || importVM.isAnalyzing || importVM.isSearching
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
    }

    // ------------------------------------------------------------------ views

    FocusScope {
        id: content
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
                onImportRequested: function (kind) { mainWindow.startImport(kind) }
                onOpenArtistRequested: function (id) { mainWindow.openArtist(id) }
                onOpenAlbumRequested: function (id) { mainWindow.openAlbum(id) }
                // A backup the update dialog sent the user to has finished, or
                // been put away: the update is still waiting, so bring it back.
                onBackupClosed: {
                    if (!mainWindow.updateAfterBackup)
                        return
                    mainWindow.updateAfterBackup = false
                    if (typeof updatesVM !== "undefined" && updatesVM.updateAvailable)
                        updateDialog.openDialog()
                }
                Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            }
            SettingsView {
                id: settingsView
                anchors.fill: parent
                active: mainWindow.viewIndex === 5
                opacity: active ? 1 : 0
                visible: opacity > 0
                onBackupRequested: mainWindow.openBackup(true)
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
            // Not in the rail either. You get here by choosing a source in the
            // Library header, or by pairing a web result with a local file.
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
        anchors.right: parent.right
        anchors.top: titleBar.bottom
        anchors.bottom: player.top
        width: mainWindow.infoOpen ? mainWindow.infoEffectiveWidth : 0
        open: mainWindow.infoOpen
        clip: true
        onEditRequested: function (id) { mainWindow.editTrack(id) }

        Behavior on width {
            NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
        }
    }

    // Drag handle for the details panel.
    MouseArea {
        anchors.right: infoPanel.left
        anchors.top: infoPanel.top
        anchors.bottom: infoPanel.bottom
        width: 6
        visible: mainWindow.infoOpen
        cursorShape: Qt.SizeHorCursor
        property real pressX: 0
        property real startWidth: 0
        onPressed: function (event) {
            pressX = mapToItem(mainWindow.contentItem, event.x, 0).x
            startWidth = mainWindow.infoWidth
        }
        onPositionChanged: function (event) {
            if (!pressed)
                return
            var nowX = mapToItem(mainWindow.contentItem, event.x, 0).x
            mainWindow.infoWidth = Math.max(240, Math.min(520, startWidth - (nowX - pressX)))
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
        sequence: "Ctrl+F"
        onActivated: mainWindow.go(2)
    }
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
    // it should bring the update back. Set by openBackup(true), cleared by the
    // Library page's backupClosed.
    property bool updateAfterBackup: false

    // The backup dialog, wherever it was asked for. It lives on the Library
    // page — one instance, so its toasts are not doubled — and this is how
    // anywhere else reaches it.
    function openBackup(thenUpdate) {
        updateAfterBackup = !!thenUpdate
        go(4)
        libraryPage.openBackup()
    }

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
        settingsVM.sync()
    }
}
