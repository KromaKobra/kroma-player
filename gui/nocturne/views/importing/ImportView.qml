import QtQuick
import QtQuick.Dialogs
import Nocturne
import "../../components/controls"
import "../../components/dialogs"
import "../../components/importing"
import "../../components/tracks"

// Getting music in: local files, a folder, whatever an enabled plugin can turn
// into tracks, or a CSV of them — into one list, in any combination. A row is fetched or filed by what
// it is, not by what the page was last asked to do, so a folder and a CSV can
// be gathered up and taken in a single press.
//
// The Import tab, under Tools in the rail. You also arrive by choosing a source
// on the Library page, or by pairing a web result with a file you already
// have. Import used to live inside the Library page and swap that page's track
// list out from under it — a whole feature borrowing another one's surface. It
// is its own destination now, so neither has to make room for the other.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // What the claim offer is about, held between the signal and the answer —
    // the same thing TrackTable does with `_menuTrackId` for its delete
    // confirmation. The signal is the only place these are known, and the
    // dialog is answered a moment later.
    property int _claimAlbumId: -1
    property string _claimAlbumName: ""
    property var _claimTrackIds: []
    property var _claimTitles: []

    // The plugin import source a dialog is open for, by key — held from the
    // menu to the answer, the way `_claimAlbumId` is held above.
    property string _pluginSourceKey: ""

    // Back out to wherever the user came from.
    signal done()

    // Every source, raised from here and from the Library header alike — which
    // is why the dialogs live on this page and the caller only names a kind.
    // Anything unrecognised falls through to files, the common case.
    //
    // This is the other half of adding a source: the catalog in
    // ImportSources.js decides what is offered and what it is called, and this
    // decides what happens when it is picked. Nothing else has to change.
    function chooseSource(kind) {
        if (kind === "folder")
            folderDialog.open()
        else if (kind === "csv")
            csvDialog.open()
        else if (String(kind).indexOf("plugin:") === 0)
            root.choosePluginSource(String(kind).slice("plugin:".length))
        else
            fileDialog.open()
    }

    // A plugin's source, raised from its own entry: a line of text with its
    // prompt, or a file matching its filters. Nothing here knows what the text
    // means — that is the plugin's, and `importFromSource` hands it over.
    function choosePluginSource(key) {
        var sources = typeof pluginsVM !== "undefined" && pluginsVM ? pluginsVM.importSources : []
        var entry = null
        for (var i = 0; i < sources.length; ++i) {
            if (sources[i].key === key) { entry = sources[i]; break }
        }
        if (!entry)
            return
        root._pluginSourceKey = key
        if (entry.input === "file") {
            var filters = (entry.filters || []).slice()
            filters.push("All files (*)")
            pluginFileDialog.title = entry.label
            pluginFileDialog.nameFilters = filters
            pluginFileDialog.open()
        } else {
            linkDialog.openFor(entry.label, "Add", entry.iconName || "external",
                               entry.prompt || "")
        }
    }

    Connections {
        target: importVM

        // One card, always — a scan of nothing but well-tagged Opus is still
        // worth seeing before it runs, and it is the only place the user is
        // told what the folder actually held. The backend computes every figure
        // on it, so the card is handed its opening state rather than working
        // one out.
        function onAnalysis_ready() {
            importOptions.openFor(
                importVM.importPreview(importVM.filenameFallback,
                                       importVM.excludedFormats),
                importVM.filenameFallback,
                importVM.excludedFormats)
        }
        function onError_occurred(message) {
            toast.show(message, true)
        }
        // The run's own numbers, not the list's: imports accumulate, so
        // foundCount is every match on the page and would read as more matches
        // than the searchTotal it is quoted against.
        //
        // "tracks" in download mode, because a CSV row and a pasted link are
        // not files and never were — counting them as such was always wrong,
        // and a link makes it obvious ("matched 1 of 1 files" for something
        // with no file anywhere near it).
        //
        // A run that asked no plugin anything — none can identify — matched
        // nothing and failed to match nothing, so it says how many it listed
        // instead of "matched 0 of 12".
        function onSearch_completed() {
            var noun = importVM.isDownloadMode ? "track" : "file"
            if (importVM.searchTotal !== 1)
                noun += "s"
            var msg = importVM.lastRunIdentified
                      ? "Matched " + importVM.lastMatchedCount + " of "
                        + importVM.searchTotal + " " + noun
                      : "Listed " + importVM.searchTotal + " " + noun
            if (importVM.duplicatesSkipped > 0)
                msg += " · " + importVM.duplicatesSkipped + " already listed"
            // A different fact from the one above: that one is about this list,
            // this one is about the library. Said whichever way the setting is
            // — it is true either way — but with the setting on it is the only
            // place the number is said at all, because those rows never landed.
            if (importVM.lastInDatabaseCount > 0)
                msg += " · " + importVM.lastInDatabaseCount + " already in your library"
            toast.show(msg, false)
        }
        function onStash_completed(uid, trackId) {
            databaseVM.refresh()
        }
        // The listing found tracks you already own that are not on the record
        // it came from — a song downloaded off a single years ago. The run has
        // just proved it belongs here, so offer to file it here too.
        //
        // The question lands on this page because the listing did: the album
        // page went out of the way the moment "Find tracks" was pressed.
        function onAlbum_claim_offered(albumId, albumName, trackIds, titles) {
            root._claimAlbumId = albumId
            root._claimAlbumName = albumName
            root._claimTrackIds = trackIds
            root._claimTitles = titles
            claimDialog.open()
        }
        // A row that finished is taken off the list when the setting says to,
        // and that is a removal the table would otherwise animate like any
        // other. It is not one: nobody threw it out, it simply finished. Naming
        // it here is what lets the table leave it where it sits and only fade
        // it — see `markAdvanceRemoved`, and QueueView, which does the same for
        // a track that played through.
        function onImported_removed(uid) {
            resultsTable.markAdvanceRemoved(uid)
        }
        // Show the user what just landed. Imports accumulate and the list keeps
        // its scroll position when they do, so without this a second folder —
        // or a pasted link added to a list already gathered — arrives entirely
        // below the fold, announced by nothing but a count in a toast.
        //
        // Only an append onto rows that were already there. A first batch has
        // nothing to be told apart from, and the list is at the top already, so
        // both halves of this would be spent saying nothing.
        function onRows_appended(first, count) {
            if (first > 0)
                resultsTable.revealAdded(first, count)
        }
    }

    readonly property bool importBusy: importVM.isAnalyzing || importVM.isSearching
    readonly property bool importActive: importBusy || importVM.hasResults

    // What the list is made of, asked of the rows still to be saved — the same
    // two questions "Import all" asks before it queues anything. A list can
    // hold both kinds now, so nothing about it can be read off the run.
    readonly property bool onlyDownloads:
        importVM.pendingDownloadCount > 0 && importVM.pendingStashCount === 0
    readonly property bool mixedKinds:
        importVM.pendingDownloadCount > 0 && importVM.pendingStashCount > 0
    // Whether the Import menu holds anything beyond files and a folder.
    readonly property bool pluginSourcesOffered:
        typeof pluginsVM !== "undefined" && pluginsVM
        && (pluginsVM.importSources.length > 0 || pluginsVM.hasAudioSource)

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Import"
        // What is happening, or — once nothing is — what is waiting. The two
        // behave differently enough to be worth naming: a downloading row is
        // fetched, a local file is stashed. Named by what happens rather than
        // by which source asked for it, because a CSV and a pasted link take
        // the same path once the rows exist.
        //
        // While a run is going the run answers, because that is the thing with
        // a progress bar under it. Once it is done the *list* answers, and a
        // list gathered from several sources says so.
        subtitle: root.importBusy
                  ? (importVM.isDownloadMode ? "Fetching online"
                                             : "From your own files")
                  : !root.importActive
                    ? (root.pluginSourcesOffered
                       ? "Bring in files, a folder, or tracks from a plugin"
                       : "Bring in files or a folder")
                    : root.mixedKinds ? "Your own files and downloads"
                      : root.onlyDownloads ? "Fetching online"
                        : "From your own files"

        // One control for every source — see ImportSources.js. The Library
        // header carries the same one, so the two pages cannot come to offer
        // different sources.
        ImportButton {
            anchors.verticalCenter: parent.verticalCenter
            enabledAction: !root.importBusy
            onSourceChosen: function (kind) { root.chooseSource(kind) }
        }

        // The way out while there is nothing to leave behind. Once a run has
        // results the rail offers Cancel or Clear in its place, so there is
        // never more than one exit on screen at a time.
        AppButton {
            anchors.verticalCenter: parent.verticalCenter
            text: "Back"
            iconName: "chevronDown"
            variant: "ghost"
            visible: !root.importActive
            onClicked: root.done()
        }
    }

    // ------------------------------------------------------------------ idle

    // What the page looks like before a source is chosen — and after cancelling
    // the file dialog you were just sent here to answer, which is the case that
    // makes this more than decoration.
    Item {
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        visible: !root.importActive

        Column {
            anchors.centerIn: parent
            spacing: Theme.gapSm

            Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                name: "stash"
                size: 34
                color: Theme.alpha(Theme.text, 0.18)
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Nothing loaded yet — choose a source above"
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }
        }
    }

    // ------------------------------------------------------------ the run

    Item {
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        visible: root.importActive

        // ------------------------------------------------------------- rail
        //
        // Everything about the import that is not a row: how much there is,
        // what it is made of, and the two things you can do to all of it.
        //
        // A column down the side rather than the bar that used to sit above the
        // list. What the user is doing here is *watching* — a match run comes
        // back a file at a time and the counts move for a minute or more — and
        // a bar puts those counts on the same axis as the list they describe,
        // so they wrap, elide and shove each other about as the list grows. Held
        // to one side at a fixed width, nothing moves but the numbers.
        Item {
            id: rail
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.leftMargin: Theme.gapXl
            // Wide enough for the longest chip and the longest button label,
            // and no wider: every pixel here is one the list does not get.
            width: 200

            // What the list is made of, once it has settled.
            //
            // Hidden while a run is going, and replaced by that run's own
            // progress below. Half-arrived counts jumping about are noise, and
            // there is nothing worth selecting until the list has stopped
            // growing.
            Column {
                id: railCounts
                anchors.top: parent.top
                anchors.topMargin: Theme.gap
                anchors.left: parent.left
                anchors.right: parent.right
                spacing: Theme.gapSm
                visible: !root.importBusy && importVM.hasResults

                // How much there is, above the breakdown of it. The one figure
                // the six chips cannot give you: they say what the rows *are*,
                // and a user who has just dragged in three folders wants to know
                // first that all 412 files arrived.
                //
                // `result_count` and not the number of rows on screen — this is
                // the size of the import, and a track hidden for being in the
                // library was still part of what was brought in. It is the total
                // the chips below add up to.
                Text {
                    width: parent.width
                    // A chip's height rather than the text's, taken from the
                    // legend so the two cannot drift: this reads as the chips'
                    // heading, and the gap under it is the gap that separates
                    // the chips from each other.
                    height: legend.chipHeight
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 10
                    text: Fmt.count(importVM.result_count, "track")
                    elide: Text.ElideRight
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    font.weight: Font.DemiBold
                    font.features: ({ "tnum": 1 })
                }

                ImportLegend {
                    id: legend
                    // Named so a test can reach it. The chips are built while
                    // this whole rail is hidden — a run has no results until it
                    // finishes — and a legend that sized itself by measuring
                    // rather than by counting stayed 0 high forever because of
                    // it. Only the loaded window shows that; nothing errors.
                    objectName: "importLegend"
                    width: parent.width
                    counts: importVM.stateCounts

                    // Selecting rather than filtering — see the note in
                    // ImportLegend. `setSelectedMultiple` reaches the table
                    // through the model's `selectionChanged`, so the rows
                    // highlight without this knowing anything about the list
                    // that draws them.
                    onStateChosen: function (state) {
                        importVM.setSelectedMultiple(importVM.uidsInState(state))
                    }
                }
            }

            // The run in flight, in the space the counts will take when it is
            // over. The two never show at once, so the rail does not change
            // height and the table below never shifts under the pointer.
            Column {
                anchors.top: parent.top
                anchors.topMargin: Theme.gap
                anchors.left: parent.left
                anchors.right: parent.right
                spacing: Theme.gapSm
                visible: root.importBusy

                Text {
                    width: parent.width
                    height: 26
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 10
                    text: importVM.isAnalyzing
                          ? "Scanning files…"
                          : "Matching " + importVM.searchProgress
                            + " / " + importVM.searchTotal
                    elide: Text.ElideRight
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    font.weight: Font.DemiBold
                    font.features: ({ "tnum": 1 })
                }

                // A track under it rather than a hairline along a card's edge,
                // because there is no card here any more and two pixels of
                // accent floating on the background would read as an artefact.
                Rectangle {
                    width: parent.width
                    height: 3
                    radius: 1.5
                    visible: importVM.isSearching && importVM.searchTotal > 0
                    color: Theme.alpha(Theme.text, 0.10)

                    Rectangle {
                        height: parent.height
                        radius: parent.radius
                        width: parent.width * (importVM.searchTotal > 0
                               ? importVM.searchProgress / importVM.searchTotal : 0)
                        color: Theme.accent
                        Behavior on width { NumberAnimation { duration: Theme.normal } }
                    }
                }
            }

            // The two things you can do to the whole list, held at the bottom
            // of the rail. Down here rather than beside the counts because they
            // are what you press when you have finished reading them, and
            // because a button under a column that grows by a chip at a time
            // would be a button that moves.
            Column {
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme.gapXl
                anchors.left: parent.left
                anchors.right: parent.right
                spacing: Theme.gapSm

                // One button with two jobs: start the run, then stop it. The
                // same control, because "stop the thing I just started" is
                // where the hand already is — and because a second button
                // sitting dead until it is needed reads as clutter.
                AppButton {
                    // Named so a test can find it: whether this button offers
                    // to start or to stop is the whole of two features, and
                    // `!undefined` is true, so a misspelt property here would
                    // simply leave it permanently offering to start.
                    objectName: "importActionButton"
                    width: parent.width

                    // The verb only narrows to "Download" when every row it
                    // would act on is one — a mixed batch is an import that
                    // happens to fetch some of what it files, and "Download
                    // all" over a folder of local files would be a lie about
                    // what the press is going to do.
                    readonly property string startText:
                        root.onlyDownloads ? "Download all" : "Import all"

                    text: !importVM.isStashing ? startText
                        : importVM.isAborting ? "Stopping…" : "Abort import"
                    iconName: !importVM.isStashing ? "download" : "x"
                    variant: importVM.isStashing ? "soft" : "filled"
                    visible: importVM.hasResults && !root.importBusy
                    // While stopping there is nothing left to ask for — the row
                    // in flight is on its way to a boundary and pressing again
                    // would not make it arrive sooner.
                    enabledAction: !importVM.isStashing || !importVM.isAborting
                    onClicked: {
                        if (importVM.isStashing)
                            importVM.abortStash()
                        // Only local rows have anything to ask about, and the
                        // count already knows that: a fetched row that matched
                        // nothing has no id to fetch and is not in it. So the
                        // number decides on its own, mixed list or not.
                        else if (importVM.unmatchedImportableCount > 0)
                            unmatchedChoice.open()
                        else
                            importVM.stashAll()
                    }
                }

                // "Clear", not "Done". It does not acknowledge anything — it
                // throws the list away, which is a thing worth being asked for
                // by name. What it *also* does is leave, and that is not a
                // second decision: the list going takes this page's slot in the
                // rail with it, so there would be nothing here to stay on.
                AppButton {
                    objectName: "importClearButton"
                    width: parent.width
                    text: root.importBusy ? "Cancel" : "Clear"
                    variant: "ghost"
                    onClicked: {
                        // Cancel stops the scan and leaves you here, because
                        // what you wanted was to pick a different source.
                        if (root.importBusy) {
                            importVM.cancelImport()
                        } else {
                            importVM.clearResults()
                            root.done()
                        }
                    }
                }
            }
        }

        // Where the rail stops and the list starts.
        //
        // Not decoration. The run's progress bar lives in the rail and is the
        // rail's width, so without a boundary a bar that ends a third of the way
        // across the page reads as a bar that has stopped rather than one that
        // has finished its column. It sits in the middle of the gutter, a full
        // page margin clear of the chips on one side and the first column on the
        // other, and it only exists while the run area does — the idle "choose a
        // source" state has no rail to divide from anything.
        Rectangle {
            id: railDivider
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.left: rail.right
            anchors.leftMargin: Theme.gap
            width: 1
            color: Theme.line
        }

        TrackTable {
            id: resultsTable
            // Named so a test can reach this list among the several the window
            // holds — see tests/test_import_clear.py, which checks that a row
            // leaving is marked here before it goes.
            objectName: "importResultsTable"
            anchors.top: parent.top
            anchors.left: rail.right
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            // The gutter between the rail and the first column is this inset,
            // and so is the page's right margin — the same figure the header
            // and the rail are held off the edges by.
            sideMargin: Theme.gapXl

            mode: "library"
            listVM: importVM
            active: root.active && root.importActive
            // One layout for every kind, and one list: the same rows and the
            // same status cell, which reads each row's own `importState` and so
            // fetches one, files the next, and says of a third that there is
            // nothing to be done with it.
            columnLayout: root.columnLayouts ? root.columnLayouts.importResults : null
            statusMode: "import"
            // An empty table under a rail counting tracks needs to say which of
            // the two things happened. Everything being in the library is not
            // "nothing matched" — it is the opposite, and it is now a state the
            // page can reach and hold, because those rows are kept.
            emptyText: root.importBusy ? ""
                     : (importVM.result_count > 0
                        && importVM.result_count === importVM.inDatabaseCount)
                       ? "Every one of these is already in your library"
                       : "Nothing matched"
            // A scan and a match run leave this panel empty for a minute or
            // more on a large folder, and an empty panel reads as a finished
            // one. Placeholder rows hold the space instead.
            //
            // `result_count` and not this table's own `count`: a run that brings
            // nothing but tracks the library already has leaves the table empty
            // with the work genuinely done, and the placeholders have to come
            // down for it. `count` cannot tell that from having found nothing at
            // all; the list's own total can.
            loading: root.importBusy && importVM.result_count === 0
            // Never more placeholders than there are files to match. Four files
            // under a panel of twelve bars is a promise the arrival breaks.
            // `searchTotal` is the run's own count and reads 0 through the scan,
            // which has not finished counting the folder yet — so the panel
            // fills until the number is known, then settles to it.
            loadingCount: importVM.searchTotal
            onCopied: toast.show("Copied to clipboard", false)
        }
    }

    // --------------------------------------------------------------- dialogs

    FileDialog {
        id: fileDialog
        title: "Choose files to import"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Audio files (*.aac *.flac *.m4a *.mp3 *.ogg *.opus *.wav)",
                      "All files (*)"]
        onAccepted: importVM.analyzePaths(selectedFiles)
    }

    FolderDialog {
        id: folderDialog
        title: "Choose a folder to import"
        // Wrapped in a list because analyzePaths takes a QVariantList; it walks
        // any folder in it recursively, so nested albums come along.
        onAccepted: importVM.analyzePaths([selectedFolder])
    }

    FileDialog {
        id: csvDialog
        title: "Choose a CSV to import"
        nameFilters: ["CSV files (*.csv)", "All files (*)"]
        onAccepted: importVM.analyzeCsv(selectedFile)
    }

    // An in-app prompt rather than a system dialog, because there is no file to
    // pick — the answer is usually on the clipboard. What it means is the
    // plugin's to work out, so nothing is validated here beyond "not blank",
    // which the dialog enforces on its own.
    NameDialog {
        id: linkDialog
        onSubmitted: function (text, editId) {
            importVM.importFromSource(root._pluginSourceKey, text)
        }
    }

    // The other shape a plugin's source can ask for: a file of its own kind.
    FileDialog {
        id: pluginFileDialog
        fileMode: FileDialog.OpenFile
        onAccepted: importVM.importFromSource(root._pluginSourceKey,
                                              selectedFile.toString())
    }

    ImportOptionsDialog { id: importOptions }

    // Raised by "Import all", never by a single row's own button: clicking one
    // row is already an answer to this question.
    ChoiceDialog {
        id: unmatchedChoice
        heading: "Import the files that matched nothing?"
        // Both halves of the count matter: these files matched nothing *and*
        // can still be imported. One that matched nothing and has no title or
        // artist either is not in this number, because no answer here changes
        // what happens to it. Quoted alone rather than against `result_count`,
        // which counts the whole list — including rows that are downloaded, and
        // rows already saved, neither of which this question is about.
        body: importVM.unmatchedImportableCount
              + " files matched nothing online but can still be imported "
              + "from their own tags — without the album, artwork or release "
              + "date a match would have supplied.\n\nSkipped files stay in the "
              + "list, so you can still take them one at a time."
        primaryText: "Import everything"
        secondaryText: "Matched only"
        onPrimary: importVM.stashAll(true)
        onSecondary: importVM.stashAll(false)
    }

    // A yes/no, so a ConfirmDialog rather than the ChoiceDialog above — and
    // nothing here is destructive, so it is not a `danger` one either. Adding a
    // track to a record takes nothing away from the record it came off.
    ConfirmDialog {
        id: claimDialog
        title: "Add these to “" + root._claimAlbumName + "”?"
        // Named rather than counted, the way the reorder toast names the tracks
        // it moved: "3 tracks" leaves you to work out which three, and the
        // signal carries the titles precisely so it does not have to.
        body: {
            var titles = root._claimTitles || []
            var shown = titles.slice(0, 2).join(", ")
            if (titles.length > 2)
                shown += " and " + (titles.length - 2) + " more"
            var one = titles.length === 1
            return shown + (one ? " is" : " are")
                 + " already in your library but not on this album.\n\n"
                 + (one ? "It" : "They") + " will be added to it without "
                 + "changing the album " + (one ? "it belongs" : "each of them belongs")
                 + " to."
        }
        confirmText: "Add to album"
        cancelText: "Not now"
        onConfirmed: {
            var added = importVM.claimTracksForAlbum(root._claimAlbumId,
                                                     root._claimTrackIds)
            if (added > 0)
                toast.show(Fmt.count(added, "track") + " added to “"
                           + root._claimAlbumName + "”", false)
        }
    }

    Toast { id: toast }
}
