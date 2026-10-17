import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"
import "../controls"

// Collapse an album's mismatched artwork onto one cover.
//
// Two decisions in one dialog, and they are separate on purpose:
//
//   1. WHICH cover to keep — the top section, one row per distinct picture.
//   2. WHICH TRACKS to rewrite — the bottom section, one tick box each.
//
// The second is the whole reason this is a dialog rather than a button. A
// track's artwork belongs to the album it is *primary* for, so an album can hold
// tracks that legitimately carry different covers, and a blanket rewrite would
// destroy exactly the artwork that was correct. `albumVM.coverGroups()` only
// ever returns the tracks this album is primary for, so a visiting track cannot
// even be offered here — but among those that are offered, which ones change is
// still the user's call and this dialog is where they make it.
//
// No confirmation step afterwards. The button says how many audio files it is
// about to rewrite, which is the confirmation; a second modal for one gesture is
// worse than one honest button.
//
// The album's sleeve always follows the keeper, and that makes a third case the
// button has to speak for: every track already agrees and only the *album row*
// is out of step. Nothing is rewritten then, so the button stops counting tracks
// and says "Use as album cover" instead. It used to read "Rewrite 0 tracks" and
// sit dead, which left the one repair that case wants with no way to ask for it.
Popup {
    id: root

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 460
    height: Math.min(600, 260 + trackList.contentHeight)

    property var _groups: []
    property string _keeper: ""
    // trackId -> bool. A plain object rather than a ListModel because the ticks
    // outlive the delegates: scrolling a long album must not silently reset the
    // choices for the rows that went out of view.
    property var _ticked: ({})

    function openForAlbum() {
        _groups = albumVM.coverGroups()
        if (_groups.length === 0) {
            close()
            return
        }
        // The biggest group wins by default, so the default rewrites the fewest
        // files. coverGroups() already orders that way and never lets the
        // "no artwork" group take the lead.
        _keeper = _groups[0].coverHash
        _resetTicks()
        open()
    }

    // Everything that is not already carrying the keeper, which is precisely the
    // set of files that would have to change. The user can untick any of them.
    function _resetTicks() {
        var next = ({})
        for (var g = 0; g < _groups.length; g++) {
            var group = _groups[g]
            for (var i = 0; i < group.trackIds.length; i++)
                next[group.trackIds[i]] = group.coverHash !== _keeper
        }
        _ticked = next
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

    // Whether this run would rewrite no files at all — every track already
    // carries the keeper — leaving the album's own sleeve as the only thing out
    // of step. The common shape on an unidentified album: the sleeve came from a
    // track that has since been retagged, coverGroups() puts the tracks' cover
    // first because it is the biggest group, and nothing needs embedding.
    readonly property bool _coverOnly: _checkedIds().length === 0

    // Nothing to do at all: no keeper picked, or the keeper is already the
    // sleeve and no file needs it either.
    readonly property bool _noop:
        _keeper === "" || (_coverOnly && _keeper === albumVM.coverHash)

    function _apply() {
        if (_noop)
            return
        albumVM.unify(_keeper, _checkedIds())
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
                text: "Unify artwork"
                color: Theme.text
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzLarge
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Keep one cover for “" + albumVM.name + "”. "
                      + "The album's own sleeve follows whichever you pick. "
                      + "Only the tracks that call this album home are listed."
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }

        // ------------------------------------------------------------ keeper

        Row {
            id: covers
            anchors.top: header.bottom
            anchors.topMargin: Theme.gap
            anchors.left: parent.left
            anchors.leftMargin: Theme.gapLg
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gap

            Repeater {
                model: root._groups

                delegate: Item {
                    required property var modelData
                    width: 68
                    height: 92

                    readonly property bool isKeeper: modelData.coverHash === root._keeper

                    CoverArt {
                        id: swatch
                        width: 64
                        height: 64
                        anchors.horizontalCenter: parent.horizontalCenter
                        radius: Theme.radiusSm
                        tier: 128
                        hash: modelData.coverHash
                        placeholderText: "?"
                    }

                    Rectangle {
                        anchors.fill: swatch
                        radius: swatch.radius
                        color: "transparent"
                        border.width: 2
                        border.color: parent.isKeeper ? Theme.accent : "transparent"
                        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
                    }

                    // A count for a cover some tracks carry, a name for the
                    // album's own sleeve — which may well be carried by none of
                    // them, and where a bare "0" would read as "nothing here"
                    // rather than "the record's real artwork".
                    Text {
                        anchors.top: swatch.bottom
                        anchors.topMargin: 5
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        text: modelData.isAlbumCover && modelData.trackIds.length === 0
                              ? "the sleeve"
                              : String(modelData.trackIds.length)
                        color: parent.isKeeper ? Theme.accent : Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root._keeper = modelData.coverHash
                            root._resetTicks()
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------------ tracks

        ListView {
            id: trackList
            anchors.top: covers.bottom
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

            ScrollBar.vertical: EdgeScrollBar {}

            delegate: CheckRow {
                required property var modelData
                width: ListView.view.width
                label: modelData.title
                checked: root._ticked[modelData.trackId] === true
                // A track already carrying the keeper has nothing to rewrite.
                enabledAction: modelData.coverHash !== root._keeper
                detail: modelData.coverHash === root._keeper ? "already this cover" : ""
                onToggled: function (value) {
                    var next = root._ticked
                    next[modelData.trackId] = value
                    root._ticked = next
                }
            }
        }

        // ------------------------------------------------------------ footer

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
                // Says which of the two repairs this click is. Rewriting audio
                // files is the one worth being wary of; pointing the album row
                // at a picture its tracks already carry touches nothing on disk,
                // so it is neither dangerous nor worth counting in tracks.
                text: root._coverOnly
                      ? "Use as album cover"
                      : "Rewrite " + Fmt.count(root._checkedIds().length, "track")
                variant: "soft"
                danger: !root._coverOnly
                // `enabledAction`, not `enabled`. AppButton dims itself off the
                // former; setting the latter kills the clicks while leaving the
                // button at full opacity, which is a button that looks live and
                // is not.
                enabledAction: !root._noop
                onClicked: root._apply()
            }
        }
    }
}
