# main.py - entrypoint

import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout
from app.theme import Theme
from app.style import apply_global_style
from panes.playlist_pane import PlaylistPane
from panes.lyrics_pane import LyricsPane
from panes.controller_pane import ControllerPane
from widgets.frameless_dock import FramelessDock

# Variables for registry setting saves
#APP_BRAND = "Kroma"
#APP_NAME = "KromaPlayer"

class MainWindow(QMainWindow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.setWindowTitle("Kroma Player")
        self.resize(1200, 760)

        margin_container = QWidget()
        layout = QHBoxLayout(margin_container)
        layout.setContentsMargins(50, 50, 50, 50)

        central = QWidget()
        central.setObjectName("central_widget")
        layout.addWidget(central)

        self.setCentralWidget(margin_container)

        # an empty board (will show background)
        layout.addStretch(1)

        controller_dock = FramelessDock("Controls", theme, self)
        controller_dock.set_content_widget(ControllerPane(theme))
        self.addDockWidget(Qt.BottomDockWidgetArea, controller_dock)
        controller_dock.setMaximumHeight(140)

    def closeEvent(self, event):
        # Save settings on close
        #from PySide6.QtCore import QSettings
        #settings = QSettings(APP_BRAND, APP_NAME)
        #settings.setValue("mainwindow/geometry", self.saveGeometry())
        #settings.setValue("mainwindow/state", self.saveState())
        super().closeEvent(event)


if __name__ == "__main__":
    from PySide6.QtCore import Qt
    app = QApplication(sys.argv)

    # create theme and make available
    theme = Theme()
    app.setProperty("theme", theme)

    # apply global stylesheet
    apply_global_style(app, theme)

    w = MainWindow(theme)
    # restore geometry and state settings
    #from app.settings_utils import safe_restore_geometry_and_state
    #safe_restore_geometry_and_state(w, APP_BRAND, APP_NAME)

    w.show()
    sys.exit(app.exec())
