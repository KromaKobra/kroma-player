import QtQuick
import Nocturne
import "../../components/controls"
import "../../components/dialogs"
import "../../components/tracks"

// The play queue, with history behind a toggle. The two keep separate selections
// in the backend, and only one of them is ever mounted — swapping listVM is what
// swaps the selection the table shows.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)

    readonly property bool showingHistory: toggle.currentIndex === 1
    readonly property var activeVM: showingHistory ? historyVM : queueVM

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: root.showingHistory ? "History" : "Queue"
        subtitle: root.showingHistory
                  ? Fmt.count(table.count, "track") + " played"
                  : (table.count > 0
                     ? Fmt.count(table.count, "track") + " queued"
                       + " · " + Fmt.longDuration(queueVM.totalDuration) + " left"
                     : "Nothing queued")

        Pill {
            anchors.verticalCenter: parent.verticalCenter
            id: toggle
            options: ["Queue", "History"]
            currentIndex: 0
        }

        AppButton {
            anchors.verticalCenter: parent.verticalCenter
            text: "Clear"
            iconName: "trash"
            variant: "ghost"
            visible: !root.showingHistory && table.count > 0
            onClicked: confirmClear.open()
        }
    }

    TrackTable {
        id: table
        anchors.top: header.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        sideMargin: Theme.gapXl

        mode: root.showingHistory ? "history" : "queue"
        listVM: root.activeVM
        active: root.active
        // One layout for both: the same rows and the same geometry. Only the
        // row number differs, and that is not part of a column layout.
        columnLayout: root.columnLayouts ? root.columnLayouts.queue : null
        showIndex: !root.showingHistory
        // History is a record of what happened; there is no order in it to
        // change. The queue's numbers double as grab handles.
        reorderable: !root.showingHistory
        emptyText: root.showingHistory
                   ? "Nothing played yet"
                   : "Your queue is empty — find something in Search or Library"
        onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
        onEditTrackRequested: function (id) { root.editTrackRequested(id) }
        onCopied: toast.show("Copied to clipboard", false)
    }

    // A track finishing takes row 0 out of the queue, and that is a removal the
    // table would otherwise animate like any other. It is not one: nobody threw
    // it out, it simply played. Naming it here is what lets the table leave it
    // alone — see `markAdvanceRemoved`.
    //
    // Bound to queueVM rather than activeVM on purpose. History gains rows as
    // playback advances, it does not lose them, so there is nothing here for it.
    Connections {
        target: queueVM
        function onAdvance_removed(uid) {
            table.markAdvanceRemoved(uid)
        }
    }

    ConfirmDialog {
        id: confirmClear
        title: "Clear the queue?"
        body: "Playback stops and every queued track is removed. "
              + "Your library is not affected."
        confirmText: "Clear queue"
        // One reset, not a row at a time: removing them one by one would have
        // the list animate every departure separately — see TrackTable's
        // `remove` transition.
        onConfirmed: {
            playerVM.stop_player()
            queueVM.clearAll()
        }
    }

    Toast { id: toast }
}
