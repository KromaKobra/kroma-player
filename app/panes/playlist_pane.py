# panes/playlist_pane.py
from math import floor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableView, QHeaderView, QStyledItemDelegate, QStyle
from PySide6.QtGui import QPainter, QFont, QColor
from PySide6.QtCore import Qt, QRect, QModelIndex, QSize, QTimer
from playlist.playlist_model import PlaylistModel
from playlist.playlist_view import PlaylistTable, PlaylistDelegate

# The playlist pane contains a TableView and an AbstractTableModel.
# The TableView is used for displaying the table.
# The AbstractTableModel is used for supplying the data that is to be displayed.


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
            ("1", "How Great Is Our God", "Chris Tomlin", "Arriving", "4:28"),
            ("2", "Oceans (Where Feet May Fail)", "Hillsong UNITED", "Zion", "8:56"),
            ("3", "10,000 Reasons (Bless the Lord)", "Matt Redman", "10,000 Reasons", "5:43"),
            ("4", "Good Good Father", "Chris Tomlin", "The Ultimate Playlist", "4:55"),
            ("5", "Reckless Love", "Cory Asbury", "Reckless Love", "5:33"),
            ("6", "What a Beautiful Name", "Hillsong Worship", "Let There Be Light", "5:38"),
            ("7", "Great Are You Lord", "All Sons & Daughters", "All Sons & Daughters", "5:02"),
            ("8", "Who You Say I Am", "Hillsong Worship", "There Is More", "3:19"),
            ("9", "Blessed Be Your Name", "Matt Redman", "Where Angels Fear to Tread", "4:11"),
            ("10", "Build My Life", "Pat Barrett", "Wholly Holy", "4:57"),
            ("11", "Cornerstone", "Hillsong Worship", "Cornerstone", "3:58"),
            ("12", "Here I Am to Worship", "Tim Hughes", "Here I Am to Worship", "4:30"),
            ("13", "This Is Amazing Grace", "Phil Wickham", "The Ascension", "4:59"),
            ("14", "Living Hope", "Phil Wickham", "Living Hope", "4:55"),
            ("15", "Mighty to Save", "Hillsong Worship", "Ultimate Worship", "5:27"),
            ("16", "In Christ Alone", "Keith & Kristyn Getty", "In Christ Alone", "4:49"),
            ("17", "Forever", "Kari Jobe", "Majestic", "5:45"),
            ("18", "Holy Spirit", "Jesus Culture", "Live from New York", "7:35"),
            ("19", "King of My Heart", "Bethel Music", "Have It All", "4:26"),
            ("20", "Way Maker", "Sinach", "Way Maker", "5:02"),
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
