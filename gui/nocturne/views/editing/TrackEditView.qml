import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import Nocturne
import "../../components/artwork"
import "../../components/chips"
import "../../components/controls"
import "../../components/dialogs"
import "../../components/editing"

// One track's metadata, editable. Opens inside the Songs tab: you arrive by
// choosing Edit on a track — from its context menu or from the details panel,
// wherever that track was listed — and the back arrow returns to the songs.
//
// Single-track on purpose. The page is where the per-track tools belong, and
// "detect this track's key" or "pitch this track down" have no batch meaning,
// so the menu entry that opens it hides itself on a multi-row selection.
Item {
    id: root

    property bool active: false

    // Up a level, to the Songs tab's list. History is the way back to wherever
    // the user came from.
    signal done()

    // A chip in the artists field was pressed. Navigation belongs to the window.
    signal openArtistRequested(int artistId)

    // And the same for a chip in the albums field.
    signal openAlbumRequested(int albumId)

    // Fields are pushed, not bound. A NocturneField owns its own `text`, and
    // typing assigns to it — which would blow away a binding put there by us,
    // after which Revert and the post-save re-read would stop showing up. So
    // the view model is the source of truth and the fields are refilled from
    // it whenever it says the track changed. Same reasoning as
    // BackingTrackView.syncDevicePill().
    function syncFields() {
        titleRow.text = editVM.title
        // Chips take the ids, with the name string as the fallback for a track
        // whose artists could not be resolved to rows.
        artistsRow.setFromIds(editVM.artistIds, editVM.artists)
        // Same bargain as the artists: ids where they resolved, the primary's
        // name as the fallback for a track whose albums did not.
        albumsRow.setFromIds(editVM.albumIds, editVM.album)
        dateRow.text = editVM.releaseDate
        genresRow.text = editVM.genres
        keyRow.text = editVM.musicalKey
        bpmRow.text = editVM.bpm
        gainRow.value = editVM.gainDb
        // The plugin-declared id rows push their own text in as the editor
        // rebuilds them — see ExternalIdRows.
        isrcRow.text = editVM.isrc
        copyrightRow.text = editVM.copyrightText
    }

    // The id fields an enabled plugin declares, split by what they name: the
    // recording itself, or where its instrumental came from.
    function idFields(role) {
        return (editVM.externalIdFields || []).filter(function (f) {
            return String(f.role || "") === role
        })
    }

    // The one page whose details panel is not driven by a selection. There is
    // no track list here to select in, so the panel's usual fallback would put
    // the playing track beside fields describing a different one. The editor is
    // unambiguously about a single track, so it reports that track the way a
    // TrackTable reports its selected rows — on activation, which main.qml's
    // go() triggers synchronously right after it clears the outgoing page's
    // selection, and again whenever the editor is handed a different track.
    function reportSelection() {
        if (!active || typeof sidebarVM === "undefined")
            return
        sidebarVM.setSelection(editVM.trackId > 0 ? [editVM.trackId] : [])
    }

    onActiveChanged: if (active) { syncFields(); reportSelection() }

    Connections {
        target: editVM
        function onTrack_changed() { root.syncFields(); root.reportSelection() }
        function onError_occurred(message) { toast.show(message, true) }
        function onSaved(trackId) { toast.show("Saved", false) }
        function onInstrumental_imported(trackId) {
            toast.show("Instrumental added", false)
        }
        function onOverwrite_offered(value) { overwriteDialog.open() }
        function onRefreshed(trackId) {
            toast.show("Metadata overwritten from " + editVM.refreshLabel, false)
        }
    }

    // A new recording id usually means the old metadata described the wrong
    // recording, so this is the one save that is worth interrupting: the right
    // one's title, artists, album, date, lyrics and artwork are one fetch away.
    ConfirmDialog {
        id: overwriteDialog
        title: "Overwrite metadata from " + editVM.refreshLabel + "?"
        body: "Fetch this recording's title, artists, album, release date, lyrics "
              + "and artwork from " + editVM.refreshLabel + ", and replace what is "
              + "stored for this track. Tempo, key, ISRC and its ids are left "
              + "alone, and the audio file itself is not fetched again."
        confirmText: "Overwrite"
        cancelText: "Keep current"
        onConfirmed: editVM.refreshFromSource()
    }

    // A track's artwork belongs to the album it is primary for, so dragging a
    // different album to the front of the chips moves the cover with it — in the
    // library and in the audio file. Worth a question before it happens: the
    // picture is the one thing about a track nobody edits by accident, and the
    // file write is the one part of a save that cannot be undone by saving again.
    //
    // Cancel writes nothing at all. That is the same all-or-nothing rule save()
    // already follows for a value that will not parse or an id that belongs
    // elsewhere, and the staged chips stay exactly as they were dragged.
    ConfirmDialog {
        id: artworkDialog
        title: "Change this track's artwork?"
        body: "“" + editVM.primaryAlbumName + "” is now this track's primary "
              + "album, so its artwork will be replaced with that album's "
              + "cover — in the library and in the audio file itself."
        confirmText: "Save"
        cancelText: "Cancel"
        onConfirmed: editVM.save()
    }

    FileDialog {
        id: instrumentalDialog
        title: "Choose this track's instrumental"
        nameFilters: ["Audio files (*.aac *.flac *.m4a *.mp3 *.ogg *.opus *.wav)",
                      "All files (*)"]
        onAccepted: editVM.importInstrumental(selectedFile)
    }

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        showBack: true
        backTooltip: "Back to Songs"
        onBackRequested: root.done()
        title: "Edit track"
        subtitle: !editVM.hasTrack ? "Nothing open"
                  : editVM.isFetching ? "Fetching from " + editVM.refreshLabel + "…"
                  : editVM.isDirty ? "Unsaved changes"
                  : editVM.title

        AppButton {
            anchors.verticalCenter: parent.verticalCenter
            text: "Revert"
            variant: "ghost"
            visible: editVM.isDirty
            onClicked: editVM.revert()
        }
        AppButton {
            anchors.verticalCenter: parent.verticalCenter
            text: editVM.isSaving ? "Saving…" : "Save"
            iconName: "check"
            variant: "filled"
            enabledAction: editVM.canSave && !editVM.isFetching
            onClicked: {
                if (editVM.artworkWillChange)
                    artworkDialog.open()
                else
                    editVM.save()
            }
        }
    }

    // ----------------------------------------------------------------- empty

    Column {
        anchors.centerIn: parent
        spacing: Theme.gapSm
        visible: !editVM.hasTrack

        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            name: "edit"
            size: 34
            color: Theme.alpha(Theme.text, 0.18)
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Right-click a track and choose “Edit” to change its details"
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }
    }

    // ----------------------------------------------------------------- form

    Flickable {
        id: flick
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        anchors.bottomMargin: Theme.gap
        visible: editVM.hasTrack
        clip: true
        contentHeight: form.implicitHeight + Theme.gapLg
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar { width: 7 }

        Column {
            id: form
            width: flick.width
            spacing: Theme.gap

            // ------------------------------------------------- identity card

            Rectangle {
                width: parent.width
                height: identity.implicitHeight + Theme.gap * 2
                radius: Theme.radius
                color: Theme.alpha(Theme.card, 0.72)
                border.width: 1
                border.color: Theme.line

                Row {
                    id: identity
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Theme.gap
                    anchors.rightMargin: Theme.gap
                    spacing: Theme.gap

                    CoverArt {
                        width: 76
                        height: 76
                        tier: 128
                        radius: Theme.radiusSm
                        hash: editVM.coverHash
                        placeholderText: editVM.title
                    }

                    Column {
                        width: parent.width - 76 - Theme.gap
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3

                        Text {
                            width: parent.width
                            text: editVM.title !== "" ? editVM.title : "Untitled"
                            elide: Text.ElideRight
                            color: Theme.text
                            font.family: Theme.displayFamily
                            font.pixelSize: Theme.fzMed
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width
                            text: "Local ID " + editVM.localIdHex
                                  + " · " + Fmt.duration(editVM.durationSeconds)
                                  + " · added " + Fmt.releaseDate(editVM.dateAdded)
                            elide: Text.ElideRight
                            color: Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }
                        Text {
                            width: parent.width
                            text: root.idFields("recording").length > 0
                                  ? "Length is fixed. Artwork follows the recording's id — "
                                    + "change it and you will be offered the new one's."
                                  : "Length is fixed."
                            wrapMode: Text.WordWrap
                            color: Theme.alpha(Theme.text, 0.3)
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                        }
                    }
                }
            }

            // ---------------------------------------------------------- track

            SectionLabel { text: "TRACK" }

            EditFieldRow {
                id: titleRow
                width: parent.width
                label: "Title"
                placeholder: "Required"
                onEdited: function (text) { editVM.title = text }
            }
            // Chips rather than a text box. See ArtistChips for why: an artist
            // committed by id cannot become a near-duplicate of one that already
            // exists, which a name matched by spelling could and regularly did.
            ArtistChips {
                id: artistsRow
                width: parent.width
                onChanged: function (ids, names) {
                    // Any chip with no id is a name that does not exist yet, so
                    // the id list cannot describe the set on its own — fall back
                    // to names and let the service create them. Once saved they
                    // have ids and every later edit takes the id path.
                    var allKnown = true
                    for (var i = 0; i < ids.length; i++)
                        if (!ids[i]) allKnown = false
                    if (allKnown)
                        editVM.setArtistIds(ids)
                    else
                        editVM.artists = names.join(", ")
                }
                onArtistOpened: function (id) { root.openArtistRequested(id) }
            }
            // Chips too, and for a second reason on top of the artist one: a
            // track can be on its original release and on three compilations,
            // and a single box could only ever name one of them. The head of the
            // list is the record it belongs to — the album whose artwork it
            // carries — so dragging a chip to the front is a real edit, not a
            // preference about ordering.
            AlbumChips {
                id: albumsRow
                width: parent.width
                onChanged: function (ids, names) {
                    editVM.setAlbums(albumsRow.stagedRefs())
                }
                onAlbumOpened: function (id) { root.openAlbumRequested(id) }
            }
            EditFieldRow {
                id: dateRow
                width: parent.width
                label: "Released"
                hint: "A year, YYYY-MM, or a full date"
                placeholder: "e.g. 2021-06-04"
                onEdited: function (text) { editVM.releaseDate = text }
            }
            EditFieldRow {
                id: genresRow
                width: parent.width
                label: "Genres"
                hint: "Separate several with commas"
                onEdited: function (text) { editVM.genres = text }
            }

            // ------------------------------------------------------- analysis

            SectionLabel { text: "ANALYSIS" }

            EditFieldRow {
                id: keyRow
                width: parent.width
                label: "Key"
                hint: "Musical or camelot — “F#m” and “11A” both work"
                onEdited: function (text) { editVM.musicalKey = text }
            }
            EditFieldRow {
                id: bpmRow
                width: parent.width
                label: "Tempo"
                hint: "Beats per minute"
                onEdited: function (text) { editVM.bpm = text }
            }

            // -------------------------------------------------------- playback

            SectionLabel { text: "PLAYBACK" }

            EditSliderRow {
                id: gainRow
                width: parent.width
                label: "Gain"
                hint: "Playback only — the audio file is never changed"
                minimum: -12
                maximum: 12
                step: 0.5
                suffix: " dB"
                onEdited: function (v) { editVM.gainDb = v }
            }

            // The same caveat the instrumental trim carries, for the same
            // reason: the output has no headroom above full scale, so a boost
            // has nowhere to go once the volume slider is near the top.
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                // The slider's own live value, not the view model's: `gainDb`
                // notifies on track_changed, which a drag deliberately does not
                // fire, and the note is most wanted while the boost is being
                // dialled in rather than after it is saved.
                visible: gainRow.shownValue > 0
                text: "A boost has nowhere to go above full volume, so near "
                    + "the top of the volume slider the difference shrinks. "
                    + "Turn the volume down a little and it comes back."
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }

            // ---------------------------------------------------- identifiers

            SectionLabel { text: "IDENTIFIERS" }

            ExternalIdRows {
                width: parent.width
                fields: root.idFields("recording")
                onEdited: function (key, text) { editVM.setExternalId(key, text) }
            }
            // The offer a save makes after a new id, available without one: the
            // stored id's catalogue, read again. Hidden with unsaved edits, since
            // it writes and reloads and would throw them away.
            Item {
                width: parent.width
                height: refreshButton.implicitHeight
                visible: editVM.canRefresh && !editVM.isDirty

                AppButton {
                    id: refreshButton
                    anchors.left: parent.left
                    anchors.leftMargin: 116     // EditFieldRow.labelWidth
                    text: editVM.isFetching ? "Fetching…" : "Refresh from " + editVM.refreshLabel
                    iconName: "repeat"
                    variant: "ghost"
                    enabledAction: !editVM.isFetching
                    onClicked: overwriteDialog.open()
                }
            }
            EditFieldRow {
                id: isrcRow
                width: parent.width
                label: "ISRC"
                onEdited: function (text) { editVM.isrc = text }
            }
            EditFieldRow {
                id: copyrightRow
                width: parent.width
                label: "Copyright"
                onEdited: function (text) { editVM.copyrightText = text }
            }

            // ---------------------------------------------------- instrumental

            SectionLabel { text: "INSTRUMENTAL" }

            Rectangle {
                width: parent.width
                height: instrumental.implicitHeight + Theme.gap * 2
                radius: Theme.radius
                color: Theme.alpha(Theme.card, 0.72)
                border.width: 1
                border.color: Theme.line

                Column {
                    id: instrumental
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Theme.gap
                    anchors.rightMargin: Theme.gap
                    spacing: Theme.gap

                    Row {
                        width: parent.width
                        spacing: Theme.gap

                        Column {
                            width: parent.width - importInstrumental.width - Theme.gap
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 3

                            Text {
                                width: parent.width
                                text: editVM.hasInstrumental
                                      ? "This track has an instrumental"
                                      : "No instrumental yet"
                                color: editVM.hasInstrumental ? Theme.positive : Theme.text
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzBody
                                font.weight: Font.DemiBold
                            }
                            Text {
                                width: parent.width
                                // The length rule is the one that gets files
                                // rejected, so it leads. The rest is what the user
                                // does *not* have to do, which is most of it.
                                text: "The same recording without vocals. An imported "
                                      + "file has to be exactly the same length as the "
                                      + "track, or the mid-playback toggle would not be "
                                      + "seamless. Any audio format works — it is "
                                      + "converted to Opus and its tags are dropped, "
                                      + "since this track already carries them."
                                      + (editVM.canFetchInstrumental
                                         ? " One fetched by a plugin is installed whatever "
                                           + "its length, so it may not line up with the mix."
                                         : "")
                                wrapMode: Text.WordWrap
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzSmall
                            }
                        }

                        AppButton {
                            id: importInstrumental
                            objectName: "importInstrumentalButton"
                            anchors.verticalCenter: parent.verticalCenter
                            text: editVM.isImportingInstrumental
                                  && !editVM.isFetchingInstrumental ? "Importing…"
                                : editVM.hasInstrumental ? "Replace…" : "Import…"
                            iconName: "mic"
                            variant: editVM.hasInstrumental ? "soft" : "filled"
                            enabledAction: !editVM.isImportingInstrumental
                            onClicked: instrumentalDialog.open()
                        }
                    }

                    // The other way to get one: an id a plugin can fetch by,
                    // installed into bt/ as it is. The id is recorded against
                    // the track once the file is in, so it is still here next
                    // time. One row per instrumental kind an enabled plugin
                    // declares; the button only where one can fetch it.
                    Repeater {
                        model: root.idFields("instrumental")

                        delegate: Row {
                            id: fetchRow
                            required property var modelData
                            width: instrumental.width
                            spacing: Theme.gap

                            EditFieldRow {
                                id: instrumentalIdRow
                                objectName: "instrumentalId:" + String(fetchRow.modelData.key)
                                width: parent.width
                                       - (fetchButton.visible ? fetchButton.width + Theme.gap : 0)
                                anchors.verticalCenter: parent.verticalCenter
                                label: String(fetchRow.modelData.sourceLabel
                                              || fetchRow.modelData.label || "")
                                hint: String(fetchRow.modelData.label || "")
                                readOnly: fetchRow.modelData.editable !== true
                                Component.onCompleted: text = String(fetchRow.modelData.value || "")
                                onEdited: function (text) {
                                    editVM.setExternalId(String(fetchRow.modelData.key), text)
                                }
                            }

                            AppButton {
                                id: fetchButton
                                objectName: "fetchInstrumentalButton"
                                anchors.verticalCenter: parent.verticalCenter
                                visible: editVM.canFetchInstrumental
                                text: editVM.isFetchingInstrumental ? "Fetching…" : "Fetch"
                                iconName: "download"
                                variant: "soft"
                                enabledAction: !editVM.isImportingInstrumental
                                               && instrumentalIdRow.text.trim() !== ""
                                onClicked: editVM.fetchInstrumental(instrumentalIdRow.text)
                            }
                        }
                    }
                }
            }

            Text {
                width: parent.width
                text: "Saving writes the library database and the audio file's own tags."
                wrapMode: Text.WordWrap
                color: Theme.alpha(Theme.text, 0.3)
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }
        }
    }

    SmoothWheel { target: flick; enabled: flick.visible }

    Toast { id: toast }
}
