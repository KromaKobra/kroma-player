# panes/playlist_pane.py
from math import floor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableView, QHeaderView, QStyledItemDelegate, QStyle
)
from PySide6.QtGui import QPainter, QFont, QColor
from PySide6.QtCore import (
    Qt, QRect, QAbstractTableModel, QModelIndex, QSize, QTimer
)


# -------------------------
# Model
# -------------------------
class PlaylistModel(QAbstractTableModel):
    """
    Model stores rows as tuples:
      (index_str, title, artist, album, time_str)
    The view uses 4 columns (index, title+artist, album, time).
    """

    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = list(rows or [])

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        # We expose 4 columns: index | name (title+artist) | album | time
        return 4

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = index.row()
        c = index.column()
        row = self._rows[r]
        # row format: (idx, title, artist, album, time)
        if role == Qt.DisplayRole:
            if c == 0:
                return row[0]
            if c == 1:
                # We'll return a tuple for custom painting (title, artist)
                return (row[1], row[2])
            if c == 2:
                return row[3]
            if c == 3:
                return row[4]
        if role == Qt.TextAlignmentRole:
            if c == 0 or c == 3:
                return Qt.AlignCenter
            return Qt.AlignVCenter | Qt.AlignLeft
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        # Hide headers in view, so simple behavior is fine
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            names = ["#", "Title/Artist", "Album", "Time"]
            return names[section] if section < len(names) else ""
        return super().headerData(section, orientation, role)

    # Convenience
    def add_rows(self, rows):
        start = self.rowCount()
        end = start + len(rows) - 1
        if start <= end:
            self.beginInsertRows(QModelIndex(), start, end)
            self._rows.extend(rows)
            self.endInsertRows()

    def remove_row(self, row):
        if 0 <= row < self.rowCount():
            self.beginRemoveRows(QModelIndex(), row, row)
            self._rows.pop(row)
            self.endRemoveRows()


# -------------------------
# Delegate - paints cells (handles hover visuals)
# -------------------------
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


# -------------------------
# View subclass (robust hover painting)
# -------------------------
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


# -------------------------
# PlaylistPane (main)
# -------------------------
class PlaylistPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # four columns: index | title+artist | album | time
        self._col_percents = [0.05, 0.45, 0.25, 0.25]

        # model + demo data (same demo you had)
        demo = [
            ("1", "Everything In Its Right Place", "Radiohead", "Kid A", "4:11"),
            ("2", "Paranoid Android", "Radiohead", "OK Computer", "6:27"),
            ("3", "Time Is Running Out", "Muse", "Absolution", "3:58"),
            ("4", "Numb", "Linkin Park", "Meteora", "3:07"),
            ("5", "Knights of Cydonia", "Muse", "Black Holes and Revelations", "6:07"),
            ("6", "In the End", "Linkin Park", "Hybrid Theory", "3:36"),
            ("7", "Idioteque", "Radiohead", "Kid A", "5:09"),
            ("8", "Plug In Baby", "Muse", "Origin of Symmetry", "3:39"),
            ("9", "Crawling", "Linkin Park", "Hybrid Theory", "3:29"),
            ("10", "Karma Police", "Radiohead", "OK Computer", "4:21"),
            ("11", "Stockholm Syndrome", "Muse", "Absolution", "4:58"),
            ("12", "Breaking the Habit", "Linkin Park", "Meteora", "3:16"),
            ("13", "How to Disappear Completely", "Radiohead", "Kid A", "5:56"),
            ("14", "Supermassive Black Hole", "Muse", "Black Holes and Revelations", "3:36"),
            ("15", "Faint", "Linkin Park", "Meteora", "2:42"),
            ("16", "No Surprises", "Radiohead", "OK Computer", "3:49"),
            ("17", "Map of the Problematique", "Muse", "Black Holes and Revelations", "4:18"),
            ("18", "Papercut", "Linkin Park", "Hybrid Theory", "3:04"),
            ("19", "Weird Fishes/Arpeggi", "Radiohead", "In Rainbows", "5:18"),
            ("20", "Hysteria", "Muse", "Absolution", "3:47"),
        ]

        # model/view/delegate
        self.model = PlaylistModel(demo)
        self.playlist = PlaylistTable(theme)
        self.playlist.setModel(self.model)
        self.delegate = PlaylistDelegate(theme, self.playlist)
        self.playlist.setItemDelegate(self.delegate)

        # view prefs (equivalent to your previous setup)
        self.playlist.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.playlist.horizontalHeader().setVisible(False)
        self.playlist.verticalHeader().setVisible(False)
        self.playlist.setShowGrid(False)
        self.playlist.setFocusPolicy(Qt.NoFocus)
        self.playlist.verticalHeader().setDefaultSectionSize(56)
        self.playlist.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist.setSelectionBehavior(QTableView.SelectRows)
        self.playlist.setMouseTracking(True)

        # Styling (same selectors you used)
        self.playlist.setStyleSheet(f"""
        QTableView {{
            background: transparent;
            border: none;
        }}
        QTableView::item {{
            border: none;
            padding: 6px;
            border-left: 0px solid transparent;
        }}
        QTableView::item:selected {{
            border-left: 0px solid transparent;
        }}
        QTableView::item:selected:!active {{
            background: rgba(80, 160, 255, 40);
        }}
        """)

        layout.addWidget(self.playlist)

        # initial column widths: schedule adjust (deferred) if viewport not ready
        self._adjust_deferred_scheduled = False
        self._adjust_column_widths()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_column_widths()

    def _normalized_percents(self, count):
        p = list(self._col_percents[:count])
        if len(p) < count:
            p += [1.0] * (count - len(p))
        s = sum(p)
        if s <= 0:
            return [1.0 / count] * count
        return [x / s for x in p]

    def _adjust_column_widths(self):
        """Set column widths based on percents and the playlist viewport width.
           If viewport isn't ready yet, defer one-shot to next event loop.
        """
        col_count = self.playlist.model().columnCount()
        avail = self.playlist.viewport().width()

        if avail <= 0:
            # schedule a single-shot retry after layouts settle
            if not getattr(self, "_adjust_deferred_scheduled", False):
                self._adjust_deferred_scheduled = True
                QTimer.singleShot(0, self._deferred_adjust_done)
            return

        perc = self._normalized_percents(col_count)
        # floor and distribute leftovers to get exact fit
        widths = [int(floor(avail * p)) for p in perc]
        leftover = int(avail - sum(widths))
        i = 0
        while leftover > 0:
            widths[i % col_count] += 1
            i += 1
            leftover -= 1

        for col, w in enumerate(widths):
            self.playlist.setColumnWidth(col, w)

    def _deferred_adjust_done(self):
        self._adjust_deferred_scheduled = False
        self._adjust_column_widths()
