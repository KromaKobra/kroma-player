import QtQuick
import Nocturne

Column {
    property string iconName: ""
    property string title: ""
    property string description: ""
    width: Math.min(320, parent.width)
    spacing: Theme.gap
    Icon {
        anchors.horizontalCenter: parent.horizontalCenter
        name: parent.iconName
        size: 40
        color: Theme.alpha(Theme.text, 0.16)
    }
    Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        text: parent.title
        color: Theme.dim
        font.family: Theme.displayFamily
        font.pixelSize: Theme.fzMed
        font.weight: Font.DemiBold
    }
    Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: parent.description
        color: Theme.faint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
    }
}
