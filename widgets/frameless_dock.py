from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QApplication, QMainWindow
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint, QRect
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen, QBrush, QRegion
from .rounded_card import RoundedCard


class DockPreviewOverlay(QWidget):
    """
    A translucent rounded-rectangle overlay shown over the main window to preview
    where a dock widget will be attached.

    Usage:
        preview = DockPreviewOverlay(parent_main_window)
        preview.show_preview(area=Qt.LeftDockWidgetArea, ratio=0.25)
        ...
        preview.hide_preview()
    """

    def __init__(
        self,
        parent: QMainWindow,
        color: QColor = QColor(40, 120, 220),
        fill_opacity: float = 0.18,
        border_color: QColor = QColor(40, 120, 220),
        border_opacity: float = 0.9,
        border_width: int = 2,
        radius: int = 10,
    ):
        super().__init__(parent)
        self._main = parent
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.Widget)

        # appearance
        self.color = color
        self.fill_opacity = fill_opacity
        self.border_color = border_color
        self.border_opacity = border_opacity
        self.border_width = border_width
        self.radius = radius

        # preview geometry (in main window coordinates)
        self._visible = False
        self._rect = QRect()

        # ensure overlay fills main's client area
        if parent:
            self.setParent(parent)
            self.resize(parent.size())
            self.move(0, 0)

    def show_preview(self, area: Qt.DockWidgetArea, main: QMainWindow, ratio: float = 0.25):
        """
        Compute the preview rectangle for `area` in `main` and show it.
        ratio: fraction of width/height the preview should occupy (0.0-1.0)
        """
        if main is None:
            return
        self._main = main
        self.setParent(main)
        self.resize(main.size())
        self.move(0, 0)

        rect = main.rect()  # in main coordinates
        w = rect.width()
        h = rect.height()
        r = max(0.0, min(1.0, float(ratio)))

        if area == Qt.LeftDockWidgetArea:
            w2 = max(40, int(w * r))
            self._rect = QRect(rect.left(), rect.top(), w2, h)
        elif area == Qt.RightDockWidgetArea:
            w2 = max(40, int(w * r))
            self._rect = QRect(rect.right() - w2 + 1, rect.top(), w2, h)
        elif area == Qt.TopDockWidgetArea:
            h2 = max(40, int(h * r))
            self._rect = QRect(rect.left(), rect.top(), w, h2)
        else:  # Bottom
            h2 = max(40, int(h * r))
            self._rect = QRect(rect.left(), rect.bottom() - h2 + 1, w, h2)

        self._visible = True
        self.update()
        self.show()

    def hide_preview(self):
        self._visible = False
        self.hide()
        self.update()

    def paintEvent(self, ev):
        if not self._visible or self._rect.isEmpty():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # fill
        fill = QColor(self.color)
        fill.setAlphaF(self.fill_opacity)
        brush = QBrush(fill)
        p.setBrush(brush)
        # border
        border = QColor(self.border_color)
        border.setAlphaF(self.border_opacity)
        pen = QPen(border, self.border_width)
        p.setPen(pen)

        path = QPainterPath()
        path.addRoundedRect(self._rect, self.radius, self.radius)
        p.drawPath(path)
        p.end()


