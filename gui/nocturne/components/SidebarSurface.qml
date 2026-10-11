import QtQuick
import Nocturne

// The window's AmbientBackdrop already blurs the artwork. Let that shared
// wash show through instead of sampling and blurring it again per sidebar.
Rectangle {
    gradient: Gradient {
        GradientStop {
            position: 0.0
            color: Theme.prefs.ambient ? Theme.alpha(Theme.panelBg, 0.50) : Theme.panelBg
        }
        GradientStop {
            position: 0.55
            color: Theme.prefs.ambient ? Theme.alpha(Theme.panelBg, 0.62) : Theme.panelBg
        }
        GradientStop {
            position: 1.0
            color: Theme.prefs.ambient ? Theme.alpha(Theme.panelBg, 0.76) : Theme.panelBg
        }
    }

    // A quiet reflection gives the material depth while the heavier tint
    // below keeps navigation and metadata readable in either colour mode.
    Rectangle {
        anchors.fill: parent
        visible: Theme.prefs.ambient
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.alpha(Theme.text, 0.035) }
            GradientStop { position: 0.45; color: Theme.alpha(Theme.text, 0.01) }
            GradientStop { position: 1.0; color: Theme.alpha(Theme.text, 0.0) }
        }
    }
}
