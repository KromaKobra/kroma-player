#!/usr/bin/env python3
# main.py - entrypoint

import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout
from app.theme import Theme
from app.style import apply_global_style
from panes.playlist_pane import PlaylistPane
from panes.lyrics_pane import LyricsPane
from panes.controller_pane import ControllerPane
from widgets.frameless_dock import FramelessDock

APP_BRAND = "Kroma"
APP_NAME = "KromaPlayer"

class MainWindow(QMainWindow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.setWindowTitle("Kroma Player")
        self.resize(1200, 760)

        # central content
        central = QWidget()
        central.setObjectName("central_widget")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)

        # an empty board (will show background)
        layout.addStretch(1)
        self.setCentralWidget(central)

        # create docks
        playlist_dock = FramelessDock("Playlist", theme, self)
        playlist_dock.set_content_widget(PlaylistPane(theme))
        self.addDockWidget(Qt.LeftDockWidgetArea, playlist_dock)
        playlist_dock.setMinimumWidth(260)

        lyrics_dock = FramelessDock("Lyrics", theme, self)
        lyrics_dock.set_content_widget(LyricsPane(theme))
        self.addDockWidget(Qt.RightDockWidgetArea, lyrics_dock)
        lyrics_dock.setMinimumWidth(300)

        controller_dock = FramelessDock("Controls", theme, self)
        controller_dock.set_content_widget(ControllerPane(theme))
        self.addDockWidget(Qt.BottomDockWidgetArea, controller_dock)
        controller_dock.setMaximumHeight(140)

    def closeEvent(self, event):
        from PySide6.QtCore import QSettings
        settings = QSettings(APP_BRAND, APP_NAME)
        settings.setValue("mainwindow/geometry", self.saveGeometry())
        settings.setValue("mainwindow/state", self.saveState())
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
    # restore geometry and state
    from app.settings_utils import safe_restore_geometry_and_state
    safe_restore_geometry_and_state(w, APP_BRAND, APP_NAME)

    w.show()
    sys.exit(app.exec())
