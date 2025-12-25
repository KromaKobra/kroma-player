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
        self.playlist.setRowCount(20)
        self.playlist.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.playlist.horizontalHeader().setVisible(False)  # column names
        self.playlist.verticalHeader().setVisible(False)  # row numbers

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

    def _adjust_column_widths(self):
        """Use self._col_percents to set column widths."""
        col_count = self.playlist.columnCount()
        # ISSUE MAY BE FOUND HERE !!!
        table_w = self.width()                                  # Width of widget
        widths = [int(self._col_percents[i] * table_w) for i in range(col_count)]   # Percentages to PXs
        widths[col_count - 1] += int(table_w - sum(widths))     # Add rounded px remainder to last column
        for col in range(col_count):                            # Sequentially size each column
            self.playlist.setColumnWidth(col, widths[col])


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
