import QtQuick
import Nocturne
import "../components"
import "settings"

// Seven tabs' worth of settings, laid out left to right.
//
// This page used to be one column of ten sections, which put the instrumental
// gain below the accent swatches and the vault path below the whole
// separation-model setup. The tabs give it somewhere to put things; the glide
// gives them somewhere to be. Each tab is a file of its own under settings/,
// and each carries the menus, dialogs and backend refreshes that belong to it —
// what is left here is the chrome they share.
Item {
    id: root

    property bool active: false

    // A tab asked for the backup dialog, which lives on the Library page. The
    // window owns going there.
    signal backupRequested()

    readonly property string guiVersion:
        typeof guiVM !== "undefined" ? guiVM.currentVersion : ""

    readonly property var tabLabels:
        ["Appearance", "Playback", "Backing", "Library", "Plugins", "System", "About"]

    // Show one tab by its label — for a dialog elsewhere whose answer lives
    // here ("Open Settings → Plugins"). Goes the way a click does.
    function openTab(label) {
        var index = tabLabels.indexOf(label)
        if (index < 0)
            return
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

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Settings"
        subtitle: "Nocturne " + root.guiVersion
    }

    SettingsTabBar {
        id: tabBar
        objectName: "settingsTabBar"
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        tabs: root.tabLabels
        currentIndex: root.currentTab
        onSelected: function (index) {
            root.currentTab = index
            Theme.prefs.settingsTab = index
        }
    }

    Item {
        id: pages
        anchors.top: tabBar.bottom
        anchors.topMargin: Theme.gapLg
        anchors.left: parent.left
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
            onBackupRequested: root.backupRequested()
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

    // One for the whole page rather than one per tab: it is chrome for Settings
    // as a whole, it must not be clipped by the container above, and a message
    // raised on the tab you just left should not leave with it.
    Toast { id: toast }
}
