import QtQuick
import Nocturne

// Left navigation rail. The accent pill slides between destinations rather than
// cutting, which makes the view change feel like one continuous surface.
Item {
    id: root

    property int currentIndex: 0

    // The permanent destinations. Each entry carries its own `view` index
    // rather than relying on its position: the transient slots below come and
    // go independently, so slot position and view index are not the same thing.
    property var baseItems: [
        { view: 0, icon: "disc",     label: "Now Playing" },
        { view: 1, icon: "queue",    label: "Queue" },
        { view: 2, icon: "search",   label: "Search" },
        { view: 3, icon: "playlist", label: "Playlists" },
        { view: 4, icon: "library",  label: "Library" },
        { view: 5, icon: "settings", label: "Settings" }
    ]

    // None of the hidden tabs is a permanent destination — you get sent to one
    // by asking for an instrumental, by choosing Edit on a track, or by picking
    // something to import. But while one has anything to show it needs to
    // behave like every other page: a slot you can see, click, and see
    // selected. So they join the rail below Settings for as long as they are
    // relevant, marked off by a hairline so they do not read as fixtures.
    property bool showBacking: false
    property bool showEdit: false
    property bool showImport: false
    property bool showArtist: false
    property bool showAlbum: false

    // The open artist's name, so the slot says who rather than what. Falls back
    // to "Artist" when nothing is loaded and the slot is only up because the
    // page is on screen.
    property string artistLabel: "Artist"
    // Which of the two artist pages the slot points at — the page, or the editor
    // if that is where you are. One slot for both, because the editor is a mode
    // of the artist rather than a second place to be.
    property int artistView: 9

    // The album slot, on the same terms as the artist one: the open album's name
    // so it says which record, and one slot covering both the page and its
    // editor because the editor is a mode of the album rather than a second
    // place to be.
    property string albumLabel: "Album"
    property int albumView: 11

    // Ordered by how long the job outlives your attention: the artist and the
    // track you are looking at, then the batch you are triaging, then the batch
    // grinding away in the background underneath all of it.
    readonly property var items: baseItems
        .concat(showArtist ? [{ view: artistView, icon: "person", label: artistLabel, transient: true }] : [])
        .concat(showAlbum ? [{ view: albumView, icon: "disc", label: albumLabel, transient: true }] : [])
        .concat(showEdit ? [{ view: 7, icon: "edit", label: "Edit track", transient: true }] : [])
        .concat(showImport ? [{ view: 8, icon: "stash", label: "Import", transient: true }] : [])
        .concat(showBacking ? [{ view: 6, icon: "mic", label: "Backing tracks", transient: true }] : [])

    // Where the current view sits in the rail, or -1 when it has no slot.
    readonly property int currentSlot: {
        for (var i = 0; i < items.length; i++)
            if (items[i].view === currentIndex)
                return i
        return -1
    }

    signal navigated(int index)

    implicitWidth: Theme.navWidth

    readonly property int slotHeight: 56
    readonly property int topOffset: 8

    // Scrolling anywhere on the rail — including the empty stretch below the
    // last slot, which is still the rail — moves one destination at a time.
    //
    // Steps the *slot*, not the view index, for the reason the marker below
    // does: with a transient tab up the two are different numbers, and adding
    // one to the view index would jump from Settings to a page the rail is not
    // drawing. Walking slots means "the next one down", which is the only
    // thing scrolling a list of destinations can be asking for.
    //
    // The step is reported through navigated() like a click, so the rail still
    // never touches its own currentIndex and go() stays the one thing that
    // changes the view. Clamped, not wrapped: the top and bottom of the rail
    // are meant to feel like ends.
    WheelSteps {
        anchors.fill: parent
        onStepped: function (delta) {
            if (root.currentSlot < 0)
                return
            var next = Math.max(
                0, Math.min(root.items.length - 1, root.currentSlot + delta))
            if (next !== root.currentSlot)
                root.navigated(root.items[next].view)
        }
    }

    Column {
        id: column
        anchors.top: parent.top
        anchors.topMargin: root.topOffset
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 0

        Repeater {
            model: root.items

            delegate: Item {
                id: slot
                required property int index
                required property var modelData

                width: Theme.navWidth
                height: root.slotHeight

                readonly property bool selected: root.currentIndex === modelData.view

                // Divider above the temporary destinations, so the rail reads
                // as "the six, plus whatever is going on right now". Drawn on
                // the first transient slot only — a second line between two of
                // them would split one group into two.
                Rectangle {
                    anchors.top: parent.top
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 22
                    height: 1
                    color: Theme.line
                    visible: slot.modelData.transient === true
                             && (slot.index === 0
                                 || root.items[slot.index - 1].transient !== true)
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 46
                    height: 40
                    radius: 13
                    color: slotMouse.containsMouse && !slot.selected
                           ? Theme.alpha(Theme.text, 0.07) : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }

                Icon {
                    anchors.centerIn: parent
                    name: slot.modelData.icon
                    size: 21
                    strokeWidth: slot.selected ? 2.0 : 1.7
                    color: slot.selected ? Theme.accent
                                         : (slotMouse.containsMouse ? Theme.text : Theme.faint)
                    scale: slotMouse.pressed ? 0.9 : 1.0
                    Behavior on scale { NumberAnimation { duration: Theme.fast } }
                }

                MouseArea {
                    id: slotMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    // Report the click and nothing else. The rail draws whatever
                    // currentIndex it is given, and assigning to it here would
                    // overwrite the caller's binding — after which keyboard
                    // shortcuts and every other route into go() would change the
                    // view with the rail stuck on the last thing clicked.
                    onClicked: root.navigated(slot.modelData.view)
                }

                NocturneTooltip {
                    text: slot.modelData.label
                    visible: slotMouse.containsMouse
                    x: Theme.navWidth - 4
                    y: Math.round(slot.height / 2 - implicitHeight / 2)
                }
            }
        }
    }

    // Sliding selection marker. Its position is arithmetic on the *slot*, not
    // on the view index — the two only coincide while no hidden tab is up.
    // The opacity guard covers a view the rail is not currently drawing, which
    // in practice never happens: whenever a view outside baseItems is current,
    // showEdit or showBacking has already given it a slot.
    Rectangle {
        width: 3
        height: 22
        radius: 2
        color: Theme.accent
        x: 0
        y: root.topOffset + Math.max(0, root.currentSlot) * root.slotHeight
           + Math.round(root.slotHeight / 2 - height / 2)
        opacity: root.currentSlot >= 0 ? 1 : 0
        visible: opacity > 0

        Behavior on y {
            NumberAnimation { duration: Theme.normal; easing.type: Easing.OutBack }
        }
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    }
}
