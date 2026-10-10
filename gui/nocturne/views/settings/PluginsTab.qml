import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import Nocturne
import "../../components"

// What Kroma Player can do beyond the library on this computer: the plugins
// installed, which of them are on, and what each one is allowed to do.
//
// Everything here is read from `pluginsVM` and every change goes back through
// it — the tab decides nothing. Two rules the backend keeps and this page only
// shows:
//
//  * Installing does not turn anything on. That is a second, deliberate step,
//    and for a plugin with terms it is the step where they are read
//    (`consent_required`, answered by main.qml's ConsentDialog).
//  * Uninstalling takes the code and nothing else. The ids a plugin wrote, its
//    settings and the user's agreement all stay with the library, so a plugin
//    put back finds everything as it was. The confirmation says so, because
//    "uninstall" usually means the opposite.
SettingsPane {
    id: pane

    readonly property bool hasPlugins: typeof pluginsVM !== "undefined" && pluginsVM !== null
    readonly property var plugins: hasPlugins ? pluginsVM.plugins : []

    // What a manifest's `declares` entries mean, said the way a person would
    // want to hear them before installing something.
    readonly property var declarationText: ({
        "network": "Connects to the internet",
        "runs-programs": "Runs programs installed on this computer",
        "files-outside-vault": "Reads or writes files outside your library"
    })

    function describeDeclarations(list) {
        return (list || []).map(function (d) {
            return pane.declarationText[d] || d
        })
    }

    onActiveChanged: if (active && hasPlugins) pluginsVM.refresh()

    Connections {
        target: pane.hasPlugins ? pluginsVM : null
        function onError_occurred(message) { pane.notify(message, true) }
        function onInstalled(id, version) {
            pane.notify("Installed — turn it on below to use it", false)
        }
    }

    // ============================================================ safe mode
    Rectangle {
        width: parent.width
        visible: pane.hasPlugins && pluginsVM.safeMode
        height: visible ? safeText.implicitHeight + Theme.gap * 2 : 0
        radius: Theme.radius
        color: Theme.alpha(Theme.warning, 0.10)
        border.width: 1
        border.color: Theme.alpha(Theme.warning, 0.4)

        Text {
            id: safeText
            anchors.fill: parent
            anchors.margins: Theme.gap
            wrapMode: Text.WordWrap
            text: "Safe mode: every plugin is off for this session, whatever is "
                + "set below. Start Kroma Player normally to use them again."
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
    }

    // ============================================================ installed
    Column {
        width: parent.width
        spacing: Theme.gap

        SectionLabel { text: "PLUGINS" }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Plugins add what Kroma Player does not do on its own — online "
                + "search, fetching audio, identifying tracks. Each runs as its "
                + "own program, and nothing it does reaches your library except "
                + "through Kroma Player."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        AppButton {
            objectName: "installPluginButton"
            text: "Install from file…"
            iconName: "plus"
            variant: "soft"
            enabledAction: pane.hasPlugins
            onClicked: kppDialog.open()
        }

        Text {
            width: parent.width
            visible: pane.plugins.length === 0
            wrapMode: Text.WordWrap
            text: "No plugins installed."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Repeater {
            model: pane.plugins
            delegate: PluginCard {
                required property var modelData
                width: parent.width
                plugin: modelData
            }
        }
    }

    // ===================================================== provider order
    // Only when two enabled plugins offer the same thing — which, with one
    // plugin, is never. The first is asked first; a later one is asked only
    // when every one before it had nothing.
    Column {
        width: parent.width
        spacing: Theme.gap
        visible: pane.hasPlugins && pluginsVM.contestedKinds.length > 0

        Rectangle { width: parent.width; height: 1; color: Theme.line }

        SectionLabel { text: "ORDER" }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Where more than one plugin can do the same thing, the first "
                + "is asked first."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Repeater {
            model: pane.hasPlugins ? pluginsVM.contestedKinds : []
            delegate: Column {
                id: kindBlock
                required property var modelData
                width: parent.width
                spacing: 4

                Text {
                    text: kindBlock.modelData.label
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                }

                Repeater {
                    model: kindBlock.modelData.plugins
                    delegate: Row {
                        id: orderRow
                        required property var modelData
                        required property int index
                        spacing: Theme.gapSm

                        function move(delta) {
                            var ids = kindBlock.modelData.plugins.map(function (p) { return p.id })
                            var to = orderRow.index + delta
                            if (to < 0 || to >= ids.length)
                                return
                            var moved = ids.splice(orderRow.index, 1)[0]
                            ids.splice(to, 0, moved)
                            pluginsVM.setProviderOrder(kindBlock.modelData.kind, ids)
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 220
                            elide: Text.ElideRight
                            text: (orderRow.index + 1) + ".  " + orderRow.modelData.name
                            color: Theme.dim
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }
                        IconButton {
                            icon: "chevronLeft"
                            rotation: 90
                            iconSize: 14
                            diameter: 26
                            enabledAction: orderRow.index > 0
                            tooltip: "Ask earlier"
                            onClicked: orderRow.move(-1)
                        }
                        IconButton {
                            icon: "chevronLeft"
                            rotation: -90
                            iconSize: 14
                            diameter: 26
                            enabledAction: orderRow.index < kindBlock.modelData.plugins.length - 1
                            tooltip: "Ask later"
                            onClicked: orderRow.move(1)
                        }
                    }
                }
            }
        }
    }

    // ================================================================ jobs
    Column {
        width: parent.width
        spacing: 4
        visible: pane.hasPlugins && pluginsVM.jobs.length > 0

        Rectangle { width: parent.width; height: 1; color: Theme.line }

        SectionLabel { text: "WORKING" }

        Repeater {
            model: pane.hasPlugins ? pluginsVM.jobs : []
            delegate: Text {
                required property var modelData
                width: parent.width
                elide: Text.ElideRight
                text: modelData.pluginName + " — " + (modelData.label || "working")
                      + (modelData.percent >= 0 ? "  " + Math.round(modelData.percent) + "%" : "")
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }
    }

    Item { width: 1; height: Theme.gapLg }

    // ============================================================ one plugin

    component PluginCard: Rectangle {
        id: card

        property var plugin: ({})

        readonly property bool missing: plugin.source === "missing"
        readonly property bool dev: plugin.source === "dev"
        readonly property string problem:
            plugin.error ? String(plugin.error)
            : plugin.disabledReason ? String(plugin.disabledReason)
            : missing ? "Turned on in this library, but not installed on this computer."
            : ""

        height: body.implicitHeight + Theme.gap * 2
        radius: Theme.radius
        color: Theme.alpha(Theme.card, 0.72)
        border.width: 1
        border.color: Theme.line
        objectName: "plugin:" + String(plugin.id || "")

        Column {
            id: body
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.gap
            spacing: Theme.gapSm

            Item {
                width: parent.width
                height: Math.max(titleColumn.implicitHeight, toggle.implicitHeight)

                Image {
                    id: pluginIcon
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    visible: String(card.plugin.iconUrl || "") !== ""
                    width: visible ? 28 : 0
                    height: 28
                    sourceSize: Qt.size(56, 56)
                    source: String(card.plugin.iconUrl || "")
                    fillMode: Image.PreserveAspectFit
                }

                Column {
                    id: titleColumn
                    anchors.left: pluginIcon.right
                    anchors.leftMargin: pluginIcon.visible ? Theme.gapSm : 0
                    anchors.right: toggle.left
                    anchors.rightMargin: Theme.gap
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2

                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        text: String(card.plugin.name || card.plugin.id || "")
                              + (card.plugin.version ? "  " + card.plugin.version : "")
                        color: Theme.text
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        visible: text !== ""
                        text: [card.plugin.author ? "by " + card.plugin.author : "",
                               card.dev ? "development copy" : "",
                               card.plugin.running ? "running" : ""]
                              .filter(function (s) { return s !== "" }).join("  ·  ")
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzTiny
                    }
                }

                ToggleSwitch {
                    id: toggle
                    objectName: "pluginToggle"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 60
                    checked: card.plugin.enabled === true
                    enabled: !card.missing && pane.hasPlugins && !pluginsVM.safeMode
                    onToggled: function (value) { pluginsVM.setEnabled(card.plugin.id, value) }
                }
            }

            Text {
                width: parent.width
                visible: text !== ""
                wrapMode: Text.WordWrap
                text: String(card.plugin.description || "")
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Text {
                width: parent.width
                visible: text !== ""
                wrapMode: Text.WordWrap
                text: card.problem
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Text {
                width: parent.width
                visible: (card.plugin.provides || []).length > 0
                wrapMode: Text.WordWrap
                text: "Adds: " + (card.plugin.provides || []).join(", ")
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }

            Text {
                width: parent.width
                visible: (card.plugin.declares || []).length > 0
                wrapMode: Text.WordWrap
                text: pane.describeDeclarations(card.plugin.declares).join(" · ")
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }

            // The programs it needs, each with whether this machine has one.
            // Settings → System has the full story and the install commands.
            Flow {
                width: parent.width
                spacing: Theme.gapSm
                visible: (card.plugin.programs || []).length > 0

                Repeater {
                    model: card.plugin.programs || []
                    delegate: Text {
                        required property var modelData
                        text: modelData.name + (modelData.available
                              ? (modelData.stale ? " — out of date" : " ✓")
                              : " — not found")
                        color: modelData.available && !modelData.stale ? Theme.dim : Theme.warning
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzTiny
                    }
                }
            }

            // The settings form, from the manifest's `contributes.settings`.
            Repeater {
                model: card.plugin.enabled ? (card.plugin.settings || []) : []
                delegate: Loader {
                    id: settingLoader
                    required property var modelData
                    width: body.width
                    sourceComponent: modelData.type === "bool" ? boolSetting
                                   : modelData.type === "enum" ? enumSetting
                                   : modelData.type === "int" ? intSetting
                                   : textSetting

                    Component {
                        id: boolSetting
                        ToggleSwitch {
                            width: settingLoader.width
                            label: settingLoader.modelData.label
                            description: settingLoader.modelData.description
                            checked: settingLoader.modelData.value === true
                            onToggled: function (value) {
                                pluginsVM.setSetting(card.plugin.id, settingLoader.modelData.key, value)
                            }
                        }
                    }
                    Component {
                        id: enumSetting
                        Column {
                            width: settingLoader.width
                            spacing: 4
                            Text {
                                text: settingLoader.modelData.label
                                color: Theme.text
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzSmall
                            }
                            Pill {
                                options: settingLoader.modelData.values
                                currentIndex: Math.max(0, settingLoader.modelData.values
                                                       .indexOf(settingLoader.modelData.value))
                                onSelected: function (index) {
                                    pluginsVM.setSetting(card.plugin.id, settingLoader.modelData.key,
                                                         settingLoader.modelData.values[index])
                                }
                            }
                            Text {
                                width: parent.width
                                visible: text !== ""
                                wrapMode: Text.WordWrap
                                text: settingLoader.modelData.description || ""
                                color: Theme.faint
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fzTiny
                            }
                        }
                    }
                    Component {
                        id: intSetting
                        NumberSetting {
                            width: settingLoader.width
                            label: settingLoader.modelData.label
                            description: settingLoader.modelData.description || ""
                            value: Number(settingLoader.modelData.value)
                            minimum: settingLoader.modelData.min !== null
                                     && settingLoader.modelData.min !== undefined
                                     ? settingLoader.modelData.min : -1000000
                            maximum: settingLoader.modelData.max !== null
                                     && settingLoader.modelData.max !== undefined
                                     ? settingLoader.modelData.max : 1000000
                            onCommitted: function (value) {
                                pluginsVM.setSetting(card.plugin.id, settingLoader.modelData.key,
                                                     Math.round(value))
                            }
                        }
                    }
                    Component {
                        id: textSetting
                        EditFieldRow {
                            width: settingLoader.width
                            label: settingLoader.modelData.label
                            hint: settingLoader.modelData.description || ""
                            Component.onCompleted: text = String(settingLoader.modelData.value || "")
                            onEdited: function (text) {
                                pluginsVM.setSetting(card.plugin.id, settingLoader.modelData.key, text)
                            }
                        }
                    }
                }
            }

            Row {
                spacing: Theme.gapSm
                visible: !card.missing && !card.dev

                AppButton {
                    text: "Uninstall"
                    iconName: "trash"
                    variant: "ghost"
                    danger: true
                    hPadding: 12
                    fontSize: Theme.fzSmall
                    implicitHeight: 30
                    onClicked: {
                        uninstallConfirm.pluginId = card.plugin.id
                        uninstallConfirm.pluginName = card.plugin.name
                        uninstallConfirm.open()
                    }
                }
            }
        }
    }

    // ============================================================= dialogs

    FileDialog {
        id: kppDialog
        title: "Choose a plugin to install"
        nameFilters: ["Kroma Player plugins (*.kpp)", "All files (*)"]
        onAccepted: {
            var found = pluginsVM.inspect(selectedFile)
            if (found.error && !found.id) {
                pane.notify(found.error, true)
                return
            }
            trustDialog.openFor(selectedFile, found)
        }
    }

    // What a package says it is and what it will be allowed to do, read from
    // the package without running any of it — the last moment to say no.
    Popup {
        id: trustDialog
        objectName: "pluginTrustDialog"

        property var url: null
        property var info: ({})

        function openFor(fileUrl, found) {
            url = fileUrl
            info = found
            open()
        }

        modal: true
        dim: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 500

        Overlay.modal: Rectangle { color: Theme.scrim }

        background: Rectangle {
            color: Theme.dialogBg
            radius: Theme.radiusLg
            border.width: 1
            border.color: Theme.lineHard
        }

        contentItem: Column {
            spacing: Theme.gap
            padding: Theme.gapLg

            readonly property real inner: trustDialog.width - Theme.gapLg * 2

            Text {
                width: parent.inner
                wrapMode: Text.WordWrap
                text: "Install " + (trustDialog.info.name || "this plugin") + "?"
                color: Theme.text
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzLarge
                font.weight: Font.DemiBold
            }

            Text {
                width: parent.inner
                wrapMode: Text.WordWrap
                text: [trustDialog.info.version ? "Version " + trustDialog.info.version : "",
                       trustDialog.info.author ? "by " + trustDialog.info.author : "",
                       trustDialog.info.installedVersion
                       ? "replaces version " + trustDialog.info.installedVersion : ""]
                      .filter(function (s) { return s !== "" }).join("  ·  ")
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Text {
                width: parent.inner
                visible: text !== ""
                wrapMode: Text.WordWrap
                text: trustDialog.info.description || ""
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Text {
                width: parent.inner
                wrapMode: Text.WordWrap
                text: "A plugin runs with your permissions. Only install one "
                    + "from someone you trust."
                    + (pane.describeDeclarations(trustDialog.info.declares).length > 0
                       ? " It says it:\n• " + pane.describeDeclarations(trustDialog.info.declares).join("\n• ")
                       : "")
                    + ((trustDialog.info.programs || []).length > 0
                       ? "\n\nNeeds: " + trustDialog.info.programs.join(", ") : "")
                    + (trustDialog.info.hasTerms
                       ? "\n\nIt has terms you will be asked to agree to before it is turned on."
                       : "")
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
                lineHeight: 1.3
            }

            Text {
                width: parent.inner
                visible: text !== ""
                wrapMode: Text.WordWrap
                text: trustDialog.info.compatible === false ? (trustDialog.info.error || "") : ""
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Row {
                anchors.right: parent.right
                anchors.rightMargin: Theme.gapLg
                spacing: Theme.gapSm

                AppButton {
                    text: "Cancel"
                    variant: "ghost"
                    onClicked: trustDialog.close()
                }
                AppButton {
                    objectName: "pluginInstallConfirm"
                    text: "Install"
                    variant: "filled"
                    enabledAction: trustDialog.info.compatible !== false
                    onClicked: {
                        trustDialog.close()
                        pluginsVM.install(trustDialog.url)
                    }
                }
            }
        }
    }

    ConfirmDialog {
        id: uninstallConfirm
        property string pluginId: ""
        property string pluginName: ""
        title: "Uninstall " + pluginName + "?"
        body: "Its program files are removed from this computer. Everything it "
            + "added to your library stays — the ids it found, your settings for "
            + "it and your agreement to its terms — so installing it again picks "
            + "up where it left off."
        confirmText: "Uninstall"
        danger: true
        onConfirmed: {
            if (pluginsVM.uninstall(pluginId))
                pane.notify(pluginName + " uninstalled", false)
        }
    }
}
