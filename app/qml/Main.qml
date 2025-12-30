import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    width: 900
    height: 600
    visible: true
    title: "PySide6 MP3 Player"
    color: "#171215"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        PlaylistPane {
            Layout.fillWidth: true
            Layout.fillHeight: true
        }

        ControllerPane {
            Layout.fillWidth: true
            Layout.preferredHeight: 90
        }
    }
}
