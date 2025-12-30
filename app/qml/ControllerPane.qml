import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

BasePane {

    RowLayout {
        anchors.centerIn: parent
        spacing: 32

        Button {
            text: "⏮"
            font.pixelSize: 22
            background: Rectangle {
                radius: 12
                color: "#2a2a2a"
            }
        }

        Button {
            text: "⏯"
            font.pixelSize: 26
            background: Rectangle {
                radius: 14
                color: "#3a3a3a"
            }
        }

        Button {
            text: "⏭"
            font.pixelSize: 22
            background: Rectangle {
                radius: 12
                color: "#2a2a2a"
            }
        }
    }
}
