import QtQuick 2.15
import QtQuick.Window 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Window {
    id: root
    width: 1200
    height: 760
    visible: true
    color: theme.primaryColor
    title: "Kroma Player (QML)"

    // Outer margins around the main content (equivalent to AppWindow QWidget container)
    Rectangle {
        id: outer
        anchors.fill: parent
        color: "transparent"

        // main area (the content that would correspond to your QMainWindow)
        Rectangle {
            id: mainArea
            anchors.fill: parent
            anchors.margins: theme.appMargins
            anchors.topMargin: theme.appMargins + theme.menubarHeight
            color: "transparent"
        }

        // The actual app UI inside the margins
        ColumnLayout {
            id: appLayout
            anchors {
                left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom
                leftMargin: theme.appMargins
                rightMargin: theme.appMargins
                topMargin: theme.appMargins + theme.menubarHeight
                bottomMargin: theme.appMargins
            }
            spacing: 12

            // Playlist card (takes remaining vertical space)
            RoundedCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: 520
                anchors.margins: theme.playlistMargins
                color: theme.cardColor
                radius: theme.cardRadius

                // Content area of playlist
                contentItem: Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    PlaylistPane { anchors.fill: parent }
                }
            }

            // Controller card at bottom (fixed height)
            RoundedCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 75
                radius: theme.cardRadius
                color: theme.cardColor
                contentItem: Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    ControllerPane { anchors.fill: parent }
                }
            }
        }

        // Border overlay semantics: a decorative border drawn around the main area
        BorderOverlay {
            anchors.fill: parent
            borderColor: theme.accentColor
            borderWidth: 2
            radius: theme.cardRadius + 4
        }
    }
}
