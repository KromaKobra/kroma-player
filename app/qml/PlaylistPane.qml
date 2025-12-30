import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Qt.labs.qmlmodels 1.0

BasePane {

    Item {
        anchors.fill: parent
        anchors.margins: 12
        clip: true

        TableView {
            anchors.fill: parent
            clip: true

            selectionModel: ItemSelectionModel {}

            model: TableModel {
                TableModelColumn { display: "title" }
                TableModelColumn { display: "artist" }
                TableModelColumn { display: "album" }
                TableModelColumn { display: "duration" }

                rows: [
                    { title: "Song A", artist: "Artist X", album: "Album 1", duration: "3:45" },
                    { title: "Song B", artist: "Artist Y", album: "Album 2", duration: "4:12" },
                    { title: "Song C", artist: "Artist Z", album: "Album 3", duration: "2:58" }
                ]
            }

            delegate: Rectangle {
                implicitHeight: 36

                color: TableView.view.selectionModel.isRowSelected(row)
                       ? "#333333"
                       : "#1e1e1e"

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    text: display
                    color: "white"
                    elide: Text.ElideRight
                }
            }

            columnWidthProvider: function (column) {
                return [300, 180, 180, 80][column]
            }
        }
    }
}
