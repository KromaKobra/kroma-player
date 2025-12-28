from PySide6.QtWidgets import QTableView, QStyledItemDelegate, QStyle
from PySide6.QtGui import QPainter, QFont, QColor
from PySide6.QtCore import Qt, QRect, QModelIndex, QSize

class PlaylistTable(QTableView):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setMouseTracking(True)
        self._hover_row = -1
        # convert theme.system2 to QColor if needed
        raw = getattr(theme, "system2", QColor(80, 160, 255, 40))
        self._hover_color = QColor(raw) if not isinstance(raw, QColor) else raw

        # row selection like before
        self.setSelectionBehavior(QTableView.SelectRows)

    def _row_rect(self, row):
        if row < 0 or row >= self.model().rowCount():
            return QRect()
        y = self.rowViewportPosition(row)
        h = self.rowHeight(row)
        if h <= 0:
            return QRect()
        return QRect(0, y, self.viewport().width(), h)

    def mouseMoveEvent(self, event):
        try:
            y = int(event.position().y())
        except Exception:
            y = int(event.y())
        new_row = self.rowAt(y)
        if new_row != self._hover_row:
            old_rect = self._row_rect(self._hover_row)
            new_rect = self._row_rect(new_row)
            if not old_rect.isNull() or not new_rect.isNull():
                union = old_rect.united(new_rect)
                self.viewport().update(union)
            else:
                self.viewport().update()
            self._hover_row = new_row
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_row != -1:
            old_rect = self._row_rect(self._hover_row)
            if not old_rect.isNull():
                self.viewport().update(old_rect)
            else:
                self.viewport().update()
            self._hover_row = -1
        super().leaveEvent(event)

    def paintEvent(self, event):
        # Paint hover rectangle before default painting (so delegate paints on top)
        painter = QPainter(self.viewport())
        if 0 <= self._hover_row < (self.model().rowCount() if self.model() else 0):
            r = self._row_rect(self._hover_row)
            if not r.isNull():
                painter.fillRect(r, self._hover_color)
        painter.end()
        super().paintEvent(event)


class PlaylistDelegate(QStyledItemDelegate):
    """
    Paints each column:
      - col 0: index (small, indented)
      - col 1: title (bold) above artist (muted)
      - col 2: album (muted)
      - col 3: time (right/center)
    Also paints hover background when option.state indicates mouse-over.
    """

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        # Fonts
        self.idx_font = QFont("AmsiPro", 11, QFont.Black)
        self.title_font = QFont("AmsiPro", 11, QFont.Black)
        self.artist_font = QFont("AmsiPro", 8, QFont.DemiBold)
        self.time_font = QFont("Barlow", 11, QFont.DemiBold)

        # Colors (fall back if theme values are strings)
        def _qcolor(val, fallback):
            if isinstance(val, QColor):
                return val
            try:
                return QColor(val)
            except Exception:
                return QColor(fallback)

        self.color_secondary = _qcolor(getattr(self.theme, "secondary", "#b6acb3"), "#b6acb3")
        self.color_primary = _qcolor(getattr(self.theme, "primary", "#ffffff"), "#ffffff")
        self.hover_color = _qcolor(getattr(self.theme, "system2", "rgba(80,160,255,40)"), QColor(80, 160, 255, 40))

    def paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()

        # Draw hover background for full row if requested by the view/delegate option
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, self.hover_color)

        col = index.column()
        rect = option.rect.adjusted(6, 0, -6, 0)  # left/right padding

        if col == 0:
            # index
            painter.setFont(self.idx_font)
            painter.setPen(self.color_secondary)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, str(index.data(Qt.DisplayRole)))
        elif col == 1:
            # Title above artist
            title, artist = index.data(Qt.DisplayRole) or ("", "")
            # Title
            painter.setFont(self.title_font)
            painter.setPen(self.color_primary)
            title_rect = QRect(rect.left(), rect.top(), rect.width(), rect.height() // 2)
            elided_title = painter.fontMetrics().elidedText(str(title), Qt.ElideRight, title_rect.width())
            painter.drawText(title_rect, Qt.AlignVCenter | Qt.AlignLeft, elided_title)
            # Artist (smaller, muted)
            painter.setFont(self.artist_font)
            painter.setPen(self.color_secondary)
            artist_rect = QRect(rect.left(), rect.top() + rect.height() // 2, rect.width(), rect.height() // 2)
            elided_artist = painter.fontMetrics().elidedText(str(artist), Qt.ElideRight, artist_rect.width())
            painter.drawText(artist_rect, Qt.AlignVCenter | Qt.AlignLeft, elided_artist)
        elif col == 2:
            painter.setFont(self.idx_font)
            painter.setPen(self.color_secondary)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, str(index.data(Qt.DisplayRole)))
        elif col == 3:
            painter.setFont(self.time_font)
            painter.setPen(self.color_secondary)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, str(index.data(Qt.DisplayRole)))
        else:
            super().paint(painter, option, index)

        painter.restore()

    def sizeHint(self, option, index):
        # Height should be large enough for two lines in the title/artist column
        # We'll use title font metrics + artist font metrics + spacing + margins
        fm_title = option.fontMetrics
        # approximate: title line + artist line + spacing
        height = fm_title.height() * 2 + 6
        return QSize(200, height)
