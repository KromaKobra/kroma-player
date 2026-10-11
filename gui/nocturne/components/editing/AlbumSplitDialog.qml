import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"
import "../controls"

// Move some of an album's tracks onto a second album with the same name.
//
// The other answer to mismatched artwork, and the opposite one: Unify says "this
// is one record and the files disagree", Split says "these were always two
// records filed as one". Nothing in the data can tell them apart, so neither is
// the default and both are one click from the album's banner.
//
// Note this is not what `ArtistService.split` does despite the shared word —
// that one merges rows by name. This divides one row into two, which is only
// expressible because `albums.name` is deliberately not unique.
Popup {
    id: root

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 460
    height: Math.min(600, 220 + trackList.contentHeight)

    property var _groups: []
    property var _ticked: ({})

    function openForAlbum() {
        // A group holding no tracks is the album's own sleeve with nothing
        // tagged with it yet. That is a real disagreement and Unify's answer to
        // it, but it says nothing about how to divide these tracks between two
        // records — and left in, it would take the head of the list and make the
        // "everything after the head" rule below tick every track on the album.
        var all = albumVM.coverGroups()
        var kept = []
        for (var k = 0; k < all.length; k++)
            if (all[k].trackIds.length > 0)
                kept.push(all[k])
        _groups = kept
        if (_groups.length === 0) {
            close()
            return
        }
        // Pre-tick the minority cover: it is the odd one out, which is why the
        // user is looking at this dialog. coverGroups() is ordered biggest
        // first, so everything after the head is the minority.
        var next = ({})
        for (var g = 0; g < _groups.length; g++) {
            var group = _groups[g]
            for (var i = 0; i < group.trackIds.length; i++)
                next[group.trackIds[i]] = g > 0
        }
        _ticked = next
        open()
    }

    function _rows() {
        var out = []
        for (var g = 0; g < _groups.length; g++) {
            var group = _groups[g]
            for (var i = 0; i < group.trackIds.length; i++) {
                out.push({
                    trackId: group.trackIds[i],
                    title: group.titles[i],
                    coverHash: group.coverHash,
                })
            }
        }
        return out
    }

    function _checkedIds() {
        var out = []
        for (var key in _ticked)
            if (_ticked[key])
                out.push(parseInt(key))
        return out
    }

    // Splitting out every track would leave an empty album behind, which the
    // service refuses anyway — better to grey the button than to raise a toast.
    readonly property bool _wouldEmpty: _checkedIds().length >= _rows().length

    function _apply() {
        var ids = _checkedIds()
        if (ids.length === 0 || _wouldEmpty)
            return
        albumVM.splitOut(ids)
        close()
    }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal }
            NumberAnimation { property: "scale"; from: 0.94; to: 1
                              duration: Theme.normal; easing.type: Theme.easing }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
    }

    Overlay.modal: Rectangle { color: Theme.scrim }

    background: Rectangle {
        color: Theme.dialogBg
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.lineHard
    }

    contentItem: Item {

        Column {
            id: header
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: Theme.gapLg
            spacing: 4

            Text {
                text: "Split album"
                color: Theme.text
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzLarge
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "The ticked tracks move to a new album, also called “"
                      + albumVM.name + "”. Nothing is deleted."
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }

        ListView {
            id: trackList
            anchors.top: header.bottom
            anchors.topMargin: Theme.gap
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: footer.top
            anchors.bottomMargin: Theme.gapSm
            anchors.leftMargin: Theme.gapLg
            anchors.rightMargin: Theme.gapLg
            clip: true
            model: root._rows()
            spacing: 2

            ScrollBar.vertical: ScrollBar { width: 6 }

            delegate: Item {
                required property var modelData
                width: ListView.view.width
                height: 40

                CoverArt {
                    id: swatch
                    width: 28
                    height: 28
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    radius: Theme.radiusSm
                    tier: 128
                    hash: modelData.coverHash
                    placeholderText: "?"
                }

                CheckRow {
                    anchors.left: swatch.right
                    anchors.leftMargin: Theme.gapSm
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    label: modelData.title
                    checked: root._ticked[modelData.trackId] === true
                    onToggled: function (value) {
                        var next = root._ticked
                        next[modelData.trackId] = value
                        root._ticked = next
                    }
                }
            }
        }

        Row {
            id: footer
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            anchors.margins: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: "Cancel"
                variant: "ghost"
                onClicked: root.close()
            }
            AppButton {
                text: "Move " + Fmt.count(root._checkedIds().length, "track")
                variant: "soft"
                danger: true
                enabled: root._checkedIds().length > 0 && !root._wouldEmpty
                onClicked: root._apply()
            }
        }
    }
}
