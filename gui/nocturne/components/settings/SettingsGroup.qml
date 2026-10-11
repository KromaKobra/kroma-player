import QtQuick
import Nocturne

// A named destination for search and a reusable card for related settings.
Column {
    id: root
    property string title: ""
    property string description: ""
    property string keywords: ""
    property bool highlighted: false
    property bool searchAvailable: true
    readonly property string label: title
    readonly property string settingsSearchText: title + " " + description + " " + keywords
    default property alias content: body.data
    spacing: 10
    Text {
        width: parent.width
        text: root.title
        color: Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzMed
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
    Rectangle {
        width: parent.width
        height: body.implicitHeight + 24
        radius: Theme.radiusLg
        color: Theme.alpha(Theme.card, 0.85)
        border.width: 1
        border.color: root.highlighted ? Theme.accent : Theme.lineSoft
        Column {
            id: body
            x: 18
            y: 12
            width: parent.width - 36
            spacing: 8
        }
    }
}
