# panes/playlist_pane.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtGui import QFont, QColor, QPainter
from PySide6.QtCore import Qt, QRect, QEvent

class PlaylistPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("")  # can be used for qss
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._col_percents = [0.05, 0.45, 0.25, 0.25]

        self.playlist = PlaylistTable(theme)
        self.playlist.setColumnCount(4)
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
            self.playlist.setCellWidget(r, 0, IndexCol(theme, row[0]))
            self.playlist.setCellWidget(r, 1, NameCol(theme, row[1], row[2]))
            self.playlist.setCellWidget(r, 2, IndexCol(theme, row[3]))
            self.playlist.setCellWidget(r, 3, TimeCol(theme, row[4]))


        self.playlist.setSelectionBehavior(QTableWidget.SelectRows)
        # COMMENT THIS WHEN DEBUGGING COLUMNS
        self.playlist.setShowGrid(False)
        self.playlist.setFocusPolicy(Qt.NoFocus)
        self.playlist.verticalHeader().setDefaultSectionSize(56)
        self.playlist.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist.setStyleSheet(f"""
        QTableView {{
            background: transparent;
            border: none;
        }}
        QTableView::item {{
            border: none;
            padding: 6px;
        }}
        QTableView::item:selected:!active {{
            background: rgba(80, 160, 255, 40);
        }}
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
        table_w = self.width()                                  # Width of widget
        widths = [int(self._col_percents[i] * table_w) for i in range(col_count)]   # Percentages to PXs
        widths[col_count - 1] += int(table_w - sum(widths))     # Add rounded px remainder to last column
        for col in range(col_count):                            # Sequentially size each column
            self.playlist.setColumnWidth(col, widths[col])


class PlaylistTable(QTableWidget):
    def __init__(self, theme):
        super().__init__()
        self.setMouseTracking(True)
        self._hover_row = -1
        self._hover_color = getattr(theme, "system2")
        # Ensure selection still behaves normally:
        self.setSelectionBehavior(QTableWidget.SelectRows)

    def mouseMoveEvent(self, event):
        # Find the row under the mouse (viewport coordinates)
        row = self.rowAt(event.position().y()) if hasattr(event, "position") else self.rowAt(event.y())
        if row != self._hover_row:
            self._hover_row = row
            # repaint just the rows involved (cheap)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        # Clear hover when mouse leaves the table
        if self._hover_row != -1:
            self._hover_row = -1
            self.viewport().update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        # Paint the hover background across the full row before default painting
        # so the selection/contents draw on top.
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, False)

        if 0 <= self._hover_row < self.rowCount():
            # y position of the row relative to the viewport
            y = self.rowViewportPosition(self._hover_row)
            h = self.rowHeight(self._hover_row)
            if h > 0:
                rect = QRect(0, y, self.viewport().width(), h)
                painter.fillRect(rect, self._hover_color)

        painter.end()
        # Now call the default paint to draw cells, widgets, selection, etc
        super().paintEvent(event)


class IndexCol(QLabel):
    def __init__(self, theme, idx, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setText(idx)

        font = QFont()
        font.setFamily("AmsiPro")  # or any installed font
        font.setPointSize(11)  # text size
        font.setWeight(QFont.Black)  # or QFont.Bold
        color = getattr(self.theme, "secondary")
        self.setStyleSheet("QLabel { color : " + color + "; }")

        self.setFont(font)
        self.setIndent(20)


class NameCol(QWidget):
    def __init__(self, theme, title, artists, parent=None):
        super().__init__(parent)
        self.theme = theme

        title = QLabel(title)
        title.setStyleSheet("QLabel { color : #ffffff; }")
        titleFont = QFont()
        titleFont.setFamily("AmsiPro")
        titleFont.setPointSize(11)
        titleFont.setWeight(QFont.Black)
        title.setFont(titleFont)

        artists = QLabel(artists)
        artists.setStyleSheet("QLabel { color : #b6acb3; }")
        artistFont = QFont()
        artistFont.setFamily("AmsiPro")
        artistFont.setPointSize(8)
        artistFont.setWeight(QFont.DemiBold)
        artists.setFont(artistFont)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title)
        layout.addWidget(artists)

        self.setContentsMargins(5, 0, 0, 0)


class TimeCol(QLabel):
    def __init__(self, theme, length, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setText(length)

        font = QFont()
        font.setFamily("Barlow")  # or any installed font
        font.setPointSize(11)  # text size
        font.setWeight(QFont.DemiBold)  # or QFont.Bold
        self.setStyleSheet("QLabel { color : #b6acb3; }")

        self.setFont(font)
        self.setIndent(20)
