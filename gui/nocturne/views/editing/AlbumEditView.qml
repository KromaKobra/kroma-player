import QtQuick
import QtQuick.Controls
import Nocturne
import "../../components/artwork"
import "../../components/controls"
import "../../components/dialogs"
import "../../components/editing"

// One album's identity, editable — and the place a record whose artwork
// disagrees is put right.
//
// The album counterpart of ArtistEditView, and it keeps that page's habits: the
// fields are pushed rather than bound, the edits are staged, and a save that is
// refused leaves the text in place so the one wrong field can be corrected.
//
// A name and whatever ids the enabled plugins declare is all an album has, so
// most of the page is the three things you can only do here: identify the album
// through a plugin, unify its artwork, and split it in two.
Item {
    id: root

    property bool active: false

    signal done()

    // Fields are pushed, not bound — a NocturneField owns its own `text` and
    // typing assigns to it, which would destroy a binding we put there and take
    // Revert with it. Same reasoning as ArtistEditView.syncFields().
    function syncFields() {
        nameRow.text = albumVM.name
        // The id rows push their own text in as the editor rebuilds them —
        // see ExternalIdRows.
    }

    onActiveChanged: if (active) syncFields()

    Connections {
        target: albumVM
        function onAlbum_changed() { root.syncFields() }
        function onError_occurred(message) { toast.show(message, true) }
        function onSaved(albumId) { toast.show("Saved", false) }
        function onOverwrite_offered(value) { overwriteDialog.open() }
        function onRefreshed(albumId) {
            toast.show("Name and cover updated from " + albumVM.refreshLabel, false)
        }
        // Zero is not a failure here: it is the run where every track already
        // agreed and only the album's own sleeve moved. "0 tracks now share one
        // cover" would report that as nothing having happened.
        function onUnify_completed(count) {
            toast.show(count > 0
                       ? Fmt.count(count, "track") + " now share one cover"
                       : "Album cover updated", false)
        }
        function onSplit_completed(albumId) {
            toast.show("Split into a second album of the same name", false)
        }
    }

    // A newly set id is the one field that, once right, can put the name and
    // the cover right too — so it is the one field that offers.
    ConfirmDialog {
        id: overwriteDialog
        title: "Update from " + albumVM.refreshLabel + "?"
        body: "Fetch this release's name and cover from " + albumVM.refreshLabel
              + ", and replace what is stored for this album. The album's "
              + "tracks are not touched."
        confirmText: "Update"
        cancelText: "Keep current"
        onConfirmed: albumVM.refreshFromSource()
    }

    AlbumUnifyDialog { id: unifyDialog }
    AlbumSplitDialog { id: splitDialog }

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Edit album"
        subtitle: !albumVM.hasAlbum ? "Nothing open"
                  : albumVM.unifyTotal > 0 && albumVM.isBusy
                    ? "Rewriting " + albumVM.unifyDone + " of " + albumVM.unifyTotal + "…"
                  : albumVM.isBusy ? "Working…"
                  : albumVM.isDirty ? "Unsaved changes"
                  : albumVM.name

        AppButton {
            text: "Revert"
            variant: "ghost"
            visible: albumVM.isDirty
            onClicked: albumVM.revert()
        }
        AppButton {
            text: albumVM.isSaving ? "Saving…" : "Save"
            iconName: "check"
            variant: "filled"
            enabledAction: albumVM.canSave && !albumVM.isBusy
            onClicked: albumVM.save()
        }
        AppButton {
            text: "Back"
            iconName: "chevronDown"
            variant: "ghost"
            onClicked: root.done()
        }
    }

    // ----------------------------------------------------------------- empty

    Column {
        anchors.centerIn: parent
        spacing: Theme.gapSm
        visible: !albumVM.hasAlbum

        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            name: "disc"
            size: 34
            color: Theme.alpha(Theme.text, 0.18)
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Open an album and choose “Edit” to change its details"
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }
    }

    // ------------------------------------------------------------------ form

    Flickable {
        id: flick
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        anchors.bottomMargin: Theme.gap
        visible: albumVM.hasAlbum
        clip: true
        contentHeight: form.implicitHeight + Theme.gapLg
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar { width: 7 }

        Column {
            id: form
            width: flick.width
            spacing: Theme.gap

            // ------------------------------------------- identity card

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
                        id: sleeve
                        width: 76
                        height: 76
                        radius: Theme.radius
                        tier: 128
                        hash: albumVM.coverHash
                        placeholderText: albumVM.name
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - sleeve.width - Theme.gap
                        spacing: 4

                        Text {
                            width: parent.width
                            text: albumVM.name
                            elide: Text.ElideRight
                            color: Theme.text
                            font.family: Theme.displayFamily
                            font.pixelSize: Theme.fzLarge
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width
                            text: Fmt.count(albumVM.trackCount, "track")
                                  + (albumVM.identifiedLabel !== ""
                                     ? "  ·  Identified on " + albumVM.identifiedLabel : "")
                            color: Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }
                    }
                }
            }

            // ------------------------------------------------------- album

            SectionLabel { text: "ALBUM" }

            EditFieldRow {
                id: nameRow
                width: parent.width
                label: "Name"
                placeholder: "Required"
                hint: "Two albums may share a name — they are told apart by id"
                onEdited: function (text) { albumVM.name = text }
            }

            ExternalIdRows {
                width: parent.width
                fields: albumVM.externalIdFields
                onEdited: function (key, text) { albumVM.setExternalId(key, text) }
            }

            // Staged like the two fields above it, so this saves with them and
            // Revert takes it back. Bound rather than pushed, unlike them: a
            // NocturneField owns its text because a binding would fight the
            // cursor, and a tick box has no cursor to fight.
            //
            // Usually the application works this out — "Find tracks" and
            // "Reorder" both set it from the release. This is for the record it
            // cannot check: local rips, a release no plugin's catalogue
            // carries, an album the user knows is finished.
            Item {
                width: parent.width
                height: completeRow.implicitHeight

                CheckRow {
                    id: completeRow
                    anchors.left: parent.left
                    anchors.leftMargin: 116     // EditFieldRow.labelWidth
                    anchors.right: parent.right
                    label: "Complete"
                    hint: "This album holds every track on the release. "
                          + "Cleared automatically if a track is added or removed"
                    checked: albumVM.isComplete
                    onToggled: function (value) { albumVM.isComplete = value }
                    // CheckRow fades itself when unticked, which is right on the
                    // import card — an unticked row there is a bucket being left
                    // out. Here an unticked box is the ordinary state of most
                    // albums, and fading it would read as "disabled".
                    opacity: 1
                }
            }

            // --------------------------------------------- finding the album

            // Nobody knows a release id by heart, so the page works it out from
            // a track on the record: the library holds an id for every track a
            // plugin identified, and the catalogue behind it will say which
            // release it files that recording under. Only drawn when an enabled
            // plugin can do either.
            Item {
                width: parent.width
                height: lookupRow.implicitHeight
                visible: albumVM.canIdentify || albumVM.canRefresh

                Row {
                    id: lookupRow
                    anchors.left: parent.left
                    anchors.leftMargin: 116     // EditFieldRow.labelWidth
                    spacing: Theme.gapSm

                    AppButton {
                        text: albumVM.isBusy ? "Identifying…" : "Identify from a track"
                        iconName: "sparkle"
                        variant: "soft"
                        visible: albumVM.canIdentify
                        // Dimmed with unsaved edits for its neighbour's reason:
                        // this writes and then reloads, which would discard them.
                        enabledAction: !albumVM.isBusy
                                       && !albumVM.isDirty
                                       && albumVM.trackCount > 0
                        onClicked: albumVM.identifyFromTracks()
                    }
                    AppButton {
                        text: "Refresh from " + albumVM.refreshLabel
                        iconName: "repeat"
                        variant: "ghost"
                        visible: albumVM.canRefresh && !albumVM.isDirty
                        enabledAction: !albumVM.isBusy
                        onClicked: overwriteDialog.open()
                    }
                }
            }

            // ----------------------------------------------------- artwork

            SectionLabel { text: "ARTWORK" }

            Item {
                width: parent.width
                height: artworkBody.implicitHeight

                Column {
                    id: artworkBody
                    anchors.left: parent.left
                    anchors.leftMargin: 116
                    anchors.right: parent.right
                    spacing: Theme.gapSm

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        // The rule stated where the user can see it, because it
                        // is the thing that makes the two buttons below make
                        // sense: an identified album's cover comes from the
                        // release itself, and otherwise from its own tracks.
                        text: albumVM.identifiedLabel !== ""
                              ? "This album's cover comes from " + albumVM.identifiedLabel + "."
                              : albumVM.isIdentified
                              ? "This album's cover comes from the release it is identified as."
                              : "This album's cover comes from the first of its "
                                + "tracks that has one."
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                    }

                    Row {
                        spacing: Theme.gapSm

                        AppButton {
                            text: "Use the first track's artwork"
                            iconName: "disc"
                            variant: "ghost"
                            // Refused while identified, because the stored cover
                            // is the release's own and a file's tag should not
                            // overrule it. The service refuses too; this just
                            // says so before the click.
                            enabledAction: !albumVM.isBusy && !albumVM.isIdentified
                            onClicked: albumVM.useFirstTrackArtwork()
                        }
                    }
                }
            }

            // ------------------------------------------------------ repair

            SectionLabel { text: "REPAIR" }

            Item {
                width: parent.width
                height: repairBody.implicitHeight

                Column {
                    id: repairBody
                    anchors.left: parent.left
                    anchors.leftMargin: 116
                    anchors.right: parent.right
                    spacing: Theme.gapSm

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        // Two answers to one symptom, and the page says which
                        // question each answers rather than picking for the user
                        // — nothing in the data can tell one record with bad
                        // tags from two records filed together.
                        // The sleeve above is one of the pictures that has to
                        // agree, not the arbiter of the others: an album whose
                        // cover no track carries is a disagreement too, and the
                        // one Unify's default keeper is aimed at.
                        text: albumVM.needsAttention
                              ? "This album's tracks and cover do not all agree. "
                                + "Unify if this is one record whose files "
                                + "disagree; split if these are two records "
                                + "filed as one."
                              : "This album's tracks and cover all agree."
                        color: albumVM.needsAttention ? Theme.accentAlt : Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        visible: albumVM.visitingTrackIds().length > 0
                        // The explanation for why an album can hold different
                        // covers and be perfectly correct. Without it the user
                        // sees a mixed album and no warning and assumes the
                        // check is broken.
                        text: Fmt.count(albumVM.visitingTrackIds().length, "track")
                              + " here belong to another album, so their artwork "
                              + "is theirs to keep and is not counted."
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                    }

                    Row {
                        spacing: Theme.gapSm

                        AppButton {
                            text: "Unify artwork…"
                            iconName: "disc"
                            variant: "soft"
                            enabledAction: !albumVM.isBusy && albumVM.trackCount > 0
                            onClicked: unifyDialog.openForAlbum()
                        }
                        AppButton {
                            text: "Split album…"
                            iconName: "target"
                            variant: "ghost"
                            danger: true
                            enabledAction: !albumVM.isBusy && albumVM.trackCount > 1
                            onClicked: splitDialog.openForAlbum()
                        }
                    }
                }
            }
        }
    }

    Toast { id: toast }
}
