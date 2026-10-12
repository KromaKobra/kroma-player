import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

NocturneMenu {
    id: root
    property var entries: []
    property int selectionCount: 1
    property bool playlist: false
    property bool inDetail: false
    signal openRequested()
    signal editRequested()
    signal renameRequested()
    signal exportRequested()
    signal deleteRequested()

    MenuRow {
        text: "Play"; iconName: "play"; enabled: root.entries.length > 0
        onTriggered: playerVM.playEntries(root.entries)
    }
    MenuRow {
        text: "Play next"; iconName: "next"; enabled: root.entries.length > 0
        onTriggered: queueVM.insertNextEntries(root.entries)
    }
    MenuRow {
        text: "Add to queue"; iconName: "plus"; enabled: root.entries.length > 0
        onTriggered: queueVM.appendEntries(root.entries)
    }
    MenuSeparator { visible: root.selectionCount === 1; height: visible ? implicitHeight : 0 }
    MenuRow {
        text: "Open"; iconName: "chevronRight"
        visible: root.selectionCount === 1 && !root.inDetail; height: visible ? implicitHeight : 0
        onTriggered: root.openRequested()
    }
    MenuRow {
        text: "Edit…"; iconName: "edit"
        visible: root.selectionCount === 1 && !root.playlist; height: visible ? implicitHeight : 0
        onTriggered: root.editRequested()
    }
    MenuRow {
        text: "Rename…"; iconName: "edit"
        visible: root.selectionCount === 1 && root.playlist; height: visible ? implicitHeight : 0
        onTriggered: root.renameRequested()
    }
    MenuRow {
        text: "Export…"; iconName: "download"
        visible: root.selectionCount === 1 && root.playlist; height: visible ? implicitHeight : 0
        enabled: root.entries.length > 0
        onTriggered: root.exportRequested()
    }
    MenuRow {
        text: "Delete playlist"; iconName: "trash"; danger: true
        visible: root.selectionCount === 1 && root.playlist; height: visible ? implicitHeight : 0
        onTriggered: root.deleteRequested()
    }
}
