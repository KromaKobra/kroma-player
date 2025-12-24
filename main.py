import sys
from PySide6.QtWidgets import QDockWidget, QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFrame
from app.theme import Theme
from app.style import apply_global_style
from panes.controller_pane import ControllerPane
from widgets.frameless_dock import FramelessDock
from widgets.app_border import BorderOverlay
from widgets.rounded_card import RoundedCard


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

        central = QWidget()
        layout1 = QVBoxLayout(central)
        layout1.setContentsMargins(0, 0, 0, 0)

        controller = RoundedCard(theme)
        controller.setMaximumHeight(75)
        controller.set_content_widget(ControllerPane(theme))
        library = RoundedCard(theme)
        library.setMaximumWidth(225)
        visualizer = RoundedCard(theme)
        visualizer.setMaximumHeight(150)
        playlist = RoundedCard(theme)
        info = RoundedCard(theme)
        info.setMaximumWidth(225)

        widget2 = QWidget()
        layout1.addWidget(widget2)
        layout1.addWidget(controller)
        layout2 = QHBoxLayout(widget2)
        layout2.setContentsMargins(0, 0, 0, 0)

        widget3 = QWidget()
        layout2.addWidget(library)
        layout2.addWidget(widget3)
        layout3 = QVBoxLayout(widget3)
        layout3.setContentsMargins(0, 0, 0, 0)

        widget4 = QWidget()
        layout3.addWidget(widget4)
        layout3.addWidget(visualizer)
        layout4 = QHBoxLayout(widget4)
        layout4.setContentsMargins(0, 0, 0, 0)

        layout4.addWidget(playlist)
        layout4.addWidget(info)

        self.setCentralWidget(central)


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
