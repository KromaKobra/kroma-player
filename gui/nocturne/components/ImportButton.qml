import QtQuick
import QtQuick.Controls
import Nocturne
import "ImportSources.js" as Sources

// The import control: one button for the common source, a chevron for the rest.
//
// Both the Library header and the Import header used to carry the same three
// buttons — files, folder, CSV — declared twice and taking up most of the
// action row between them. They were always one action with alternatives: each
// of the six only ever handed a `kind` string to the same place. This is that
// shape drawn honestly, and it is the same control on both pages, so the two
// can no longer drift apart.
//
// The sources themselves are not here. They are in ImportSources.js, which is
// what makes adding one an entry in a list rather than new QML in two views.
Item {
    id: root

    // Follows AppButton: `enabled` does nothing visible on these controls, so
    // callers reach for this instead and the two halves read it together.
    property bool enabledAction: true
    // "filled" | "soft" | "ghost", as AppButton means them.
    property string variant: "soft"
    property int hPadding: 16
    property int fontSize: Theme.fzBody
    // Wide enough for a 16px glyph to sit in without crowding the divider.
    property int arrowWidth: 30

    // The chosen source, by `kind`. One signal for both halves: the caller has
    // a single place to send a kind, which is all either page ever wanted.
    signal sourceChosen(string kind)

    // What this button offers. The catalog by default — which is the whole
    // point of it — but settable, so the control can be reused with a narrower
    // list without the list having to be global to do it.
    property var sources: Sources.sourcesFor(
        typeof pluginsVM !== "undefined" && pluginsVM ? pluginsVM.hasAudioSource : false,
        typeof pluginsVM !== "undefined" && pluginsVM ? pluginsVM.importSources : [])

    // The split the catalog documents: first entry is the half you can click
    // without reading anything, the rest are behind the chevron.
    readonly property var primary: sources[0]
    readonly property var alternates: sources.slice(1)

    implicitHeight: 36
    implicitWidth: label.implicitWidth + hPadding * 2 + arrowWidth

    readonly property color baseColor: Theme.accent
    readonly property real primaryWidth: width - arrowWidth

    // AppButton's variant logic, per half rather than per button — which is the
    // whole reason this does not just contain two AppButtons. Hover has to be
    // answerable separately for each side, and AppButton keeps its MouseArea
    // to itself.
    function fillFor(lit) {
        if (variant === "filled")
            return lit ? Qt.lighter(baseColor, 1.14) : baseColor
        if (variant === "soft")
            return lit ? Theme.alpha(baseColor, 0.22) : Theme.alpha(baseColor, 0.13)
        return lit ? Theme.alpha(Theme.text, 0.08) : "transparent"
    }

    readonly property color ink: variant === "filled" ? Theme.accentInk : Theme.text

    opacity: enabledAction ? 1 : 0.4
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    Item {
        id: surface
        anchors.fill: parent

        // Pressing either half dips the whole control. A button that is one
        // shape has to move as one, however many things you can click on it.
        scale: (primaryMouse.pressed || arrowMouse.pressed) && root.enabledAction
               ? 0.975 : 1.0
        Behavior on scale {
            NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
        }

        // Two halves of one shape.
        //
        // Each draws the *whole* rounded rectangle and shows only its own slice
        // through a rectangular clip. So the outer corners stay round, the
        // inner edges come out square, and the halves tint independently.
        //
        // Not two overlapping rounded rectangles, the usual trick: `soft` is a
        // translucent fill, and two of them would stack to a visibly darker
        // band wherever they met. Not Qt 6.7's per-corner radii either —
        // requirements.txt still admits PySide6 6.6, where they do not exist.
        Item {
            id: primaryHalf
            anchors.left: parent.left
            width: root.primaryWidth
            height: parent.height
            clip: true

            Rectangle {
                width: root.width
                height: root.height
                radius: Theme.radius
                color: root.fillFor(primaryMouse.containsMouse)
                border.width: root.variant === "ghost" ? 1 : 0
                border.color: Theme.line
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
        }

        Item {
            id: arrowHalf
            anchors.right: parent.right
            width: root.arrowWidth
            height: parent.height
            clip: true

            Rectangle {
                x: -root.primaryWidth
                width: root.width
                height: root.height
                radius: Theme.radius
                // Lit while the menu is up, not only while hovered: the pointer
                // moves onto the popup the moment it opens, and a chevron that
                // went flat under an open menu would read as having lost it.
                color: root.fillFor(arrowMouse.containsMouse || sourceMenu.visible)
                border.width: root.variant === "ghost" ? 1 : 0
                border.color: Theme.line
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
        }

        // The seam. Drawn over both halves so it reads as one control divided,
        // rather than two controls that happen to touch.
        Rectangle {
            anchors.right: arrowHalf.left
            anchors.verticalCenter: parent.verticalCenter
            width: 1
            height: parent.height - 10
            color: Theme.line
        }

        Row {
            id: label
            anchors.verticalCenter: parent.verticalCenter
            x: (root.primaryWidth - implicitWidth) / 2
            spacing: 8

            Icon {
                anchors.verticalCenter: parent.verticalCenter
                name: root.primary.icon
                size: 16
                color: root.ink
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.primary.label
                color: root.ink
                font.family: Theme.fontFamily
                font.pixelSize: root.fontSize
                font.weight: Font.DemiBold
            }
        }

        Icon {
            anchors.centerIn: arrowHalf
            name: "chevronDown"
            size: 16
            color: root.ink
        }
    }

    MouseArea {
        id: primaryMouse
        anchors.left: parent.left
        width: root.primaryWidth
        height: parent.height
        hoverEnabled: true
        enabled: root.enabledAction
        cursorShape: Qt.PointingHandCursor
        onClicked: root.sourceChosen(root.primary.kind)
    }

    MouseArea {
        id: arrowMouse
        anchors.right: parent.right
        width: root.arrowWidth
        height: parent.height
        hoverEnabled: true
        enabled: root.enabledAction
        cursorShape: Qt.PointingHandCursor
        onClicked: root.openMenu()
    }

    // Every other menu in the interface opens with a bare `popup()`, which
    // anchors to the cursor — right for something you reached by right-clicking
    // a row, wrong for a dropdown, which has to hang off the thing it belongs
    // to wherever you happened to click it.
    //
    // The menu is declared inside this item, so its parent is already this
    // item and x/y are measured from here; that is why this sets coordinates
    // and calls `open()` rather than going through `popup()` at all.
    function openMenu() {
        // `implicitWidth`, not `width`: NocturneMenu and MenuRow both fix it at
        // 232, and `width` is not resolved until the first time it opens.
        //
        // Right-aligned, and wider than the button, so it hangs left into the
        // page. Both headers sit hard against the window's right margin, where
        // a left-aligned menu would be the one that ran off the edge.
        sourceMenu.x = root.width - sourceMenu.implicitWidth
        sourceMenu.y = root.height + 4
        sourceMenu.open()
    }

    // Built with an Instantiator, not a Repeater: a Repeater inside a Menu
    // parents its items to the internal list instead of registering them as
    // entries, so they never appear (the same trap the Library sort menu and
    // the backing-track model menu both document).
    NocturneMenu {
        id: sourceMenu

        Instantiator {
            model: root.alternates
            onObjectAdded: function (index, object) {
                sourceMenu.insertItem(index, object)
            }
            onObjectRemoved: function (index, object) {
                sourceMenu.removeItem(object)
            }

            delegate: MenuRow {
                required property var modelData
                text: modelData.label
                iconName: modelData.icon
                iconSource: modelData.iconUrl || ""
                onTriggered: root.sourceChosen(modelData.kind)
            }
        }
    }
}
