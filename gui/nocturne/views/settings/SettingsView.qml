import QtQuick
import QtQuick.Controls
import Nocturne
import "../../components/controls"
import "../../components/settings"
import "tabs"

// Searchable categories with persistent pages and responsive navigation.
Item {
    id: root
    objectName: "settingsView"

    property bool active: false

    // The window owns one backup dialog and the optional return to an update.
    signal backupRequested(bool thenUpdate)

    readonly property var tabLabels:
        ["Appearance", "Playback", "Backing", "Library", "Plugins", "System", "About"]

    // Show one tab by its label — for a dialog elsewhere whose answer lives
    // here ("Open Settings → Plugins"). Goes the way a click does.
    function openTab(label) {
        var index = tabLabels.indexOf(label)
        if (index < 0)
            return
        searchField.clear()
        currentTab = index
        Theme.prefs.settingsTab = index
    }

    // Owned here and nowhere else. SettingsTabBar draws this and asks for
    // changes rather than making them, so a tab restored from preferences
    // arrives by the same route a click does — see the note in that file.
    //
    // Clamped on the way in, the way main.qml clamps navIndex: a hand-edited
    // ini, or a downgrade from a Nocturne with more tabs than this one.
    property int currentTab: Math.max(
        0, Math.min(tabLabels.length - 1, Theme.prefs.settingsTab))

    readonly property bool compact: width < 850
    property var searchResults: []
    readonly property var categoryHints: ["Theme, colour & motion", "Audio, queue & history", "Instrumentals & models", "Vault, backup & importing", "Extensions & permissions", "Updates & dependencies", "Shortcuts & app details"]
    function search(query) {
        var words = query.trim().toLowerCase().split(/\s+/)
        if (!query.trim()) { searchResults = []; return }
        var matches = []
        for (var i = 0; i < pages.children.length; ++i) {
            var page = pages.children[i]
            if (!page.searchEntries) continue
            var entries = page.searchEntries()
            for (var j = 0; j < entries.length; ++j) {
                var haystack = (tabLabels[page.index] + " " + entries[j].search).toLowerCase()
                if (words.every(function(word) { return haystack.indexOf(word) >= 0 })) matches.push(entries[j])
            }
        }
        searchResults = matches
    }
    function selectResult(entry) {
        openTab(tabLabels[entry.tab])
        searchField.clear()
        Qt.callLater(function() {
            // Refreshing a plugin page may replace its generated controls.
            var current = entry.pane.searchEntries().find(function(item) { return item.key === entry.key })
            if (current) entry.pane.reveal(current.target)
        })
    }

    Column {
        id: header
        x: 24
        y: 18
        spacing: 5
        Text {
            text: "Settings"
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: 32
            font.weight: Font.Bold
            font.letterSpacing: -0.8
        }
        Text {
            text: "Preferences save as you go."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
    }

    TextField {
        id: searchField
        objectName: "settingsSearch"
        x: 24
        y: header.y + header.height + 24
        width: root.compact ? root.width - 48 : 218
        height: 40
        placeholderText: "Search all settings"
        Accessible.name: "Search all settings"
        selectByMouse: true
        leftPadding: 34
        rightPadding: 32
        color: Theme.text
        placeholderTextColor: Theme.dim
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
        onTextChanged: root.search(text)
        onAccepted: if (root.searchResults.length) root.selectResult(root.searchResults[0])
        Keys.onEscapePressed: clear()
        background: Rectangle {
            radius: Theme.radius
            color: Theme.sunken
            border.color: searchField.activeFocus ? Theme.accent : Theme.line
        }
        Icon { x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; color: Theme.dim }
        SettingButton {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 30
            height: 30
            text: "×"
            visible: searchField.text !== ""
            Accessible.name: "Clear settings search"
            onClicked: searchField.clear()
        }
    }

    SettingsTabBar {
        id: tabBar
        objectName: "settingsTabBar"
        x: 24
        y: searchField.y + searchField.height + 14
        width: root.compact ? root.width - 48 : 218
        height: root.compact ? 46 : root.height - y - 24
        compact: root.compact
        tabs: root.tabLabels
        descriptions: root.categoryHints
        currentIndex: root.currentTab
        onSelected: function(index) {
            searchField.clear()
            root.currentTab = index
            Theme.prefs.settingsTab = index
        }
    }

    Item {
        id: pages
        visible: !searchField.text.trim()
        anchors.top: parent.top
        anchors.topMargin: root.compact ? tabBar.y + tabBar.height + 20 : searchField.y
        anchors.left: parent.left
        anchors.leftMargin: root.compact ? 0 : 260
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        // The waiting tabs sit one page margin off each edge; without this they
        // would be visible sliding out past the scroll bar and under the rail.
        clip: true

        // Every tab stays instantiated, as the top-level views do in main.qml.
        // Each keeps its own scroll position, a tab has to exist to glide in,
        // the Connections declared inside one go on being heard while another
        // is up — and a Loader would put the import switch out of reach of the
        // test that finds it by objectName.
        //
        // anchors.fill is not usable here: the whole point is that x moves.
        AppearanceTab {
            objectName: "appearanceTab"
            index: 0
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
        }

        PlaybackTab {
            objectName: "playbackTab"
            index: 1
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
        }

        BackingTab {
            objectName: "backingTab"
            index: 2
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
        }

        LibraryTab {
            objectName: "libraryTab"
            index: 3
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
            onBackupRequested: root.backupRequested(false)
        }

        PluginsTab {
            objectName: "pluginsTab"
            index: 4
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
        }

        SystemTab {
            objectName: "systemTab"
            index: 5
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
            onBackupRequested: root.backupRequested(true)
        }

        AboutTab {
            objectName: "aboutTab"
            index: 6
            width: pages.width
            height: pages.height
            currentIndex: root.currentTab
            viewActive: root.active
            onNotify: function (message, isError) { toast.show(message, isError) }
        }
    }

    ScrollView {
        visible: searchField.text.trim() !== ""
        anchors.fill: pages
        clip: true
        contentWidth: availableWidth
        ScrollBar.vertical: EdgeScrollBar {}
        Column {
            x: 24
            width: parent.width - 48
            spacing: 12
            Text {
                text: root.searchResults.length ? root.searchResults.length + " results" : "No settings found"
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzLarge
                font.weight: Font.DemiBold
            }
            Text {
                visible: root.searchResults.length === 0
                width: parent.width
                text: "Try a different term, like audio, colour, imports or updates."
                wrapMode: Text.WordWrap
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }
            Repeater {
                model: root.searchResults
                delegate: Button {
                    id: result
                    required property var modelData
                    width: parent.width
                    height: resultText.implicitHeight + 32
                    padding: 16
                    Accessible.name: modelData.label + ", " + root.tabLabels[modelData.tab]
                    onClicked: root.selectResult(modelData)
                    background: Rectangle {
                        radius: Theme.radius
                        color: result.down || result.hovered ? Theme.raised : Theme.card
                        border.color: result.visualFocus ? Theme.accent : Theme.lineSoft
                    }
                    contentItem: Column {
                        id: resultText
                        spacing: 5
                        Text { text: root.tabLabels[result.modelData.tab] + (result.modelData.context ? " › " + result.modelData.context : ""); width: parent.width; elide: Text.ElideRight; color: Theme.accent; font.family: Theme.fontFamily; font.pixelSize: Theme.fzTiny }
                        Text { width: parent.width; wrapMode: Text.WordWrap; text: result.modelData.label; color: Theme.text; font.family: Theme.fontFamily; font.pixelSize: Theme.fzBody; font.weight: Font.DemiBold }
                        Text { width: parent.width; visible: text !== ""; wrapMode: Text.WordWrap; text: result.modelData.description; color: Theme.dim; font.family: Theme.fontFamily; font.pixelSize: Theme.fzSmall }
                    }
                }
            }
        }
    }

    // One for the whole page rather than one per tab: it is chrome for Settings
    // as a whole, it must not be clipped by the container above, and a message
    // raised on the tab you just left should not leave with it.
    Toast { id: toast }
}
