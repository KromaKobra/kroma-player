import QtQuick
import QtQuick.Controls
import Nocturne

// Shared presentation for collection rows and tiles. Small role adapters supply
// metadata and handle actions without copying layout or interaction code.
FocusScope {
    id: root
    readonly property var view: GridView.view || ListView.view
    readonly property bool listMode: view ? view.listMode : false
    property string displayTitle: ""
    property string subtitle: ""
    property string artworkHash: ""
    property bool centered: false
    property bool attention: false
    property bool complete: false
    property bool playable: false
    property bool contextActions: false
    property bool placeholderGlyph: true
    signal activated()
    signal contextRequested()
    signal playRequested()

    width: view ? view.cellWidth : 0
    height: view ? view.cellHeight : 0
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: displayTitle + ", " + subtitle
    Accessible.onPressAction: activated()
    Keys.onReturnPressed: activated()
    Keys.onSpacePressed: activated()
    Keys.onMenuPressed: if (contextActions) contextRequested()

    Rectangle {
        anchors.fill: parent
        anchors.margins: root.listMode ? 2 : 7
        radius: Theme.radiusLg
        border.width: root.activeFocus ? 1 : 0
        border.color: Theme.accent
        color: mouse.pressed ? Theme.accentSoft : mouse.containsMouse
               ? Theme.alpha(Theme.text, 0.06) : "transparent"
        Behavior on color {
            enabled: !mouse.pressed && !Theme.prefs.reducedMotion
            ColorAnimation { duration: Theme.fast }
        }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: root.contextActions ? Qt.LeftButton | Qt.RightButton : Qt.LeftButton
        cursorShape: Qt.PointingHandCursor
        onClicked: function(event) {
            root.forceActiveFocus(Qt.MouseFocusReason)
            if (event.button === Qt.RightButton) root.contextRequested()
            else root.activated()
        }
    }
    Item {
        anchors.fill: parent
        anchors.margins: root.view ? root.view.artworkPadding : 0
        Item {
            id: artwork
            width: root.view ? root.view.artSize : 0
            height: width
            y: root.listMode ? (parent.height - height) / 2 : 0
            CoverArt {
                anchors.fill: parent
                radius: root.view && root.view.roundArtwork ? width / 2
                        : root.view ? root.view.artworkRadius : Theme.radius
                tier: root.listMode ? 128 : 512
                maskSource: root.view ? root.view.artMask : null
                hash: root.artworkHash
                placeholderText: root.displayTitle
                showPlaceholderGlyph: root.placeholderGlyph
                scale: !root.listMode && !Theme.prefs.reducedMotion && mouse.containsMouse ? 1.03 : 1
                Behavior on scale {
                    enabled: !Theme.prefs.reducedMotion
                    NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
                }
            }
            Loader {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 2
                width: 22; height: 22
                active: root.attention
                sourceComponent: Rectangle {
                    radius: 11
                    color: Theme.card
                    border.width: 1
                    border.color: Theme.alpha(Theme.accentAlt, 0.45)
                    Icon { anchors.centerIn: parent; name: "warning"; size: 12; color: Theme.accentAlt }
                }
            }
            Loader {
                id: playControl
                width: root.listMode ? 30 : 44
                height: width
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: root.listMode ? 0 : 10
                active: root.playable
                visible: active && (root.listMode || mouse.containsMouse || root.activeFocus
                                    || (item && (item.hovered || item.visualFocus)))
                sourceComponent: Button {
                    objectName: "collectionPlayButton"
                    Accessible.name: "Play " + root.displayTitle
                    contentItem: Item {
                        Icon { anchors.centerIn: parent; name: "play"; size: 17; color: Theme.accentInk }
                    }
                    background: Rectangle {
                        radius: height / 2
                        color: parent.down ? Qt.darker(Theme.accent, 1.1) : Theme.accent
                        border.width: parent.visualFocus ? 2 : 0
                        border.color: Theme.text
                    }
                    onClicked: root.playRequested()
                }
            }
        }
        Column {
            id: labels
            x: root.listMode ? artwork.width + Theme.gap : 0
            y: root.listMode ? (parent.height - height) / 2 : artwork.height + 10
            width: parent.width - x
            spacing: 3
            Text {
                width: parent.width
                horizontalAlignment: !root.listMode && root.centered ? Text.AlignHCenter : Text.AlignLeft
                text: root.displayTitle
                elide: Text.ElideRight
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                font.weight: Font.DemiBold
            }
            Row {
                id: countLine
                x: !root.listMode && root.centered ? (parent.width - width) / 2 : 0
                spacing: 5
                Loader {
                    id: mark
                    anchors.verticalCenter: parent.verticalCenter
                    active: root.complete
                    visible: active
                    sourceComponent: CompleteMark { complete: true; size: 11 }
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(0, Math.min(implicitWidth, labels.width - (mark.visible ? mark.width + countLine.spacing : 0)))
                    text: root.subtitle
                    elide: Text.ElideRight
                    color: root.centered ? Theme.dim : Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                }
            }
        }
    }
}
