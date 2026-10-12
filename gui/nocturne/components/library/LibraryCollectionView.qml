import QtQuick
import QtQuick.Window
import Nocturne

// One live layout per collection. Switching preserves the first visible item;
// tab navigation retains the entire component and its scroll position.
Item {
    id: root
    property var controller: null
    required property var model
    required property Component delegate
    property bool listMode: false
    property bool compact: false
    property int tileSize: 200
    property int artworkPadding: 15
    property int labelHeight: 56
    property real artworkRadius: Theme.radius
    property bool roundArtwork: false
    property string viewObjectName: ""
    readonly property var activeView: layout.item
    readonly property int count: activeView ? activeView.count : 0

    property bool _ready: false
    property int _firstVisible: 0
    property int _focusedIndex: -1

    function changeLayout() {
        var previous = layout.item
        if (previous) {
            _firstVisible = Math.max(0, Math.floor((previous.contentY - previous.originY)
                                / previous.cellHeight) * previous.columns)
            _focusedIndex = -1
            var focused = previous.Window.window ? previous.Window.window.activeFocusItem : null
            while (focused && focused !== previous.contentItem) {
                if (focused.parent === previous.contentItem && focused.index !== undefined) {
                    _focusedIndex = focused.index
                    break
                }
                focused = focused.parent
            }
        }
        layout.sourceComponent = listMode ? listLayout : gridLayout
    }

    onListModeChanged: if (_ready) changeLayout()
    Component.onCompleted: { _ready = true; changeLayout() }

    Loader {
        id: layout
        anchors.fill: parent
        onLoaded: {
            var view = item
            Qt.callLater(function() {
                if (layout.item !== view || view.count === 0) return
                view.forceLayout()
                view.positionViewAtIndex(Math.min(root._firstVisible, view.count - 1), ListView.Beginning)
                if (root._focusedIndex >= 0) {
                    view.currentIndex = Math.min(root._focusedIndex, view.count - 1)
                    if (view.currentItem) view.currentItem.forceActiveFocus(Qt.OtherFocusReason)
                }
            })
        }
    }
    Component {
        id: gridLayout
        LibraryGrid {
            objectName: root.viewObjectName
            controller: root.controller
            model: root.model
            delegate: root.delegate
            tileSize: root.tileSize
            artworkPadding: root.artworkPadding
            labelHeight: root.labelHeight
            artworkRadius: root.artworkRadius
            roundArtwork: root.roundArtwork
        }
    }
    Component {
        id: listLayout
        LibraryList {
            objectName: root.viewObjectName
            controller: root.controller
            model: root.model
            delegate: root.delegate
            compact: root.compact
            artworkRadius: root.artworkRadius
            roundArtwork: root.roundArtwork
        }
    }
}
