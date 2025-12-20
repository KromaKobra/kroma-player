# app_border.py  (note: "border", not "boarder")
from PySide6.QtCore import Qt, QEvent, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath
from PySide6.QtWidgets import QWidget

class BorderOverlay(QWidget):
    def __init__(self, parent: QWidget, target: QWidget,
                 outer_expand: int = 16,
                 inset_from_margin: int = 12,
                 radius: int = 14,
                 pen_width: int = 6,
                 color: QColor = QColor(0, 0, 0)):
        super().__init__(parent)
        self.target = target
        self.outer_expand = outer_expand
        self.inset_from_margin = inset_from_margin
        self.radius = radius
        self.pen_width = pen_width
        self.color = color

        # Make overlay transparent to mouse and background
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)

        # Make sure the overlay covers the parent initially
        self.setGeometry(parent.rect())
        self.raise_()        # stack on top
        self.show()

        # Watch for geometry changes on target and parent
        self.target.installEventFilter(self)
        self.parent().installEventFilter(self)

    def eventFilter(self, obj, event):
        # Update geometry if parent size changed so overlay continues to cover parent
        if obj is self.parent() and event.type() in (QEvent.Resize, QEvent.Show, QEvent.Move):
            self.setGeometry(self.parent().rect())
            self.raise_()
            self.update()
        # If target moved/resized/visibility changed, repaint (target.geometry() is in parent coords)
        if obj is self.target and event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide):
            # ensure overlay still covers parent (target could be moved by layout changes)
            self.setGeometry(self.parent().rect())
            self.raise_()
            self.update()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        if not self.target.isVisible():
            return

        rect = self.target.geometry()
        if rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # -------- compute the rectangle we want to cut out (inner rounded rect) --------
        draw_rect = rect.adjusted(
            -self.outer_expand + self.inset_from_margin,
            -self.outer_expand + self.inset_from_margin,
            self.outer_expand - self.inset_from_margin,
            self.outer_expand - self.inset_from_margin,
        )

        # adjust for half pen width so stroke sits nicely centered on the edge
        half = self.pen_width / 2.0
        inner_rf = QRectF(draw_rect)
        inner_rf.adjust(half, half, -half, -half)

        # ------------------ fill outside the rounded rect ------------------
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))  # full overlay area
        path.addRoundedRect(inner_rf, self.radius, self.radius)  # inner rounded rect
        path.setFillRule(Qt.OddEvenFill)  # fill outer minus inner

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)  # black (or any QColor)
        painter.drawPath(path)

        # ------------------ draw the border stroke on top ------------------
        pen = QPen(self.color)
        pen.setWidth(self.pen_width)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(inner_rf, self.radius, self.radius)

        painter.end()

