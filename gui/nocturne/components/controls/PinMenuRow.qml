import QtQuick

// Menu bindings read the shared cached state; opening a menu never queries SQL.
MenuRow {
    id: root
    property string contentType: ""
    property int contentId: -1
    property bool eligible: true
    property var pinModel: typeof pinsVM !== "undefined" ? pinsVM : null
    readonly property var pinnedItems: pinModel ? pinModel.items : []
    readonly property bool alreadyPinned: {
        var items = pinnedItems
        return pinModel ? pinModel.isPinned(contentType, contentId) : false
    }
    objectName: "pinToSidebar"
    text: "Pin to sidebar"
    iconName: "pin"
    visible: pinModel !== null && eligible && contentId > 0 && !alreadyPinned
    height: visible ? implicitHeight : 0
    onTriggered: pinModel.pin(contentType, contentId)
}
