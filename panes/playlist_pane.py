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

        playlist = QTableWidget()
        playlist.setColumnCount(5)
        # playlist.setHorizontalHeaderLabels(["#", "Title", "Artist", "Album", "Dur"])
        # playlist.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)  # Title expands
        # playlist.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        # playlist.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        playlist.setRowCount(4)
        playlist.horizontalHeader().setVisible(False)  # column names
        playlist.verticalHeader().setVisible(False)  # row numbers

        demo = [
            ("1", "Everything In Its Right Place", "Radiohead", "Kid A", "4:11"),
            ("2", "Paranoid Android", "Radiohead", "OK Computer", "6:27"),
            ("3", "Time Is Running Out", "Muse", "Absolution", "3:58"),
            ("4", "Numb", "Linkin Park", "Meteora", "3:07"),
        ]
        for r, row in enumerate(demo):
            for c, val in enumerate(row):
                item = IndexCol(theme, val)
                playlist.setCellWidget(r, c, item)

        playlist.setSelectionBehavior(QTableWidget.SelectRows)
        playlist.setShowGrid(False)
        playlist.setFocusPolicy(Qt.NoFocus)
        playlist.verticalHeader().setDefaultSectionSize(56)
        playlist.setStyleSheet("""
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
        layout.addWidget(playlist)

class IndexCol(QLabel):
    def __init__(self, theme, idx, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setText(idx)

        self.font = QFont()
        self.font.setFamily("Segoe UI")  # or any installed font
        self.font.setPointSize(11)  # text size
        self.font.setWeight(QFont.Medium)  # or QFont.Bold

        self.setFont(self.font)

        # playlist = QListWidget()
        # playlist.setStyleSheet("""
        #         QListWidget {
        #             background: transparent;
        #             border: none;
        #         }
        #         QListWidget::item {
        #             background: transparent;
        #         }
        #         /* disable default selection highlight if selection mode is not NoSelection */
        #         QListWidget::item:selected {
        #             background: transparent;
        #         }
        #         QListWidget::item:focus {
        #             background: transparent;
        #         }
        #     """)
        # for i in range(1, 21):
        #     item = QListWidgetItem(playlist)
        #     widget = TrackEntry("theme will go here", f"Track {i:02d}", "Sample Artist")
        #
        #     item.setSizeHint(widget.sizeHint())
        #     playlist.addItem(item)
        #     playlist.setItemWidget(item, widget)
        # layout.addWidget(playlist)


# class TrackEntry(QWidget):
#     def __init__(self, theme, title: str, subtitle: str = "", parent=None):
#         super().__init__(parent)
#
#         # Labels
#         self.title_label = QLabel(title)
#         self.subtitle_label = QLabel(subtitle)
#
#         self.title_label.setObjectName("title")
#         self.subtitle_label.setObjectName("subtitle")
#
#         # Layout
#         text_layout = QVBoxLayout()
#         # text_layout.setSpacing(2)
#         text_layout.setContentsMargins(0, 0, 0, 0)
#         text_layout.addWidget(self.title_label)
#         text_layout.addWidget(self.subtitle_label)
#
#         main_layout = QHBoxLayout(self)
#         main_layout.setContentsMargins(12, 8, 12, 8)
#         main_layout.addLayout(text_layout)
#
#         # Background + styling
#         self.setObjectName("listEntry")
#         self.setStyleSheet("""
#                     QWidget#listEntry {
#                         background-color: #2b2b2b;
#                         border-radius: 6px;
#                     }
#
#                     QLabel#title {
#                         color: white;
#                         font-weight: 600;
#                         font-size: 14px;
#                     }
#
#                     QLabel#subtitle {
#                         color: #aaaaaa;
#                         font-size: 11px;
#                     }
#                 """)
#
#         # Fixed height is important for list widgets
#         self.setMinimumHeight(56)