# widgets/rounded_card.py
from PySide6.QtWidgets import QWidget, QGraphicsDropShadowEffect, QSizePolicy
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtCore import Qt, QRectF

class RoundedCard(QWidget):
    """
    A rounded card with background color, clipping mask and optional shadow.
    Use this as the container for dock contents.
    """
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.radius = int(getattr(theme, "dock_radius"))
        self.padding = int(getattr(theme, "padding"))
        self.bg = QColor(getattr(theme, "system"))
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        #shadow = QGraphicsDropShadowEffect(self)
        #shadow.setBlurRadius(getattr(theme, "shadow_blur", 20))
        #shadow.setOffset(0, 8)
        #shadow.setColor(QColor(0, 0, 0, 140))
        #self.setGraphicsEffect(shadow)

    # ---------- geometry helpers ----------
    def _inner_rect(self) -> QRectF:
        p = self.padding
        r = self.rect()
        return QRectF(
            r.left() + p,
            r.top() + p,
            r.width() - 2 * p,
            r.height() - 2 * p,
        )

    # ---------- masking ----------
    def _update_mask(self):
        inner = self._inner_rect()
        if inner.width() <= 0 or inner.height() <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(inner, self.radius, self.radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_mask()

    # ---------- painting ----------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.bg)
        inner = self._inner_rect()
        if inner.width() <= 0 or inner.height() <= 0:
            return
        p.drawRoundedRect(inner, self.radius, self.radius)
