import sys
from PySide6.QtWidgets import QDockWidget, QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout
from app.theme import Theme
from app.style import apply_global_style
from panes.controller_pane import ControllerPane
from widgets.frameless_dock import FramelessDock
from widgets.app_border import BorderOverlay


class AppWindow(QWidget):
    # The AppWindow QWidget currently exists to contain the QMainWindow that contains the dock widgets.
    # This is useful so we can add a margin between the edge of the screen and the dock widgets.
    def __init__(self, theme: Theme):
        super().__init__()
        self.resize(1200, 760)
        self.setWindowTitle("Kroma Player")

        margin_layout = QHBoxLayout(self)
        margins = getattr(theme, "app_margins")
        margin_layout.setContentsMargins(margins, margins, margins, margins)

        self.main_window = MainWindow(theme)
        margin_layout.addWidget(self.main_window)

        self.border_overlay = BorderOverlay(
            parent=self,
            target=self.main_window,
            theme=theme
        )


class MainWindow(QMainWindow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme

        # This whole section can probably be removed once all of the docks have been added.
        # It's currently just used to allow the docks to not have to fill the entire MainWindow.
        central = QWidget()
        central.setObjectName("central_widget")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(central)

        controller_dock = FramelessDock("Controls", theme, self)
        controller_dock.set_dock_content(ControllerPane(theme))
        self.addDockWidget(Qt.BottomDockWidgetArea, controller_dock)
        controller_dock.setMaximumHeight(140)

        controller_dock2 = FramelessDock("Controls2", theme, self)
        controller_dock2.set_dock_content(ControllerPane(theme))
        self.addDockWidget(Qt.TopDockWidgetArea, controller_dock2)
        controller_dock2.setMaximumHeight(140)

    def closeEvent(self, event):
        # This is where dock layouts could/should be saved.
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
