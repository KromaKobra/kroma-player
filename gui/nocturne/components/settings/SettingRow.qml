import QtQuick
import Nocturne

// Shared, searchable label + editor layout. Controls emit intent; the caller
// owns the value so backend validation never breaks a QML binding.
Item {
    id: root
    property string label: ""
    property string description: ""
    property string keywords: ""
    property bool stacked: width < 510
    property real controlWidth: 210
    property bool highlighted: false
    readonly property string settingsSearchText: label + " " + description + " " + keywords
    default property alias editor: control.data
    function focusEditor() {
        function focusFirst(item) {
            if (item.activeFocusOnTab && item.enabled && item.visible) {
                item.forceActiveFocus(Qt.TabFocusReason)
                return true
            }
            var children = item.children || []
            for (var i = 0; i < children.length; ++i)
                if (focusFirst(children[i])) return true
            return false
        }
        focusFirst(control)
    }
    implicitWidth: 560
    implicitHeight: (stacked ? labels.implicitHeight + control.implicitHeight + 12
                            : Math.max(labels.implicitHeight, control.implicitHeight)) + 28
    opacity: enabled ? 1 : 0.5

    Rectangle {
        anchors.fill: parent
        anchors.margins: -4
        radius: Theme.radius
        color: root.highlighted ? Theme.accentSoft : "transparent"
        border.color: root.highlighted ? Theme.accent : "transparent"
    }
    Column {
        id: labels
        x: 0
        y: root.stacked ? 14 : (root.height - implicitHeight) / 2
        width: root.stacked ? root.width : Math.max(0, root.width - control.width - 24)
        spacing: 5
        Text {
            width: parent.width
            text: root.label
            wrapMode: Text.WordWrap
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            visible: text !== ""
            text: root.description
            wrapMode: Text.WordWrap
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            lineHeight: 1.25
        }
    }
    Item {
        id: control
        x: root.stacked ? 0 : root.width - width
        y: root.stacked ? labels.y + labels.implicitHeight + 12 : (root.height - implicitHeight) / 2
        width: root.stacked ? root.width : Math.min(root.controlWidth, root.width)
        height: implicitHeight
        implicitHeight: childrenRect.height
    }
}
