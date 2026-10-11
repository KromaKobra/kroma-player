import QtQuick
import QtQuick.Controls
import Nocturne
import "../../components"

SettingsPane {
    id: pane
    title: "Appearance"
    description: "Your colours, your atmosphere, your interface."
    onActiveChanged: if (active && typeof guiVM !== "undefined") guiVM.refresh()

    SettingsGroup {
        width: parent.width
        title: "Theme"
        description: "Choose a look, or follow your desktop’s light and dark schedule."
        keywords: "system dark light mode"
        Row {
            width: parent.width
            spacing: 10
            Repeater {
                model: Theme.modeKeys
                delegate: Button {
                    id: themeCard
                    required property int index
                    required property string modelData
                    width: (parent.width - 20) / 3
                    height: 122
                    Accessible.name: Theme.modeLabels[index] + " theme"
                    Accessible.role: Accessible.RadioButton
                    Accessible.checked: Theme.prefs.themeMode === modelData
                    onClicked: Theme.setMode(modelData)
                    background: Rectangle {
                        radius: Theme.radius
                        color: Theme.prefs.themeMode === themeCard.modelData ? Theme.accentSoft : "transparent"
                        border.color: themeCard.visualFocus || Theme.prefs.themeMode === themeCard.modelData ? Theme.accent : Theme.line
                        border.width: 2
                    }
                    contentItem: Item {
                        Rectangle {
                            x: 4; y: 4
                            width: parent.width - 8; height: 72
                            radius: 6
                            color: themeCard.modelData === "light" ? "#edeef3" : "#171923"
                            Rectangle {
                                anchors.right: parent.right
                                width: parent.width / 2; height: parent.height
                                radius: 6
                                visible: themeCard.modelData === "system"
                                color: "#edeef3"
                            }
                            Rectangle { x: 8; y: 10; width: parent.width * 0.23; height: 4; radius: 2; color: Theme.accent }
                            Rectangle { x: 8; y: 21; width: parent.width * 0.23; height: 3; radius: 1; color: "#727889" }
                            Rectangle {
                                x: parent.width * 0.38; y: 10
                                width: parent.width * 0.54; height: 50; radius: 5
                                gradient: Gradient {
                                    GradientStop { position: 0; color: Theme.accent }
                                    GradientStop { position: 1; color: Theme.accentAlt }
                                }
                            }
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 87
                            text: (Theme.prefs.themeMode === themeCard.modelData ? "✓  " : "") + Theme.modeLabels[themeCard.index]
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                            font.weight: Font.DemiBold
                        }
                    }
                }
            }
        }
    }
    SettingsGroup {
        width: parent.width
        title: "Colour & atmosphere"
        ToggleSwitch {
            width: parent.width
            label: "Follow the artwork"
            description: "Match accents to the playing track’s cover. Your chosen colour is used when artwork is unavailable."
            checked: Theme.prefs.followArtwork
            onToggled: function(value) { Theme.prefs.followArtwork = value }
        }
        SettingRow {
            width: parent.width
            label: Theme.prefs.followArtwork ? "Fallback colour" : "Accent colour"
            description: "Choose the colour used for buttons, selections and highlights."
            keywords: "color accent aurora ember bloom verdant ice gold"
            stacked: true
            Flow {
                width: parent.width
                spacing: 10
                Repeater {
                    model: Theme.accentKeys
                    delegate: Button {
                        id: swatch
                        required property string modelData
                        width: 66; height: 66
                        Accessible.name: Theme.accentTable[modelData].label
                        Accessible.role: Accessible.RadioButton
                        Accessible.checked: Theme.prefs.accent === modelData
                        onClicked: Theme.setAccent(modelData)
                        background: Rectangle {
                            radius: Theme.radius
                            color: swatch.down ? Theme.raised : "transparent"
                            border.color: swatch.visualFocus || Theme.prefs.accent === swatch.modelData ? Theme.accent : "transparent"
                        }
                        contentItem: Column {
                            spacing: 6
                            Rectangle {
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: 32; height: 32; radius: 16
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0; color: Theme.accentOf(swatch.modelData) }
                                    GradientStop { position: 1; color: Theme.accentAltOf(swatch.modelData) }
                                }
                                Text { anchors.centerIn: parent; text: Theme.prefs.accent === swatch.modelData ? "✓" : ""; color: Theme.accentInk }
                            }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: Theme.accentTable[swatch.modelData].label; color: Theme.text; font.family: Theme.fontFamily; font.pixelSize: Theme.fzTiny }
                        }
                    }
                }
            }
        }
        ToggleSwitch {
            width: parent.width
            label: "Ambient artwork"
            description: "Add a soft wash of the playing track’s cover behind the interface."
            checked: Theme.prefs.ambient
            onToggled: function(value) { Theme.prefs.ambient = value }
        }
    }
    SettingsGroup {
        width: parent.width
        title: "Motion"
        ToggleSwitch {
            width: parent.width
            label: "Reduce motion"
            description: "Switch settings pages and resize sidebars immediately, with less control animation."
            checked: Theme.prefs.reducedMotion
            onToggled: function(value) { Theme.prefs.reducedMotion = value }
        }
    }

    // ================================================= gui switching
    SettingsGroup {
        title: "Interface package"
        keywords: "GUI skin restart version"
        width: parent.width
        spacing: Theme.gap
        visible: typeof guiVM !== "undefined"
        searchAvailable: typeof guiVM !== "undefined"



        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Choose an installed interface package. "
                + "Your selection takes effect on the next launch."
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
