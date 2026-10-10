import QtQuick
import QtQuick.Controls
import Nocturne

// Every album in the library, as a wall of sleeves.
//
// The third face of the Library page. A track list answers "what do I have" and
// the artist wall answers "who do I have"; this answers "which records do I
// have", which is the question a shelf answers and wants the shape of one.
//
// Bound straight to `albumsVM`, which is not one of the track list models: it
// loads whole rather than on demand, because albums are bounded by the size of
// the library rather than by anything a user types. That is also why this is an
// ordinary GridView with no `active` gate — there is no demand to register and
// nothing to stop fetching.
Item {
    id: root

    property alias count: grid.count

    signal albumActivated(int albumId)

    // ------------------------------------------------------------- tile size

    // Clamped where it is read, like the artist wall's, so a hand-edited
    // settings.json cannot paint a wall there is no way back from.
    readonly property int minTile: 120
    readonly property int maxTile: 300
    readonly property int tileSize:
        Math.max(minTile, Math.min(maxTile, Theme.prefs.albumTileSize))

    // --------------------------------------------------------------- ordering

    // `albumsVM` is a context property the backend owns, so its sort cannot be
    // set from a declaration the way a `TrackQuery`'s can — hence Binding
    // elements rather than plain assignments.
    Binding { target: albumsVM; property: "sortField";     value: Theme.prefs.albumSort }
    Binding { target: albumsVM; property: "sortAscending"; value: Theme.prefs.albumSortAsc }

    readonly property var sortOptions: [
        { key: "trackCount", label: "Most tracks" },
        { key: "name",       label: "Name" },
        { key: "dateAdded",  label: "Date added" }
    ]

    function sortLabel(key) {
        for (var i = 0; i < sortOptions.length; i++)
            if (sortOptions[i].key === key)
                return sortOptions[i].label
        return sortOptions[0].label
    }

    // ---------------------------------------------------------------- heading

    Item {
        id: heading
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        height: 32

        SectionLabel {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "ALL ALBUMS · " + albumsVM.count
                  + (albumsVM.needsAttentionCount > 0
                     ? "  ·  " + albumsVM.needsAttentionCount + " need a look"
                     : "")
        }

        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.gapXs

            // Its own control over its own preference: the two walls are sized
            // apart for the same reason they are sorted apart.
            TileSizeSlider {
                objectName: "albumTileSlider"
                anchors.verticalCenter: parent.verticalCenter
                tileSize: root.tileSize
                minimum: root.minTile
                maximum: root.maxTile
                viewportWidth: grid.width
                visible: available && heading.width >= 520
                onSizeRequested: function (size) { Theme.prefs.albumTileSize = size }
            }

            AppButton {
                anchors.verticalCenter: parent.verticalCenter
                text: "Sort: " + root.sortLabel(Theme.prefs.albumSort)
                iconName: "sort"
                variant: "ghost"
                fontSize: Theme.fzSmall
                hPadding: 12
                onClicked: sortMenu.popup()
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                icon: "chevronDown"
                iconSize: 16
                diameter: 30
                rotation: Theme.prefs.albumSortAsc ? 180 : 0
                tooltip: Theme.prefs.albumSortAsc ? "Ascending" : "Descending"
                onClicked: Theme.prefs.albumSortAsc = !Theme.prefs.albumSortAsc

                Behavior on rotation {
                    NumberAnimation { duration: Theme.normal
                                      easing.type: Theme.easing }
                }
            }
        }
    }

    // Written out rather than repeated over `sortOptions`: a Repeater inside a
    // Menu parents its items to the internal list instead of registering them as
    // entries, so they never appear.
    NocturneMenu {
        id: sortMenu

        MenuRow {
            text: "Most tracks"
            iconName: Theme.prefs.albumSort === "trackCount" ? "check" : ""
            onTriggered: Theme.prefs.albumSort = "trackCount"
        }
        MenuRow {
            text: "Name"
            iconName: Theme.prefs.albumSort === "name" ? "check" : ""
            onTriggered: Theme.prefs.albumSort = "name"
        }
        MenuRow {
            text: "Date added"
            iconName: Theme.prefs.albumSort === "dateAdded" ? "check" : ""
            onTriggered: Theme.prefs.albumSort = "dateAdded"
        }
    }

    // ------------------------------------------------------------------- grid

    GridView {
        id: grid
        objectName: "albumGridView"
        anchors.top: heading.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        clip: true
        model: albumsVM

        // Opens wider than the artist wall's 170. A sleeve is worth seeing at a
        // size where the artwork is legible — which is the whole point of
        // browsing records this way — and album names run longer than artist
        // names, so two lines of title need the room. That gap is the opening
        // position of two independent sliders, not a fixed ratio: from here the
        // heading's control moves this target, and the wall still fills its row.
        readonly property int columns: Math.max(2, Math.floor(width / root.tileSize))
        cellWidth: Math.floor(width / columns)
        cellHeight: cellWidth + 56

        // One mask for every sleeve in the grid. Every cell is the same size, so
        // a mask each is pure waste — and per-cell mask layers leave random
        // covers blank while scrolling, which on a page that is nothing but
        // covers would be the first thing anyone noticed. See CoverArt.qml.
        readonly property int artSize: Math.max(1, cellWidth - 34)
        CornerMask {
            id: albumArtMask
            parent: grid
            width: grid.artSize
            height: grid.artSize
            radius: Theme.radiusLg
        }

        ScrollBar.vertical: ScrollBar { width: 7 }

        delegate: Item {
            id: cell
            required property int index
            required property var albumId
            required property var name
            required property var coverHash
            required property var trackCount
            required property var needsAttention
            required property var isComplete

            width: grid.cellWidth
            height: grid.cellHeight

            Rectangle {
                anchors.fill: parent
                anchors.margins: 7
                radius: Theme.radiusLg
                color: cellMouse.containsMouse
                       ? Theme.alpha(Theme.text, 0.06) : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }

            Column {
                anchors.fill: parent
                anchors.margins: 17
                spacing: 10

                Item {
                    width: parent.width
                    height: width

                    CoverArt {
                        anchors.centerIn: parent
                        width: grid.artSize
                        height: grid.artSize
                        radius: Theme.radiusLg
                        tier: 512
                        maskSource: albumArtMask
                        hash: String(cell.coverHash || "")
                        placeholderText: String(cell.name || "")
                        scale: cellMouse.containsMouse ? 1.03 : 1.0
                        Behavior on scale {
                            NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
                        }
                    }

                    // The repair badge, in the same corner and the same amber as
                    // the artist wall's. Here it means this album's own tracks
                    // and the sleeve drawn under this badge do not all carry the
                    // same picture — either a record whose files disagree, two
                    // records filed as one, or a sleeve nothing was ever tagged
                    // with. A wall of covers side by side is exactly where that
                    // shows up.
                    Rectangle {
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 2
                        width: 22
                        height: 22
                        radius: 11
                        visible: Boolean(cell.needsAttention)
                        color: Theme.card
                        border.width: 1
                        border.color: Theme.alpha(Theme.accentAlt, 0.45)

                        Icon {
                            anchors.centerIn: parent
                            name: "warning"
                            size: 12
                            color: Theme.accentAlt
                        }
                    }
                }

                Column {
                    id: cellColumn
                    width: parent.width
                    spacing: 3

                    Text {
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        text: String(cell.name || "")
                        elide: Text.ElideRight
                        color: Theme.text
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzBody
                        font.weight: Font.DemiBold
                    }
                    // Centred as a pair, so the tick sits beside the figure
                    // rather than pushing it off the middle of the sleeve. A Row
                    // skips an invisible child *and* its spacing, so a tile with
                    // no mark is laid out exactly as it was before.
                    Row {
                        id: countLine
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: 5

                        CompleteMark {
                            id: cellMark
                            anchors.verticalCenter: parent.verticalCenter
                            complete: Boolean(cell.isComplete)
                            size: 11
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            // Sized to its text so the pair stays centred, and
                            // capped at the tile so a wide count cannot push the
                            // mark off the edge of the sleeve.
                            width: Math.min(
                                implicitWidth,
                                cellColumn.width
                                - (cellMark.visible
                                   ? cellMark.width + countLine.spacing : 0))
                            text: Fmt.count(Number(cell.trackCount || 0), "track")
                            elide: Text.ElideRight
                            color: Theme.dim
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                        }
                    }
                }
            }

            MouseArea {
                id: cellMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.albumActivated(Number(cell.albumId))
            }
        }

        // Empty state
        Column {
            anchors.centerIn: parent
            spacing: Theme.gap
            visible: grid.count === 0
            width: 320

            Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                name: "disc"
                size: 40
                color: Theme.alpha(Theme.text, 0.16)
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: "No albums yet"
                color: Theme.dim
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzMed
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: "Import or download some music and the records it came from turn up here."
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }
    }

    SmoothWheel { target: grid }
}
