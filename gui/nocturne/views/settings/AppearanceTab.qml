import QtQuick
import Nocturne
import "../../components"

// What the interface looks like, down to which interface it is.
SettingsPane {
    id: pane

    onActiveChanged: {
        if (!active)
            return
        if (typeof guiVM !== "undefined")
            guiVM.refresh()
        syncThemePill()
    }

    // The control owns its index, so the preference is pushed into it rather
    // than bound to it — a binding would be discarded by the first click and
    // the pill would go on showing something nothing agreed to.
    function syncThemePill() {
        themePill.currentIndex = Math.max(
            0, Theme.modeKeys.indexOf(Theme.prefs.themeMode))
    }

    // ================================================== appearance
    Column {
        width: parent.width
        spacing: Theme.gap

        SectionLabel { text: "APPEARANCE" }

        Item {
            width: parent.width
            height: Math.max(themeLabel.implicitHeight, themePill.height)

            Column {
                id: themeLabel
                anchors.left: parent.left
                anchors.right: themePill.left
                anchors.rightMargin: Theme.gap
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2

                Text {
                    text: "Theme"
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: "System follows the desktop, and keeps following it "
                        + "while Kroma Player is open."
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                }
            }

            Pill {
                id: themePill
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                options: Theme.modeLabels
                onSelected: function (index) {
                    Theme.setMode(Theme.modeKeys[index])
                }
                Component.onCompleted: pane.syncThemePill()
            }
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Kroma means colour. Pick the one this interface runs on."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Flow {
            width: parent.width
            spacing: Theme.gap

            Repeater {
                model: Theme.accentKeys

                delegate: Item {
                    id: swatch
                    required property var modelData

                    readonly property var preset: Theme.accentTable[modelData]
                    readonly property bool selected: Theme.prefs.accent === modelData

                    width: 104
                    height: 76

                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radius
                        color: swatch.selected ? Theme.alpha(Theme.text, 0.07)
                             : (swatchMouse.containsMouse
                                ? Theme.alpha(Theme.text, 0.04) : "transparent")
                        border.width: swatch.selected ? 1 : 1
                        border.color: swatch.selected
                                      ? Theme.alpha(Theme.accent, 0.55) : Theme.line
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                    }

                    Column {
                        anchors.centerIn: parent
                        spacing: 9

                        Rectangle {
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: 62
                            height: 22
                            radius: 11
                            // The pair for the mode that is on, so a swatch
                            // previews what picking it would actually paint.
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop {
                                    position: 0.0
                                    color: Theme.accentOf(swatch.modelData)
                                }
                                GradientStop {
                                    position: 1.0
                                    color: Theme.accentAltOf(swatch.modelData)
                                }
                            }
                            scale: swatchMouse.containsMouse ? 1.06 : 1.0
                            Behavior on scale {
                                NumberAnimation { duration: Theme.fast
                                                  easing.type: Theme.easing }
                            }
                        }

                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: swatch.preset.label
                            color: swatch.selected ? Theme.text : Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                            font.weight: swatch.selected ? Font.DemiBold : Font.Normal
                        }
                    }

                    MouseArea {
                        id: swatchMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Theme.setAccent(swatch.modelData)
                    }
                }
            }
        }

        ToggleSwitch {
            width: parent.width
            label: "Ambient artwork"
            description: "Tint the window with a blurred wash of the playing track's cover."
            checked: Theme.prefs.ambient
            onToggled: function (value) { Theme.prefs.ambient = value }
        }

        ToggleSwitch {
            width: parent.width
            label: "Reduce sidebar motion"
            description: "Change sidebar size immediately instead of animating."
            checked: Theme.prefs.reducedMotion
            onToggled: function (value) { Theme.prefs.reducedMotion = value }
        }
    }

    Rectangle { width: parent.width; height: 1; color: Theme.line }

    // ================================================= gui switching
    Column {
        width: parent.width
        spacing: Theme.gap
        visible: typeof guiVM !== "undefined"

        SectionLabel { text: "INTERFACE" }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Kroma Player loads its interface from a folder. Drop another one in "
                + "and pick it here — changes take effect on the next launch."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Rectangle {
            width: parent.width
            height: restartText.implicitHeight + 20
            visible: typeof guiVM !== "undefined" && guiVM.restartRequired
            radius: Theme.radiusSm
            color: Theme.alpha(Theme.accent, 0.10)
            border.width: 1
            border.color: Theme.alpha(Theme.accent, 0.35)

            Text {
                id: restartText
                anchors.fill: parent
                anchors.margins: 10
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                text: typeof guiVM !== "undefined"
                      ? "“" + guiVM.selectedName + "” loads next time you start Kroma Player."
                      : ""
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }

        Column {
            width: parent.width
            spacing: Theme.gapSm

            Repeater {
                model: typeof guiVM !== "undefined" ? guiVM.available : []

                delegate: Rectangle {
                    id: guiCard
                    required property var modelData

                    width: parent.width
                    height: guiInfo.implicitHeight + 26
                    radius: Theme.radius
                    color: guiMouse.containsMouse && modelData.loadable
                           ? Theme.alpha(Theme.text, 0.05) : Theme.alpha(Theme.card, 0.5)
                    border.width: 1
                    border.color: (typeof guiVM !== "undefined"
                                   && modelData.name === guiVM.selectedName)
                                  ? Theme.alpha(Theme.accent, 0.55) : Theme.line
                    opacity: modelData.loadable ? 1 : 0.55

                    Behavior on color { ColorAnimation { duration: Theme.fast } }

                    Column {
                        id: guiInfo
                        anchors.left: parent.left
                        anchors.leftMargin: 15
                        anchors.right: tick.left
                        anchors.rightMargin: Theme.gap
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3

                        Row {
                            spacing: Theme.gapSm

                            Text {
                                text: guiCard.modelData.title
                                color: Theme.text
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzBody
                                font.weight: Font.DemiBold
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: guiCard.modelData.version
                                      ? "v" + guiCard.modelData.version : ""
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzTiny
                            }
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: guiCard.modelData.current
                                width: runningLabel.implicitWidth + 14
                                height: 18
                                radius: 9
                                color: Theme.alpha(Theme.accent, 0.16)

                                Text {
                                    id: runningLabel
                                    anchors.centerIn: parent
                                    text: "running"
                                    color: Theme.accent
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fzMicro
                                    font.weight: Font.DemiBold
                                }
                            }
                        }

                        Text {
                            width: parent.width
                            text: guiCard.modelData.loadable
                                  ? (guiCard.modelData.description
                                     || guiCard.modelData.author)
                                  : guiCard.modelData.error
                            wrapMode: Text.WordWrap
                            color: guiCard.modelData.loadable ? Theme.faint : Theme.danger
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                        }
                    }

                    Icon {
                        id: tick
                        anchors.right: parent.right
                        anchors.rightMargin: 15
                        anchors.verticalCenter: parent.verticalCenter
                        visible: typeof guiVM !== "undefined"
                                 && guiCard.modelData.name === guiVM.selectedName
                        name: "check"
                        size: 18
                        color: Theme.accent
                    }

                    MouseArea {
                        id: guiMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: guiCard.modelData.loadable
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: guiVM.select(guiCard.modelData.name)
                    }
                }
            }
        }

        Row {
            spacing: Theme.gapSm

            AppButton {
                text: "Open GUI folder"
                iconName: "folder"
                variant: "ghost"
                onClicked: guiVM.openGuiFolder()
            }
            AppButton {
                text: "Rescan"
                iconName: "sort"
                variant: "ghost"
                onClicked: guiVM.refresh()
            }
        }
    }

    Item { width: 1; height: Theme.gapLg }
}
