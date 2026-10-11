import QtQuick
import QtQuick.Controls
import Nocturne

// Library totals come from TrackQuery's cached aggregates. The header never
// walks track rows; playback requests IDs only when a button is activated.
Item {
    id: root

    property int songCount: 0
    property int totalDuration: 0
    property int sideMargin: Theme.gapXl
    readonly property alias optionsAnchor: moreButton
    readonly property bool stacked: width - sideMargin * 2
                                    < heading.implicitWidth + actions.width + Theme.gapLg

    signal playRequested()
    signal shuffleRequested()
    signal optionsRequested()

    implicitHeight: Theme.gapLg + heading.implicitHeight + Theme.gapXl
                    + (stacked ? actions.height + Theme.gap : 0)

    Column {
        id: heading
        x: root.sideMargin
        y: Theme.gapLg
        spacing: Theme.gapXs

        Text {
            objectName: "songsTitle"
            text: "Songs"
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzHero
            font.weight: Font.Bold
            font.letterSpacing: -0.8
            Accessible.role: Accessible.Heading
        }

        Text {
            objectName: "songsSummary"
            text: Fmt.count(root.songCount, "song") + " · " + Fmt.longDuration(root.totalDuration)
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
        objectName: "songsActions"
        x: root.stacked ? root.sideMargin : root.width - root.sideMargin - width
        y: root.stacked ? heading.y + heading.height + Theme.gap
                       : heading.y + heading.height - height
        spacing: Theme.gapSm

        HeaderButton {
            objectName: "songsPlayButton"
            text: "Play"
            iconName: "play"
            primary: true
            accessibleLabel: "Play all songs"
            enabled: root.songCount > 0
            onClicked: root.playRequested()
        }
        HeaderButton {
            objectName: "songsShuffleButton"
            iconName: "shuffle"
            accessibleLabel: "Shuffle all songs"
            enabled: root.songCount > 1
            onClicked: root.shuffleRequested()
        }
        HeaderButton {
            id: moreButton
            objectName: "songsMoreButton"
            iconName: "more"
            accessibleLabel: "Song view options"
            onClicked: root.optionsRequested()
        }
    }
}
