# widgets/rounded_card.py
from PySide6.QtWidgets import QWidget, QGraphicsDropShadowEffect, QSizePolicy
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtCore import Qt

class RoundedCard(QWidget):
    """
    A rounded card with background color, clipping mask and optional shadow.
    Use this as the container for dock contents.
    """
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.radius = int(getattr(theme, "inner_radius", 12))
        self.bg = QColor(getattr(theme, "dock_color", "#0b1220"))
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(getattr(theme, "shadow_blur", 20))
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

    def _update_mask(self):
        path = QPainterPath()
        path.addRoundedRect(self.rect(), self.radius, self.radius)
        region = path.toFillPolygon().toPolygon()
        from PySide6.QtGui import QRegion
        self.setMask(QRegion(region))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_mask()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.bg)
        p.drawRoundedRect(self.rect(), self.radius, self.radius)
