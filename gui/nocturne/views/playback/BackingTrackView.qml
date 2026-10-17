import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import Nocturne
import "../../components/controls"

// Backing tracks, made here. Not in the navigation rail: you arrive by asking
// for an instrumental that does not exist yet — the mic button in the transport,
// or "Generate backing track" on a selection — and leave when it does.
//
// The work runs in a Python interpreter the user configures in Settings,
// because Kroma cannot host PyTorch itself. Everything on this page degrades to
// an explanation when that is not set up.
Item {
    id: root

    property bool active: false

    // Back out to wherever the user came from.
    signal done()

    onActiveChanged: {
        if (!active)
            return
        // The models folder is a plain directory — the user may well have just
        // dropped a checkpoint into it from outside the app.
        backingVM.refreshModels()
        syncDevicePill()
        backingVM.probeDevicesIfNeeded()
    }

    // Pill owns its own index — clicking one assigns to `currentIndex`, which
    // would blow away a binding put there by us (the same reason NavRail
    // reports clicks instead of self-selecting). So the setting is pushed into
    // it imperatively, exactly as BrowseView does with its source pill. This is
    // what keeps this pill, the identical one in Settings, and the stored value
    // from drifting apart.
    function syncDevicePill() {
        devicePill.currentIndex = Math.max(
            0, backingVM.deviceChoices.indexOf(backingVM.device))
    }

    Connections {
        target: backingVM
        function onError_occurred(message) { toast.show(message, true) }
        function onSettings_changed() { root.syncDevicePill() }
        function onBatch_finished() {
            if (backingVM.succeededCount > 0)
                toast.show(Fmt.count(backingVM.succeededCount, "backing track")
                           + " ready", false)
        }
    }

    readonly property var selectedModelEntry: {
        var list = backingVM.models
        for (var i = 0; i < list.length; i++)
            if (list[i].name === backingVM.selectedModel)
                return list[i]
        return null
    }

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Backing tracks"
        subtitle: backingVM.jobCount > 0
                  ? backingVM.completedCount + " of " + backingVM.jobCount + " done"
                  : "Strip the vocals from a track to get an instrumental"

        AppButton {
            anchors.verticalCenter: parent.verticalCenter
            text: "Back"
            iconName: "chevronDown"
            variant: "ghost"
            onClicked: root.done()
        }
    }

    // -------------------------------------------------------------- controls

    Rectangle {
        id: setup
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        height: setupColumn.implicitHeight + Theme.gap * 2
        radius: Theme.radius
        color: Theme.alpha(Theme.card, 0.72)
        border.width: 1
        border.color: Theme.line

        Column {
            id: setupColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Theme.gap
            anchors.rightMargin: Theme.gap
            spacing: Theme.gapSm

            // Model + device, side by side.
            Row {
                spacing: Theme.gap

                Column {
                    spacing: 4

                    Text {
                        text: "MODEL"
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzMicro
                        font.weight: Font.DemiBold
                        font.letterSpacing: 0.8
                    }

                    AppButton {
                        text: backingVM.selectedModel !== ""
                              ? backingVM.selectedModel : "No model selected"
                        iconName: "sparkle"
                        variant: "ghost"
                        fontSize: Theme.fzSmall
                        onClicked: modelMenu.popup()
                    }
                }

                Column {
                    spacing: 4

                    Text {
                        text: "DEVICE"
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzMicro
                        font.weight: Font.DemiBold
                        font.letterSpacing: 0.8
                    }

                    Pill {
                        id: devicePill
                        options: backingVM.deviceChoices
                        onSelected: function (index) {
                            backingVM.device = backingVM.deviceChoices[index]
                        }
                        Component.onCompleted: root.syncDevicePill()
                    }
                }
            }

            // What the configured interpreter can actually do. This is the one
            // place the "you have not set this up" story gets told.
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: backingVM.deviceError !== "" || backingVM.backendError !== ""
                       ? Theme.danger : Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
                text: {
                    if (backingVM.isProbing)
                        return "Checking what this interpreter can run on…"
                    if (backingVM.pythonPath === "")
                        return "No Python interpreter set. Kroma cannot run the model "
                             + "itself — choose one in Settings that has PyTorch, "
                             + "along with the code to run the model with."
                    if (backingVM.deviceError !== "")
                        return backingVM.deviceError
                    if (backingVM.backendError !== "")
                        return backingVM.backendError
                    if (backingVM.availableDevices.length > 0)
                        return "Available here: "
                             + backingVM.availableDevices.join(", ")
                             + (backingVM.backend !== ""
                                ? " — running the model with " + backingVM.backend
                                : "")
                    return ""
                }
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                visible: root.selectedModelEntry !== null
                         && !root.selectedModelEntry.usable
                color: Theme.danger
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
                text: root.selectedModelEntry
                      ? root.selectedModelEntry.reason : ""
            }
        }
    }

    // -------------------------------------------------------------- progress

    Rectangle {
        id: progressStrip
        anchors.top: setup.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        height: 56
        radius: Theme.radius
        color: Theme.alpha(Theme.card, 0.72)
        border.width: 1
        border.color: Theme.line

        Column {
            anchors.left: parent.left
            anchors.leftMargin: Theme.gap
            anchors.right: actions.left
            anchors.rightMargin: Theme.gap
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2

            Text {
                width: parent.width
                elide: Text.ElideRight
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                font.weight: Font.DemiBold
                text: backingVM.isRunning
                      ? backingVM.activeTitle
                      : (backingVM.jobCount === 0
                         ? "Nothing queued"
                         : Fmt.count(backingVM.jobCount, "track") + " queued")
            }

            Text {
                width: parent.width
                elide: Text.ElideRight
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
                text: backingVM.isRunning
                      ? (backingVM.activeStage + " · " + backingVM.activeProgress + "%")
                      : (backingVM.failedCount > 0
                         ? Fmt.count(backingVM.failedCount, "failure")
                         : "")
                visible: text !== ""
            }
        }

        Row {
            id: actions
            anchors.right: parent.right
            anchors.rightMargin: Theme.gap
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.gapSm

            AppButton {
                text: "Generate"
                iconName: "sparkle"
                variant: "filled"
                visible: !backingVM.isRunning
                enabledAction: backingVM.canGenerate && backingVM.jobCount > 0
                               && backingVM.completedCount < backingVM.jobCount
                onClicked: backingVM.generate()
            }
            AppButton {
                text: "Cancel"
                variant: "ghost"
                visible: backingVM.isRunning
                onClicked: backingVM.cancel()
            }
            AppButton {
                text: "Clear"
                variant: "ghost"
                visible: !backingVM.isRunning && backingVM.completedCount > 0
                onClicked: backingVM.clearFinished()
            }
        }

        // Batch progress along the bottom edge: whole tracks done, plus the
        // fraction of the one running, so it advances continuously rather than
        // jumping once per track.
        Rectangle {
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            anchors.margins: 1
            height: 2
            radius: 1
            color: Theme.accent
            visible: backingVM.jobCount > 0
            width: {
                if (backingVM.jobCount === 0)
                    return 0
                var whole = backingVM.completedCount
                var partial = backingVM.isRunning
                              ? backingVM.activeProgress / 100 : 0
                return (parent.width - 2) * Math.min(
                    1, (whole + partial) / backingVM.jobCount)
            }
            Behavior on width { NumberAnimation { duration: Theme.normal } }
        }
    }

    // ------------------------------------------------------------------ jobs

    ListView {
        id: jobList
        anchors.top: progressStrip.bottom
        anchors.topMargin: Theme.gap
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        clip: true
        spacing: 2
        model: backingVM

        ScrollBar.vertical: EdgeScrollBar {
            parent: jobList.parent
            anchors.top: jobList.top
            anchors.bottom: jobList.bottom
            anchors.right: parent.right
            visible: jobList.visible && needed
        }

        delegate: Rectangle {
            id: jobRow
            required property int trackId
            required property string title
            required property string artist
            required property string state
            required property int progress
            required property string message

            width: jobList.width
            height: 52
            radius: Theme.radiusSm
            color: rowMouse.containsMouse ? Theme.alpha(Theme.text, 0.04) : "transparent"
            // Finished work recedes; what is still to come stays legible.
            opacity: (state === "done" || state === "cancelled") ? 0.55 : 1

            Behavior on color { ColorAnimation { duration: Theme.fast } }

            Icon {
                id: stateIcon
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                size: 17
                name: jobRow.state === "done" ? "check"
                    : jobRow.state === "failed" ? "x"
                    : jobRow.state === "cancelled" ? "x"
                    : jobRow.state === "running" ? "sparkle" : "clock"
                color: jobRow.state === "done" ? Theme.accent
                     : jobRow.state === "failed" ? Theme.danger
                     : jobRow.state === "running" ? Theme.accent : Theme.faint
            }

            Column {
                anchors.left: stateIcon.right
                anchors.leftMargin: Theme.gap
                anchors.right: removeButton.left
                anchors.rightMargin: Theme.gapSm
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2

                Text {
                    width: parent.width
                    text: jobRow.title
                    elide: Text.ElideRight
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                }

                Text {
                    width: parent.width
                    elide: Text.ElideRight
                    text: {
                        if (jobRow.state === "running")
                            return jobRow.message + " · " + jobRow.progress + "%"
                        if (jobRow.state === "failed")
                            return jobRow.message
                        if (jobRow.state === "done")
                            return "Backing track ready"
                        if (jobRow.state === "cancelled")
                            return "Cancelled"
                        return jobRow.artist
                    }
                    color: jobRow.state === "failed" ? Theme.danger : Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                }
            }

            IconButton {
                id: removeButton
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                icon: "x"
                iconSize: 14
                diameter: 26
                // The running job leaves through Cancel, not by being deleted
                // out from under itself.
                visible: jobRow.state !== "running"
                tooltip: "Remove"
                onClicked: backingVM.removeJob(jobRow.trackId)
            }

            MouseArea {
                id: rowMouse
                anchors.fill: parent
                anchors.rightMargin: 40
                hoverEnabled: true
            }

            // Per-row progress, hairline along the bottom.
            Rectangle {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                height: 2
                radius: 1
                color: Theme.accent
                visible: jobRow.state === "running"
                width: parent.width * jobRow.progress / 100
                Behavior on width { NumberAnimation { duration: Theme.normal } }
            }
        }

        // Empty state.
        Column {
            anchors.centerIn: parent
            spacing: Theme.gapSm
            visible: backingVM.jobCount === 0

            Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                name: "mic"
                size: 34
                color: Theme.alpha(Theme.text, 0.18)
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Nothing queued — pick tracks and choose "
                    + "“Generate backing track”"
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }
        }
    }

    SmoothWheel { target: jobList }

    // --------------------------------------------------------------- dialogs

    // Built with an Instantiator, not a Repeater: a Repeater inside a Menu
    // parents its items to the internal list instead of registering them as
    // entries, so they never appear (the same trap the Library sort menu
    // documents).
    NocturneMenu {
        id: modelMenu

        Instantiator {
            model: backingVM.models
            onObjectAdded: function (index, object) {
                modelMenu.insertItem(index, object)
            }
            onObjectRemoved: function (index, object) {
                modelMenu.removeItem(object)
            }

            delegate: MenuRow {
                required property var modelData
                text: !modelData.usable
                      ? modelData.name + " — " + modelData.reason
                      : modelData.configName
                        ? modelData.name + "  (" + modelData.configName + ")"
                        : modelData.name
                iconName: modelData.name === backingVM.selectedModel ? "check" : ""
                enabled: modelData.usable
                onTriggered: backingVM.selectedModel = modelData.name
            }
        }

        MenuSeparator {
            padding: 0
            topPadding: 4
            bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        MenuRow {
            text: "Import a model…"
            iconName: "folder"
            onTriggered: modelDialog.open()
        }
    }

    FileDialog {
        id: modelDialog
        title: "Choose a vocal-removal model"
        nameFilters: ["Model checkpoints (*.ckpt)", "All files (*)"]
        onAccepted: backingVM.importModel(selectedFile)
    }

    Toast { id: toast }
}
