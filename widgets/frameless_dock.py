# widgets/frameless_dock.py
import os
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QLabel, QToolButton,
    QHBoxLayout, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainterPath, QRegion
from .rounded_card import RoundedCard


class CustomTitleBar(QWidget):
    def __init__(self, dock: QDockWidget, title: str, theme):
        super().__init__(dock)
        self.dock = dock
        self.theme = theme
        self._drag_pos = None
        self.setFixedHeight(40)
        self.setContentsMargins(6, 0, 6, 0)

        # horizontal layout for title + buttons
        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"font-weight:600; color:{theme.text};")
        hl.addWidget(self.title_label)
        hl.addStretch(1)

        # helper to load icons from resources folder
        res_dir = os.path.join(os.path.dirname(__file__), "..", "resources")
        def icon(name):
            p = os.path.join(res_dir, name)
            return QIcon(p)

        # pop/dock button
        self.btn_pop = QToolButton(self)
        self.btn_pop.setIcon(icon("pop.svg"))
        self.btn_pop.setToolTip("Pop / Dock")
        self.btn_pop.setAutoRaise(True)
        self.btn_pop.clicked.connect(self._toggle)

        # close button
        self.btn_close = QToolButton(self)
        self.btn_close.setIcon(icon("close.svg"))
        self.btn_close.setToolTip("Close")
        self.btn_close.setAutoRaise(True)
        self.btn_close.clicked.connect(lambda: self.dock.close())

        hl.addWidget(self.btn_pop)
        hl.addWidget(self.btn_close)

    def _toggle(self):
        self.dock.setFloating(not self.dock.isFloating())

    # dragging when floating
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.dock.isFloating():
            self._drag_pos = ev.globalPosition().toPoint() - self.dock.frameGeometry().topLeft()
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_pos and self.dock.isFloating() and (ev.buttons() & Qt.LeftButton):
            newpos = ev.globalPosition().toPoint() - self._drag_pos
            self.dock.move(newpos)
            ev.accept()
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._drag_pos = None
        super().mouseReleaseEvent(ev)


class FramelessDock(QDockWidget):
    def __init__(self, title: str, theme, parent=None):
        super().__init__(title, parent)
        self.theme = theme

        # hide native title and use our own controls
        self.setWindowTitle("")
        self.setFeatures(QDockWidget.DockWidgetClosable
                         | QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable)

        # create rounded card and layout
        self.card = RoundedCard(theme)
        self.layout = QVBoxLayout(self.card)
        self.layout.setContentsMargins(10, 6, 10, 10)
        self.layout.setSpacing(6)

        # custom title bar (added into card)
        self.titlebar = CustomTitleBar(self, title, theme)
        self.layout.addWidget(self.titlebar)

        # separator line
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.02);")
        self.layout.addWidget(sep)

        # inner content container
        self.inner = QWidget(self.card)
        il = QVBoxLayout(self.inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        self.layout.addWidget(self.inner, 1)

        self.setWidget(self.card)

        # connect top-level (floating) changes
        self.topLevelChanged.connect(self._on_top_level_changed)

    def set_content_widget(self, widget):
        # clear current layout of inner and add widget
        l = self.inner.layout()
        while l.count():
            item = l.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        l.addWidget(widget)

    def _apply_mask_to_window(self):
        top = self.window()
        if top is None:
            return
        radius = int(getattr(self.theme, "inner_radius", 12))
        path = QPainterPath()
        path.addRoundedRect(top.rect(), radius, radius)
        region = path.toFillPolygon().toPolygon()
        top.setMask(QRegion(region))

    def _on_top_level_changed(self, floating: bool):
        # When floating = True => top-level window. Remove native frame & make translucent.
        top = self.window()
        if floating:
            flags = (Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
            self.setWindowFlags(flags)
            # translucency for rounded corners
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            if top:
                top.setAttribute(Qt.WA_TranslucentBackground, True)
            # re-show to apply flags
            self.show()
            # apply mask so OS doesn't draw rectangular frame edges
            self._apply_mask_to_window()
        else:
            # docked back: remove translucency and clear mask
            self.setAttribute(Qt.WA_TranslucentBackground, False)
            if top:
                top.setAttribute(Qt.WA_TranslucentBackground, False)
                try:
                    top.clearMask()
                except Exception:
                    pass
            # ensure flags refreshed
            self.show()
