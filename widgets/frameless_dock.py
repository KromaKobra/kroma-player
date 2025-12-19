# widgets/frameless_dock.py
import os
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QLabel, QToolButton,
    QHBoxLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
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

        # remove native title bar (removes close / undock buttons)
        self.setTitleBarWidget(QWidget())

        # Still allow moving/floatable behavior (features control behavior,
        # titlebar widget controls visible buttons)
        self.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )

        # create rounded card and layout (kept your original structure)
        self.card = RoundedCard(theme)
        self.layout = QVBoxLayout(self.card)
        self.layout.setContentsMargins(10, 6, 10, 10)
        self.layout.setSpacing(6)

        # inner content container
        self.inner = QWidget(self.card)
        il = QVBoxLayout(self.inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        self.layout.addWidget(self.inner, 1)

        self.setWidget(self.card)

        # make the dock widget itself translucent background where needed
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        # connect to floating/docked changes
        self.topLevelChanged.connect(self._on_top_level_changed)

        # ensure titlebar is removed even after reparenting; keep a default empty widget
        self.setTitleBarWidget(QWidget())

    def set_content_widget(self, widget: QWidget):
        """Replace inner content with widget."""
        l = self.inner.layout()
        # clear
        while l.count():
            item = l.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        l.addWidget(widget)

    def _apply_mask_to_window(self):
        """Apply rounded mask to the top-level (floating) window."""
        top = self.window()
        if top is None:
            return
        radius = int(getattr(self.theme, "inner_radius", 12))
        rect = top.rect()
        if rect.isEmpty():
            return
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        try:
            top.setMask(region)
        except Exception:
            # some platforms may not like setMask in some states; ignore
            pass

    def _on_top_level_changed(self, floating: bool):
        """
        When floating==True the dock becomes a top-level window.
        Make that top-level window frameless, translucent and apply a rounded mask.
        When docked again, clear translucency and mask so the main window frame remains.
        """
        top = self.window()
        if top is None:
            return

        if floating:
            # Make the floating top-level window frameless and tool-like so it doesn't
            # get a regular window titlebar from the OS.
            # IMPORTANT: change flags on the top-level window object, then show() to apply.
            top.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
            top.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)

            # show to apply flags; schedule mask application on next event loop turn
            top.show()
            QTimer.singleShot(0, self._apply_mask_to_window)
        else:
            # docked back into the main window: restore regular widget flags and clear mask
            try:
                # Clear mask and translucency on top-level container (if any).
                top.clearMask()
            except Exception:
                pass
            top.setAttribute(Qt.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WA_TranslucentBackground, False)

            # Reset flags for child widget so it behaves as a normal dock widget inside the main window
            # Use Qt.Widget to indicate it's not a standalone top-level window.
            top.setWindowFlags(Qt.Widget)
            top.show()

