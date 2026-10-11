import QtQuick
import QtQuick.Controls
import Nocturne
import "../../components/artwork"
import "../../components/controls"
import "../../components/dialogs"
import "../../components/editing"

// One artist's identity, editable — and the place a badly-formed one is taken
// apart.
//
// The artist counterpart of TrackEditView, and it keeps that page's habits: the
// fields are pushed rather than bound, the edits are staged, and a save that is
// refused leaves the text in place so the one wrong field can be corrected.
//
// A name and whatever ids the enabled plugins declare is all an artist has, so
// most of the page is the two things you can only do here: identify the artist
// through a plugin, and split a name that is really several artists.
Item {
    id: root

    property bool active: false

    signal done()

    // Fields are pushed, not bound — a NocturneField owns its own `text` and
    // typing assigns to it, which would destroy a binding we put there and take
    // Revert with it. Same reasoning as TrackEditView.syncFields().
    function syncFields() {
        nameRow.text = artistVM.name
        // The id rows push their own text in as the editor rebuilds them —
        // see ExternalIdRows.
    }

    onActiveChanged: if (active) syncFields()

    Connections {
        target: artistVM
        function onArtist_changed() { root.syncFields() }
        function onError_occurred(message) { toast.show(message, true) }
        function onSaved(artistId) { toast.show("Saved", false) }
        function onOverwrite_offered(value) { overwriteDialog.open() }
        function onRefreshed(artistId) {
            toast.show("Name and portrait updated from " + artistVM.refreshLabel, false)
        }
        function onSplit_completed(count) {
            toast.show("Split into " + count + " artists", false)
            root.done()
        }
    }

    // A newly set id is the one field that, once right, can put the name and
    // the portrait right too — so it is the one field that offers.
    ConfirmDialog {
        id: overwriteDialog
        title: "Update from " + artistVM.refreshLabel + "?"
        body: "Fetch this artist's name and picture from " + artistVM.refreshLabel
              + ", and replace what is stored for them. The artist's tracks are "
              + "not touched."
        confirmText: "Update"
        cancelText: "Keep current"
        onConfirmed: artistVM.refreshFromSource()
    }

    // Splitting moves every one of the artist's tracks and then deletes the row,
    // so it asks first and says exactly how many tracks will move. Named as a
    // danger action because the row it removes does not come back.
    ConfirmDialog {
        id: splitDialog
        property var names: []
        title: "Split this artist?"
        body: names.length === 0 ? "" :
              "“" + artistVM.name + "” becomes " + names.join(", ")
              + ". All " + artistVM.trackCount + " of its tracks are re-credited"
              + " and the original entry is removed. Names that already exist are"
              + " reused rather than duplicated."
        confirmText: "Split"
        danger: true
        onConfirmed: artistVM.splitInto(names)
    }

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Edit artist"
        subtitle: !artistVM.hasArtist ? "Nothing open"
                  : artistVM.isFetching ? "Fetching…"
                  : artistVM.isDirty ? "Unsaved changes"
                  : artistVM.name

        AppButton {
            text: "Revert"
            variant: "ghost"
            visible: artistVM.isDirty
            onClicked: artistVM.revert()
        }
        AppButton {
            text: artistVM.isSaving ? "Saving…" : "Save"
            iconName: "check"
            variant: "filled"
            enabledAction: artistVM.canSave && !artistVM.isFetching
            onClicked: artistVM.save()
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
        visible: !artistVM.hasArtist

        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            name: "person"
            size: 34
            color: Theme.alpha(Theme.text, 0.18)
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Open an artist and choose “Edit” to change their details"
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
        visible: artistVM.hasArtist
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
                        id: portrait
                        width: 76
                        height: 76
                        radius: width / 2
                        tier: 128
                        hash: artistVM.imageHash
                        placeholderText: artistVM.name
                        showPlaceholderGlyph: false
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - portrait.width - Theme.gap
                        spacing: 4

                        Text {
                            width: parent.width
                            text: artistVM.name
                            elide: Text.ElideRight
                            color: Theme.text
                            font.family: Theme.displayFamily
                            font.pixelSize: Theme.fzLarge
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width
                            text: Fmt.count(artistVM.trackCount, "track")
                            color: Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }
                        Text {
                            width: parent.width
                            visible: artistVM.externalIdFields.length > 0
                            text: "The picture follows the artist's id — set one "
                                  + "and you will be offered the picture that goes with it."
                            wrapMode: Text.WordWrap
                            color: Theme.alpha(Theme.text, 0.3)
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                        }
                    }
                }
            }

            // ------------------------------------------------------ artist

            SectionLabel { text: "ARTIST" }

            EditFieldRow {
                id: nameRow
                width: parent.width
                label: "Name"
                placeholder: "Required"
                onEdited: function (text) { artistVM.name = text }
            }

            ExternalIdRows {
                width: parent.width
                fields: artistVM.externalIdFields
                onEdited: function (key, text) { artistVM.setExternalId(key, text) }
            }

            // --------------------------------------------- finding the id

            // Nobody knows an artist's catalogue id by heart, and an identity
            // field nobody can fill in is one nobody uses. So the page works it
            // out from a track the artist is on: the library holds an id for
            // every track a plugin identified, and the catalogue behind it says
            // who is credited on that recording without anyone having to
            // recognise one. Only drawn when an enabled plugin can do either.
            Item {
                width: parent.width
                height: lookupRow.implicitHeight
                visible: artistVM.canIdentify || artistVM.canRefresh

                Row {
                    id: lookupRow
                    anchors.left: parent.left
                    anchors.leftMargin: 116     // EditFieldRow.labelWidth
                    spacing: Theme.gapSm

                    AppButton {
                        text: artistVM.isFetching
                              ? "Identifying…" : "Identify from a track"
                        iconName: "sparkle"
                        variant: "soft"
                        visible: artistVM.canIdentify
                        // Dimmed with unsaved edits for its neighbour's reason:
                        // this writes and then reloads, which would discard them.
                        enabledAction: !artistVM.isFetching
                                       && !artistVM.isDirty
                                       && artistVM.trackCount > 0
                        onClicked: artistVM.identifyFromTracks()
                    }
                    AppButton {
                        text: "Refresh from " + artistVM.refreshLabel
                        iconName: "repeat"
                        variant: "ghost"
                        visible: artistVM.canRefresh && !artistVM.isDirty
                        enabledAction: !artistVM.isFetching
                        onClicked: overwriteDialog.open()
                    }
                }
            }

            // ------------------------------------------------------- split

            SectionLabel { text: "SPLIT" }

            Item {
                width: parent.width
                height: splitBody.implicitHeight

                Column {
                    id: splitBody
                    anchors.left: parent.left
                    anchors.leftMargin: 116
                    anchors.right: parent.right
                    spacing: Theme.gapSm

                    Text {
                        width: parent.width
                        text: artistVM.needsAttention
                              ? "This name looks like several artists run together. "
                                + "Splitting re-credits every track and removes this entry."
                              : "If this entry is really several artists, splitting "
                                + "re-credits every track and removes it. Names that "
                                + "already exist are reused, not duplicated."
                        wrapMode: Text.WordWrap
                        color: artistVM.needsAttention ? Theme.accentAlt : Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                    }

                    NocturneField {
                        id: splitField
                        width: Math.min(parent.width, 420)
                        placeholder: "Separate the artists with commas"
                    }

                    Row {
                        spacing: Theme.gapSm

                        AppButton {
                            text: "Suggest"
                            iconName: "sparkle"
                            variant: "ghost"
                            onClicked: {
                                var proposed = artistVM.proposedSplit()
                                if (proposed.length === 0) {
                                    toast.show("That name does not look compound", false)
                                    return
                                }
                                splitField.text = proposed.join(", ")
                            }
                        }
                        AppButton {
                            text: "Split"
                            iconName: "sort"
                            variant: "soft"
                            danger: true
                            enabledAction: splitField.text.trim() !== ""
                            onClicked: {
                                var parts = splitField.text.split(",")
                                    .map(function (p) { return p.trim() })
                                    .filter(function (p) { return p !== "" })
                                if (parts.length === 0)
                                    return
                                splitDialog.names = parts
                                splitDialog.open()
                            }
                        }
                    }
                }
            }
        }
    }

    Toast { id: toast }
}
