# panes/playlist_pane.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

class PlaylistPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("")  # can be used for qss
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._col_percents = [0.05, 0.45, 0.25, 0.20, 0.05]
        self._table_margin = 30  # pixels smaller than layout width

        self.playlist = QTableWidget()
        self.playlist.setColumnCount(5)
        # self.playlist.setHorizontalHeaderLabels(["#", "Title", "Artist", "Album", "Dur"])
        # self.playlist.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)  # Title expands
        # self.playlist.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        # self.playlist.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.playlist.setRowCount(4)
        self.playlist.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.playlist.horizontalHeader().setVisible(False)  # column names
        self.playlist.verticalHeader().setVisible(False)  # row numbers

        demo = [
            ("1", "Everything In Its Right Place", "Radiohead", "Kid A", "4:11"),
            ("2", "Paranoid Android", "Radiohead", "OK Computer", "6:27"),
            ("3", "Time Is Running Out", "Muse", "Absolution", "3:58"),
            ("4", "Numb", "Linkin Park", "Meteora", "3:07"),
        ]
        for r, row in enumerate(demo):
            for c, val in enumerate(row):
                item = IndexCol(theme, val)
                self.playlist.setCellWidget(r, c, item)

        self.playlist.setSelectionBehavior(QTableWidget.SelectRows)
        # self.playlist.setShowGrid(False)
        self.playlist.setFocusPolicy(Qt.NoFocus)
        self.playlist.verticalHeader().setDefaultSectionSize(56)
        self.playlist.setStyleSheet("""
        QTableView {
            background: transparent;
            border: none;
        }
        QTableView::item {
            border: none;
            padding: 6px;
        }
        QTableView::item:selected:!active {
            background: rgba(80, 160, 255, 40);
        }
        """)
        layout.addWidget(self.playlist)
        self._adjust_column_widths()

    def resizeEvent(self, event):
        """Recompute column widths when the widget (and layout) resize."""
        super().resizeEvent(event)
        self._adjust_column_widths()
        self._update_ext_scroll_visibility_and_geometry()

    def _adjust_column_widths(self):
        """Set column widths so they sum to the table widget's width,
        using the percentages in self._col_percents."""
        col_count = self.playlist.columnCount()
        if col_count <= 0:
            return

        # If percent list length doesn't match, fall back to even distribution
        if len(self._col_percents) != col_count:
            per = 1.0 / col_count
            self._col_percents = [per] * col_count

        # Use the table widget's width (table is centered and slightly narrower than the whole pane)
        table_w = self.playlist.width()
        if table_w <= 0:
            return

        # compute integer widths and distribute rounding remainder
        widths = [int(self._col_percents[i] * table_w) for i in range(col_count)]
        used = sum(widths)
        remainder = table_w - used
        i = 0
        while remainder > 0:
            widths[i % col_count] += 1
            remainder -= 1
            i += 1

        for col in range(col_count):
            self.playlist.setColumnWidth(col, widths[col])

    def _update_ext_scroll_visibility_and_geometry(self):
        # resizeEvent can fire before __init__ completes
        if not hasattr(self, "ext_vscroll"):
            return

        internal_vscroll = self.playlist.verticalScrollBar()

        needs_scroll = internal_vscroll.maximum() > 0
        self.ext_vscroll.setVisible(needs_scroll)

        # Ensure external scrollbar height matches table's height and is aligned with it
        # Map the table geometry to the PlaylistPane coordinates
        table_geom = self.playlist.geometry()
        # Place scrollbar to the right of the table, matching its top and height
        scrollbar_width = self.ext_vscroll.sizeHint().width()
        x = table_geom.right() + 0  # directly to the right of the table
        y = table_geom.top()
        height = table_geom.height()

        # If placing scrollbar directly would run off the widget, clamp it inside contentsRect
        contents = self.contentsRect()
        max_x = contents.right() - scrollbar_width
        if x > max_x:
            x = max_x
        if y < contents.top():
            y = contents.top()
        if y + height > contents.bottom():
            height = max(10, contents.bottom() - y)

        self.ext_vscroll.setGeometry(x, y, scrollbar_width, height)


class IndexCol(QLabel):
    def __init__(self, theme, idx, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setText(idx)

        self.font = QFont()
        self.font.setFamily("AmsiPro")  # or any installed font
        self.font.setPointSize(11)  # text size
        self.font.setWeight(QFont.Black)  # or QFont.Bold
        self.color = getattr(self.theme, "secondary")
        self.setStyleSheet("QLabel { color : " + self.color + "; }")

        self.setFont(self.font)
        self.setIndent(20)
