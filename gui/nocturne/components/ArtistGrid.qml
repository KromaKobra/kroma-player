import QtQuick
import QtQuick.Controls
import Nocturne

// Every artist in the library, as a wall of portraits.
//
// The other face of the Library page. A track list answers "what do I have"; this
// answers "who do I have", which is a different question and wants a different
// shape — a circle and a name reads at a glance where a row of columns does not.
//
// Bound straight to `artistsVM`, which is not one of the track list models: it
// loads whole rather than on demand, because artists are bounded by the size of
// the library rather than by anything a user types, so paging would cost more
// than it saved. That is also why this is an ordinary GridView with no `active`
// gate — there is no demand to register and nothing to stop fetching.
Item {
    id: root

    property alias count: grid.count

    // A tile was pressed. The page above owns navigation to the artist page.
    signal artistActivated(int artistId)

    // ------------------------------------------------------------- tile size

    // The range the slider in the heading can reach. Clamped here rather than
    // trusted from the preference: `artistTileSize` is a plain integer in
    // settings.json, and a hand-edited 9999 would otherwise paint a wall of two
    // enormous tiles with no way back to a usable one.
    readonly property int minTile: 120
    readonly property int maxTile: 300
    readonly property int tileSize:
        Math.max(minTile, Math.min(maxTile, Theme.prefs.artistTileSize))

    // --------------------------------------------------------------- ordering

    // `artistsVM` is a context property the backend owns, so its sort cannot be
    // set from a declaration the way a `TrackQuery`'s can — hence Binding
    // elements rather than plain assignments. They keep the model following the
    // preference for the life of the page, which a one-shot assignment in
    // Component.onCompleted would not.
    Binding { target: artistsVM; property: "sortField";     value: Theme.prefs.artistSort }
    Binding { target: artistsVM; property: "sortAscending"; value: Theme.prefs.artistSortAsc }

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

    // The artist list's answer to the track list's "ALL TRACKS · N", which lives
    // in TrackTable's scrolling header and so is not rendered on this face. The
    // sort control rides beside it for the same reason it rides beside that one.
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
            text: "ALL ARTISTS · " + artistsVM.count
        }

        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.gapXs

            TileSizeSlider {
                objectName: "artistTileSlider"
                anchors.verticalCenter: parent.verticalCenter
                tileSize: root.tileSize
                minimum: root.minTile
                maximum: root.maxTile
                viewportWidth: grid.width
                // `available` is the control's own "this viewport offers more
                // than one layout". The width test is the other half: the
                // SectionLabel is anchored left and this Row right, and on a
                // narrow window the two would meet in the middle. The sort
                // button is the one that has to survive that, not this.
                visible: available && heading.width >= 520
                onSizeRequested: function (size) { Theme.prefs.artistTileSize = size }
            }

            AppButton {
                anchors.verticalCenter: parent.verticalCenter
                text: "Sort: " + root.sortLabel(Theme.prefs.artistSort)
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
                rotation: Theme.prefs.artistSortAsc ? 180 : 0
                tooltip: Theme.prefs.artistSortAsc ? "Ascending" : "Descending"
                onClicked: Theme.prefs.artistSortAsc = !Theme.prefs.artistSortAsc

                Behavior on rotation {
                    NumberAnimation { duration: Theme.normal
                                      easing.type: Theme.easing }
                }
            }
        }
    }

    // Written out rather than repeated over `sortOptions`, as LibraryView's is: a
    // Repeater inside a Menu parents its items to the internal list instead of
    // registering them as entries, so they never appear.
    NocturneMenu {
        id: sortMenu

        MenuRow {
            text: "Most tracks"
            iconName: Theme.prefs.artistSort === "trackCount" ? "check" : ""
            onTriggered: Theme.prefs.artistSort = "trackCount"
        }
        MenuRow {
            text: "Name"
            iconName: Theme.prefs.artistSort === "name" ? "check" : ""
            onTriggered: Theme.prefs.artistSort = "name"
        }
        MenuRow {
            text: "Date added"
            iconName: Theme.prefs.artistSort === "dateAdded" ? "check" : ""
            onTriggered: Theme.prefs.artistSort = "dateAdded"
        }
    }

    // ------------------------------------------------------------------- grid

    GridView {
        id: grid
        objectName: "artistGridView"
        anchors.top: heading.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        clip: true
        model: artistsVM

        // Opens narrower than the playlist grid's 200: a portrait and two short
        // lines need less room than an album card, and there are far more
        // artists than playlists to get through. Only the opening size, though
        // — the slider in the heading moves it, and what it moves is this
        // target, not the count below. The wall still fills its row, so the
        // size drawn is the target rounded to whatever number of columns fits.
        readonly property int columns: Math.max(2, Math.floor(width / root.tileSize))
        cellWidth: Math.floor(width / columns)
        // The portrait is `cellWidth - 34` tall (the delegate's 17px margins),
        // and the name and count under it need roughly 45 more once the 10px
        // gap is counted. The slack is deliberate: at fzBody + fzTiny the exact
        // figure moves with the font actually resolved, and a cell sized to the
        // millimetre clips its second line on a machine without the one this
        // was measured against.
        cellHeight: cellWidth + 56

        // One mask for every portrait in the grid. Every cell is the same size,
        // so a mask each is pure waste — and per-cell mask layers leave random
        // covers blank while scrolling, which on a page that is nothing but
        // covers would be the first thing anyone noticed. See CoverArt.qml.
        //
        // A CornerMask is a plain rounded Rectangle, so a radius of half its
        // size *is* a circle mask. Round portraits cost no new component.
        readonly property int artSize: Math.max(1, cellWidth - 34)
        CornerMask {
            id: artistArtMask
            parent: grid
            width: grid.artSize
            height: grid.artSize
            radius: grid.artSize / 2
        }

        ScrollBar.vertical: ScrollBar { width: 7 }

        delegate: Item {
            id: cell
            required property int index
            required property var artistId
            required property var name
            required property var imageHash
            required property var trackCount
            required property var needsAttention

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
                        radius: width / 2
                        tier: 512
                        maskSource: artistArtMask
                        hash: String(cell.imageHash || "")
                        placeholderText: String(cell.name || "")
                        showPlaceholderGlyph: false
                        scale: cellMouse.containsMouse ? 1.03 : 1.0
                        Behavior on scale {
                            NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
                        }
                    }

                    // The one piece of repair work this page can surface. A name
                    // like "person1/person2" is a mistake the user may not know
                    // they have, and a wall of artists side by side is where it
                    // is most obvious that one of them is not a person.
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

                // Centred under a circle that is itself centred in the cell.
                // Left-aligned text below a centred portrait reads as broken.
                Column {
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
                    Text {
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        text: Fmt.count(Number(cell.trackCount || 0), "track")
                        elide: Text.ElideRight
                        color: Theme.dim
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzTiny
                    }
                }
            }

            // No floating play button, unlike the playlist grid: playing an
            // artist needs their track ids, and there is no per-row slot for
            // those — artistVM.trackIds() only answers for the artist that is
            // open. Their page has Play all one click away.
            MouseArea {
                id: cellMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.artistActivated(Number(cell.artistId))
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
                name: "person"
                size: 40
                color: Theme.alpha(Theme.text, 0.16)
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: "No artists yet"
                color: Theme.dim
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzMed
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: "Import or download some music and everyone who made it turns up here."
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }
    }

    SmoothWheel { target: grid }
}
