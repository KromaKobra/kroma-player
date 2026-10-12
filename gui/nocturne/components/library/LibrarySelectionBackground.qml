import QtQuick

// Behind entries and controls, inside the content item so the Flickable does
// not consume empty-space clicks before they reach this handler.
MouseArea {
    required property var view
    parent: view.contentItem
    x: 0
    y: view.contentY
    width: view.width
    height: view.height
    z: -1
    acceptedButtons: Qt.LeftButton
    onClicked: if (view.controller) view.controller.clearSelection()
}
