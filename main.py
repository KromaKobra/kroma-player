# main.py - entrypoint

import sys
from PySide6.QtWidgets import QDockWidget, QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout
from app.theme import Theme
from app.style import apply_global_style
from panes.playlist_pane import PlaylistPane
from panes.lyrics_pane import LyricsPane
from panes.controller_pane import ControllerPane
from widgets.frameless_dock import FramelessDock


class AppWindow(QWidget):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.resize(1200, 760)
        self.setWindowTitle("Kroma Player")

        margin_layout = QHBoxLayout(self)
        margin_layout.setContentsMargins(50, 50, 50, 50)

        main_window = MainWindow(theme)
        margin_layout.addWidget(main_window)


class MainWindow(QMainWindow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme

        central = QWidget()
        central.setObjectName("central_widget")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.setCentralWidget(central)

        # an empty board (will show background)
        #layout.addStretch(1)

        controller_dock = FramelessDock("Controls", theme, self)
        controller_dock.set_content_widget(ControllerPane(theme))
        self.addDockWidget(Qt.BottomDockWidgetArea, controller_dock)
        controller_dock.setMaximumHeight(140)

    def closeEvent(self, event):
        super().closeEvent(event)


if __name__ == "__main__":
    from PySide6.QtCore import Qt
    app = QApplication(sys.argv)

    # create theme and make available
    theme = Theme()
    app.setProperty("theme", theme)

    # apply global stylesheet
    apply_global_style(app, theme)

    app_window = AppWindow(theme)

    app_window.show()
    sys.exit(app.exec())
