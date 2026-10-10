import QtQuick
import QtQuick.Controls
import Nocturne

// What a scan found, and what to do about it, on one card.
//
// This replaced two ChoiceDialogs shown one after the other: the first asked
// how tracks should be identified, the second — sometimes, depending on the
// answer to the first — which file types to take. Each stated a fragment of the
// scan in prose, neither could be gone back to, and a user who realised at the
// second card that they had answered the first one wrong had to cancel and
// rescan a folder.
//
// So the makeup *is* the control. Every figure on the card is recomputed by the
// backend on every tick, which is why nothing here adds anything up: the number
// under the list is the number the Import button will act on, because both come
// from the same call.
Popup {
    id: root

    objectName: "importOptionsDialog"

    // Everything the card draws, from `importVM.importPreview`. Shaped
    // { total, selected, unreadable, withExternalId, withTitleArtist,
    //   withoutMetadata, formats: [{ ext, count, selected, untagged,
    //   excluded, transcoded }] }.
    property var preview: ({})

    readonly property bool canIdentify:
        typeof pluginsVM !== "undefined" && pluginsVM ? pluginsVM.hasIdentity : false

    property bool _fallback: true
    property var _excluded: []

    // Set every stateful field, never some of them — the rule NameDialog
    // documents. A card reopened for a second folder must not come up wearing
    // the first one's figures, and a scan of nothing but Opus must not come up
    // still excluding the wav files of the scan before it.
    function openFor(makeup, fallback, excluded) {
        preview = makeup || ({})
        _fallback = fallback
        _excluded = (excluded || []).slice()
        open()
    }

    function _refresh() {
        preview = importVM.importPreview(_fallback, _excluded)
    }

    function _setExcluded(ext, excluded) {
        var next = _excluded.filter(function (e) { return e !== ext })
        if (excluded)
            next.push(ext)
        _excluded = next
        _refresh()
    }

    readonly property int _total: preview.total || 0
    readonly property int _selected: preview.selected || 0

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 520

    // Escape is a cancel, and a cancel throws the scan away — see the Cancel
    // button. Routed through onClosed so both doors out mean the same thing.
    property bool _committed: false

    onClosed: {
        if (!_committed)
            importVM.cancelImport()
        _committed = false
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

    contentItem: Column {
        id: card
        spacing: Theme.gap
        topPadding: Theme.gapLg
        bottomPadding: Theme.gapLg
        leftPadding: Theme.gapLg
        rightPadding: Theme.gapLg

        // Column padding does not shrink its children, so anything that wants
        // to fill the card has to subtract it by hand.
        property real innerWidth: width - Theme.gapLg * 2

        Text {
            width: card.innerWidth
            text: "Import " + Fmt.count(root._total, "file")
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }

        // ------------------------------------------------------ what is here

        SectionLabel { text: "WHAT WAS FOUND" }

        Text {
            objectName: "importMakeupSummary"
            width: card.innerWidth
            wrapMode: Text.WordWrap
            lineHeight: 1.4
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            // The three identity buckets, then the two that are only worth a
            // line when they are not zero. "have neither" is the sentence the
            // fallback switch below is the answer to, so it is said before the
            // switch is offered rather than inside its description.
            text: {
                var parts = []
                if (root.preview.withExternalId > 0)
                    parts.push(root.preview.withExternalId + " already carry an id")
                if (root.preview.withTitleArtist > 0)
                    parts.push(root.preview.withTitleArtist
                               + " have title and artist tags")
                if (root.preview.withoutMetadata > 0)
                    parts.push(root.preview.withoutMetadata + " have neither")
                if (root.preview.unreadable > 0)
                    parts.push(Fmt.count(root.preview.unreadable, "file")
                               + " could not be read")
                return parts.join(" · ")
            }
        }

        // ---------------------------------------------------------- formats

        SectionLabel { text: "FILE TYPES" }

        Column {
            objectName: "importFormatList"
            width: card.innerWidth
            spacing: 2

            Repeater {
                objectName: "importFormatRepeater"
                model: root.preview.formats || []

                CheckRow {
                    required property var modelData

                    objectName: "importFormat_" + modelData.ext
                    width: parent.width
                    checked: !modelData.excluded
                    label: modelData.ext
                    count: String(modelData.count)
                    detail: modelData.transcoded ? "transcoded to Opus" : "filed as-is"
                    // Said per format because it is per format that it decides
                    // anything: a folder of well-tagged flac and badly-tagged
                    // mp3 loses nothing to the switch below on one side and
                    // most of the other.
                    hint: (root.canIdentify && !modelData.excluded && !root._fallback
                           && modelData.untagged > 0)
                          ? modelData.untagged + " have no usable tags and will be skipped"
                          : ""
                    onToggled: function (value) {
                        root._setExcluded(modelData.ext, !value)
                    }
                }
            }
        }

        // --------------------------------------------------------- matching

        // Only with something to match against: with no plugin that can
        // identify a track, every file is filed from its own tags, and a switch
        // about how matching behaves would be a switch about nothing.
        SectionLabel {
            text: "MATCHING"
            visible: root.canIdentify
        }

        ToggleSwitch {
            objectName: "importFallbackToggle"
            visible: root.canIdentify
            width: card.innerWidth
            checked: root._fallback
            label: "Search by file name when tags are missing"
            description: "A file with no title or artist is matched by its "
                         + "file name instead of being skipped. A weak match is "
                         + "reported as not found rather than guessed at."
            onToggled: function (value) {
                root._fallback = value
                root._refresh()
            }
        }

        // ----------------------------------------------------------- totals

        Rectangle {
            width: card.innerWidth
            height: 1
            color: Theme.line
        }

        Text {
            objectName: "importSelectionCount"
            width: card.innerWidth
            wrapMode: Text.WordWrap
            color: root._selected === 0 ? Theme.danger : Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            font.features: ({ "tnum": 1 })
            text: root._selected === 0
                  ? "Nothing left to import — every file is excluded by these options"
                  : root._selected + " of " + root._total + " files will be imported"
        }

        Item { width: 1; height: Theme.gapXs }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                objectName: "importOptionsCancel"
                text: "Cancel"
                variant: "ghost"
                onClicked: root.close()
            }

            AppButton {
                objectName: "importOptionsConfirm"
                text: "Import"
                iconName: "download"
                variant: "filled"
                enabledAction: root._selected > 0
                onClicked: {
                    root._committed = true
                    root.close()
                    importVM.beginImport(root._fallback, root._excluded)
                }
            }
        }
    }
}
