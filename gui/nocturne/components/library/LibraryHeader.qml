import QtQuick
import QtQuick.Controls
import Nocturne

// Shared library heading, summary and responsive action row.
Item {
    id: root

    property string title: ""
    property string summary: ""
    property string actionPrefix: "library"
    property string collectionName: "Library"
    property string optionsObjectName: actionPrefix + "MoreButton"
    property bool showPlayback: false
    property bool canPlay: false
    property bool canShuffle: false
    property bool showSort: false
    property string primaryActionText: ""
    property string primaryActionIcon: "plus"
    property int sideMargin: Theme.gapXl
    readonly property alias optionsAnchor: moreButton
    readonly property alias sortAnchor: sortButton
    readonly property bool stacked: width - sideMargin * 2
                                    < heading.implicitWidth + actions.width + Theme.gapLg

    signal playRequested()
    signal shuffleRequested()
    signal sortRequested()
    signal optionsRequested()
    signal primaryActionRequested()

    implicitHeight: Theme.gapLg + heading.implicitHeight + Theme.gapXl
                    + (stacked ? actions.height + Theme.gap : 0)

    Column {
        id: heading
        x: root.sideMargin
        y: Theme.gapLg
        spacing: Theme.gapXs

        Text {
            objectName: root.actionPrefix + "Title"
            text: root.title
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzHero
            font.weight: Font.Bold
            font.letterSpacing: -0.8
            Accessible.role: Accessible.Heading
        }

        Text {
            objectName: root.actionPrefix + "Summary"
            text: root.summary
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }
    }

    // Native controls provide Tab navigation, Space/Return activation and
    // accessible names. Press feedback is immediate, without an input delay.
    component HeaderButton: Button {
        id: button
        property string iconName: ""
        property bool primary: false
        property string accessibleLabel: text

        implicitHeight: text ? 40 : 36
        implicitWidth: text ? content.implicitWidth + 36 : implicitHeight
        anchors.verticalCenter: parent.verticalCenter
        padding: 0
        hoverEnabled: true
        opacity: enabled ? 1 : 0.4
        Accessible.name: accessibleLabel
        ToolTip.text: accessibleLabel
        ToolTip.visible: hovered && !text
        ToolTip.delay: 500

        contentItem: Item {
            implicitWidth: content.implicitWidth
            Row {
                id: content
                anchors.centerIn: parent
                spacing: Theme.gapSm
                Icon {
                    anchors.verticalCenter: parent.verticalCenter
                    name: button.iconName
                    size: 18
                    rotation: button.iconName === "more" ? 90 : 0
                    color: button.primary ? Theme.accentInk : Theme.text
                }
                Text {
                    visible: button.text !== ""
                    anchors.verticalCenter: parent.verticalCenter
                    text: button.text
                    color: button.primary ? Theme.accentInk : Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                    font.weight: Font.DemiBold
                }
            }
        }

        background: Rectangle {
            radius: height / 2
            color: button.primary
                   ? (button.down ? Qt.darker(Theme.accent, 1.1)
                      : button.hovered ? Qt.lighter(Theme.accent, 1.1) : Theme.accent)
                   : (button.down ? Theme.accentSoft
                      : button.hovered ? Theme.raised : Theme.alpha(Theme.text, 0.07))
            border.width: button.visualFocus ? 2 : 0
            border.color: button.primary ? Theme.text : Theme.accent
        }
    }

    Row {
        id: actions
        objectName: root.actionPrefix + "Actions"
        x: root.stacked ? root.sideMargin : root.width - root.sideMargin - width
        y: root.stacked ? heading.y + heading.height + Theme.gap
                       : heading.y + heading.height - height
        spacing: Theme.gapSm

        HeaderButton {
            objectName: root.actionPrefix + "PrimaryButton"
            visible: root.primaryActionText !== ""
            text: root.primaryActionText
            iconName: root.primaryActionIcon
            primary: true
            onClicked: root.primaryActionRequested()
        }
        HeaderButton {
            objectName: root.actionPrefix + "PlayButton"
            visible: root.showPlayback
            text: "Play"
            iconName: "play"
            primary: true
            accessibleLabel: "Play all songs"
            enabled: root.canPlay
            onClicked: root.playRequested()
        }
        HeaderButton {
            objectName: root.actionPrefix + "ShuffleButton"
            visible: root.showPlayback
            iconName: "shuffle"
            accessibleLabel: "Shuffle all songs"
            enabled: root.canShuffle
            onClicked: root.shuffleRequested()
        }
        HeaderButton {
            id: sortButton
            objectName: root.actionPrefix + "SortButton"
            visible: root.showSort
            iconName: "sort"
            accessibleLabel: "Sort " + root.title.toLowerCase()
            onClicked: root.sortRequested()
        }
        HeaderButton {
            id: moreButton
            objectName: root.optionsObjectName
            iconName: "more"
            accessibleLabel: root.collectionName + " view options"
            onClicked: root.optionsRequested()
        }
    }
}
