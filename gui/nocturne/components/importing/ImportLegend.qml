import QtQuick
import Nocturne

/*
 * What is in the import list, said in words, and a way to act on it.
 *
 * The rows already mark themselves — a glyph in the status cell, an amber wash,
 * a badge over the artwork — but a marking only answers "what is this row?". It
 * cannot answer "how many of these are there?", and it cannot be answered at all
 * without scrolling. This answers both, and it is also where the vocabulary is
 * taught: the glyph beside "4 unavailable" is the same glyph those four rows are
 * wearing, so the list explains itself once and needs no legend elsewhere.
 *
 * A column down the side of the page rather than a bar across the top of the
 * list. The counts are what the user watches an import by, and a bar puts them
 * on the one axis a growing list is already using — six chips wrapping or
 * eliding as a batch is gathered, above rows scrolling past. A column holds one
 * chip per line at a fixed width, so a number changing is the only thing that
 * moves.
 *
 * Clicking a chip selects those rows. That is the whole of "manage them": the
 * right-click menu's Remove from list already acts on a selection, so taking out
 * every row that will never import becomes one press and one menu item rather
 * than a hunt down the list. It selects rather than filters because selecting is
 * something the list can already do, and because a filter hides the thing it is
 * talking about, which is the opposite of what a legend is for.
 *
 * The `inLibrary` chip is the one whose rows may not be on screen — the
 * `clearImported` setting hides them, and they stay in the list precisely so
 * this can go on counting them. Selecting them is still what a press does; the
 * backend refuses to *remove* a row nobody can see, which is the only thing that
 * would be dangerous about it.
 *
 * Chips appear only for states the list actually contains, so a plain run of
 * local files shows one chip and a gathered batch shows five. The order is fixed
 * by the backend's `ALL_STATES` rather than by count, so a chip does not move
 * under the pointer as rows are imported — which matters more in a column, where
 * a chip appearing pushes every chip below it down.
 *
 * Colour follows the same rule as the status cell: severity only, never the
 * accent, because the accent is one of six hues the user picks and two of them
 * would read as a state.
 */
Item {
    id: root

    // Rows in each state, straight from `importVM.stateCounts`.
    property var counts: ({})

    signal stateChosen(string state)

    // One chip's height, read by the delegates as well so the two cannot drift.
    readonly property int chipHeight: 26

    // How many chips there are, counted off the data rather than measured off
    // the laid-out column.
    //
    // That distinction is load-bearing, and getting it wrong is a latch: a
    // positioner that is not effectively visible does not lay out, so a legend
    // sizing itself from `chips.height` and *hiding itself* when that reads 0
    // can never measure anything again. It stays 0 for as long as the page it
    // is on was hidden when it was built — which is every import, because the
    // rail is not shown until a run has results. Height and visibility both hang
    // off the counts instead, and the counts are true before anything is drawn.
    readonly property int shownCount: {
        var n = 0
        for (var i = 0; i < states.length; i++)
            if (countFor(states[i].key) > 0)
                n++
        return n
    }

    implicitHeight: shownCount > 0
                    ? shownCount * chipHeight + (shownCount - 1) * Theme.gapSm
                    : 0
    visible: shownCount > 0

    // Order, glyph and wording for each state. The keys are the backend's
    // `importState` values and the order is its `ALL_STATES`: what is done,
    // then what will happen, then what wants attention, then what cannot be
    // helped.
    //
    // Every label reads the same after any number, which is why they are
    // "to download" and not "needs downloading": a chip is a count with a
    // phrase after it, and a phrase that only agrees with one of them would be
    // wrong more often than right. The three in the middle are deliberately the
    // same shape — "to X" — because they are the three the Import button acts
    // on, and "to identify" names the fix for the one it does not.
    readonly property var states: [
        { key: "inLibrary",     icon: "check",    label: "in library" },
        { key: "download",      icon: "download", label: "to download" },
        { key: "stash",         icon: "stash",    label: "to import" },
        { key: "tagsOnly",      icon: "warning",  label: "tags only" },
        { key: "needsMetadata", icon: "search",   label: "to identify" },
        { key: "unavailable",   icon: "x",        label: "unavailable" }
    ]

    function colorFor(key) {
        if (key === "inLibrary")     return Theme.positive
        if (key === "tagsOnly")      return Theme.warning
        if (key === "needsMetadata") return Theme.warning
        if (key === "unavailable")   return Theme.danger
        return Theme.dim
    }

    function countFor(key) {
        var n = root.counts ? root.counts[key] : 0
        return Number(n) || 0
    }

    Column {
        id: chips
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.gapSm

        Repeater {
            model: root.states

            delegate: Rectangle {
                id: chip

                required property var modelData

                readonly property int n: root.countFor(modelData.key)
                readonly property color tint: root.colorFor(modelData.key)

                // A state with no rows in it is not a chip. Showing "0 tags
                // only" would put a number in front of the user that means
                // nothing and cannot be pressed. A Column leaves an invisible
                // child out of its layout entirely, spacing and all, so the
                // ones that are there close up on their own.
                visible: n > 0
                width: parent.width
                height: root.chipHeight
                radius: height / 2
                color: chipHover.hovered ? Theme.alpha(tint, 0.16)
                                         : Theme.alpha(tint, 0.09)
                border.width: 1
                border.color: Theme.alpha(tint, chipHover.hovered ? 0.42 : 0.24)

                Behavior on color { ColorAnimation { duration: Theme.fast } }

                HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }

                // Anchored rather than laid out in a Row, and left-aligned
                // rather than centred. The chips are all one width now, so
                // centring would leave six glyphs at six different insets with
                // no column edge for the eye to run down — and a fixed left
                // inset is what lets the label elide when the rail is narrow
                // instead of pushing the glyph off it.
                Icon {
                    id: chipIcon
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    name: chip.modelData.icon
                    size: 13
                    // Small enough that the default stroke falls under a
                    // pixel, the same reason the artwork badge sets one.
                    strokeWidth: 2.2
                    color: chip.tint
                }

                Text {
                    anchors.left: chipIcon.right
                    anchors.leftMargin: 6
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: chip.n + " " + chip.modelData.label
                    elide: Text.ElideRight
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    font.features: ({ "tnum": 1 })
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: root.stateChosen(chip.modelData.key)
                }

                NocturneTooltip {
                    text: "Select these " + chip.n + " rows"
                    visible: chipHover.hovered
                }
            }
        }
    }
}
