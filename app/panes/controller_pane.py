# panes/controller_pane.py
import os
from PySide6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QSizePolicy
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSize

class ControllerPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        layout.addStretch(1)

        res_dir = os.path.join(os.path.dirname(__file__), "..", "graphics")
        def icon(name):
            return QIcon(os.path.join(res_dir, name))

        btn_prev = QToolButton()
        btn_prev.setIcon(icon("prev.svg"))
        btn_prev.setAutoRaise(True)
        btn_prev.setIconSize(QSize(theme.icon_size, theme.icon_size))
        layout.addWidget(btn_prev)

        btn_play = QToolButton()
        btn_play.setIcon(icon("play.svg"))
        btn_play.setAutoRaise(True)
        btn_play.setIconSize(QSize(theme.icon_size + 8, theme.icon_size + 8))
        layout.addWidget(btn_play)

        btn_next = QToolButton()
        btn_next.setIcon(icon("next.svg"))
        btn_next.setAutoRaise(True)
        btn_next.setIconSize(QSize(theme.icon_size, theme.icon_size))
        layout.addWidget(btn_next)

        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