class FramelessDock(QDockWidget):
    def __init__(self, title: str, theme, parent=None):
        super().__init__(title, parent)
        self.theme = theme

        # Allow movable/floatable
        self.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )

        # DockWidget background
        self.card = RoundedCard(theme)
        # Layout will contain content from panes/ (Set with self.set_dock_content(self, widget))
        self.layout = QVBoxLayout(self.card)
        # Important so that the QDockWidget actually displays the card and content
        self.setWidget(self.card)

        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.topLevelChanged.connect(self._on_top_level_changed)
        self.setTitleBarWidget(QWidget())       # Set titlebar to an empty QWidget to make it disappear

        # Drag state
        self._dragging = False
        self._press_pos_local = QPoint(0, 0)
        self._press_pos_global = QPoint(0, 0)
        self._started_floating_during_drag = False

        # preview support
        self._dock_candidate_area = None
        self._dock_margin_threshold = 100
        self._preview_ratio = 0.25  # default preview size fraction

        # placeholder dock used to shift other docks during preview
        self._placeholder_dock = None

        # overlay preview (created lazy when needed)
        self._preview_overlay = None

        self.card.installEventFilter(self)  # Events targeting the card will be filtered by eventFilter()

    def set_dock_content(self, widget: QWidget):
        self.layout.addWidget(widget)

    # ---------- event filter ----------
    def eventFilter(self, obj, event):
        if obj is self.card:
            et = event.type()
            if et == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self._dragging = True
                    self._started_floating_during_drag = False
                    self._dock_candidate_area = None
                    self._press_pos_local = event.pos()
                    self._press_pos_global = event.globalPos()
                    return True

            elif et == QEvent.MouseMove:
                if not self._dragging:
                    return False

                if not self.isFloating():
                    dist = (event.globalPos() - self._press_pos_global).manhattanLength()   # Dist window been dragged
                    if dist >= QApplication.startDragDistance():
                        self._start_floating_at(event.globalPos(), event.pos())
                    return True
                else:
                    # move top-level window
                    top = self.window()     # Floating window
                    if top:
                        # Dist mouse has moved away from the loc on the window
                        new_topleft = event.globalPos() - self._press_pos_local
                        top.move(new_topleft)   # Move the window so the cursor stays in the same spot inside the dock

                    # update candidate area, preview + placeholder
                    new_area = self._area_for_point(event.globalPos())  # Find new preview area
                    if new_area != self._dock_candidate_area:           # The area has changed from the frame before
                        self._dock_candidate_area = new_area
                        if new_area is not None:                        # Show the preview only if the area exists
                            self._show_preview(new_area)
                        else:
                            self._hide_preview()
                    return True

            elif et == QEvent.MouseButtonRelease:
                if self._dragging and event.button() == Qt.LeftButton:
                    self._dragging = False
                    if self.isFloating() and self._dock_candidate_area is not None:
                        self._dock_to(self._dock_candidate_area)
                    self._hide_preview()
                    self._dock_candidate_area = None
                    self._started_floating_during_drag = False
                    return True

        return super().eventFilter(obj, event)

    def _start_floating_at(self, global_pos, local_pos):
        if self._started_floating_during_drag:
            return
        self._started_floating_during_drag = True
        self._press_pos_global = global_pos
        self._press_pos_local = local_pos
        self.setFloating(True)

        def _move_top():
            top = self.window()
            if top:
                try:
                    new_topleft = global_pos - local_pos
                    top.move(new_topleft)
                except Exception:
                    pass

        QTimer.singleShot(0, _move_top)

    # ---------- preview + placeholder helpers ----------
    def _find_main_window(self):
        # Walk up parents to find the QMainWindow that can accept docks (or None).
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QMainWindow):
                return w
            w = w.parentWidget()
        top = self.window()
        if isinstance(top, QMainWindow):
            return top
        return None

    def _show_preview(self, area):
        """
        Display overlay and insert placeholder dock to shift other docks.
        """
        main = self._find_main_window()
        if main is None:
            return

        # create overlay if needed
        if self._preview_overlay is None or self._preview_overlay.parent() != main:
            self._preview_overlay = DockPreviewOverlay(parent=main,
                                                      color=QColor(30, 144, 255),
                                                      fill_opacity=0.16,
                                                      border_color=QColor(30, 144, 255),
                                                      border_opacity=0.95,
                                                      border_width=2,
                                                      radius=int(getattr(self.theme, "dock_radius")))
        self._preview_overlay.show_preview(area, main, ratio=self._preview_ratio)

        # Insert placeholder dock widget so other docks are shifted.
        # If a placeholder exists and is already in the same area, leave it.
        if self._placeholder_dock is not None:
            # If there is a placeholder but not in the requested area, remove it first.
            try:
                cur_main = self._placeholder_dock.parent()
                # remove and recreate if necessary
                if cur_main is not main:
                    self._remove_placeholder()
            except Exception:
                self._remove_placeholder()

        if self._placeholder_dock is None:
            # Create minimal placeholder
            placeholder = QDockWidget("", main)
            placeholder.setTitleBarWidget(QWidget())  # hide title bar
            placeholder.setFeatures(QDockWidget.NoDockWidgetFeatures)
            placeholder.setAllowedAreas(Qt.AllDockWidgetAreas)

            # Size hint: match preview size to force layout changes
            mw = main.rect().width()
            mh = main.rect().height()
            if area in (Qt.LeftDockWidgetArea, Qt.RightDockWidgetArea):
                placeholder.setFixedWidth(max(40, int(mw * self._preview_ratio)))
            else:
                placeholder.setFixedHeight(max(40, int(mh * self._preview_ratio)))

            # Add the placeholder to requested area. This will shift other docks visually.
            try:
                main.addDockWidget(area, placeholder)
            except Exception:
                # If addDockWidget fails, silently ignore; overlay still shows.
                pass

            # keep reference
            self._placeholder_dock = placeholder

    def _remove_placeholder(self):
        if self._placeholder_dock is None:
            return
        main = self._find_main_window()
        if main is None:
            # try safe delete
            self._placeholder_dock.setParent(None)
            self._placeholder_dock.deleteLater()
            self._placeholder_dock = None
            return

        try:
            main.removeDockWidget(self._placeholder_dock)
        except Exception:
            # fallback: unparent and delete
            try:
                self._placeholder_dock.setParent(None)
            except Exception:
                pass
        try:
            self._placeholder_dock.deleteLater()
        except Exception:
            pass
        self._placeholder_dock = None

    def _hide_preview(self):
        if self._preview_overlay:
            self._preview_overlay.hide_preview()
        self._remove_placeholder()

    # ---------- docking helpers ----------
    def _area_for_point(self, global_pos: QPoint):
        main = self._find_main_window()
        if main is None:
            return None

        local = main.mapFromGlobal(global_pos)
        rect = main.rect()
        margin = self._dock_margin_threshold
        expanded = rect.adjusted(-margin, -margin, margin, margin)
        if not expanded.contains(local):
            return None

        x = local.x()
        y = local.y()
        w = rect.width()
        h = rect.height()

        d_left = x
        d_right = w - x
        d_top = y
        d_bottom = h - y

        min_d = min(d_left, d_right, d_top, d_bottom)

        if min_d == d_left:
            return Qt.LeftDockWidgetArea
        if min_d == d_right:
            return Qt.RightDockWidgetArea
        if min_d == d_top:
            return Qt.TopDockWidgetArea
        return Qt.BottomDockWidgetArea

    def _dock_to(self, area):
        main = self._find_main_window()
        if main is None:
            return
        try:
            # remove placeholder (the UI already shifted); then re-add this dock into the target area
            self._remove_placeholder()
            main.addDockWidget(area, self)
            self.setFloating(False)
            self.show()
        except Exception:
            pass

    # ---------- mask & top-level handling ----------
    def _apply_mask_to_window(self):
        top = self.window()
        if top is None:
            return
        radius = int(getattr(self.theme, "dock_radius", 12))
        rect = top.rect()
        if rect.isEmpty():
            return
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        try:
            top.setMask(region)
        except Exception:
            pass

    def _on_top_level_changed(self, floating: bool):
        top = self.window()
        if top is None:
            return

        if floating:
            top.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
            top.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            top.show()
            QTimer.singleShot(0, self._apply_mask_to_window)
        else:
            try:
                top.clearMask()
            except Exception:
                pass
            top.setAttribute(Qt.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WA_TranslucentBackground, False)
            top.setWindowFlags(Qt.Widget)
            top.show()
