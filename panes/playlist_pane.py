# panes/playlist_pane.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem
from PySide6.QtCore import Qt

class PlaylistPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("")  # can be used for qss
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        playlist = QListWidget()
        playlist.setStyleSheet("""
                QListWidget {
                    background: transparent;
                    border: none;
                }
                QListWidget::item {
                    background: transparent; 
                }
                /* disable default selection highlight if selection mode is not NoSelection */
                QListWidget::item:selected {
                    background: transparent;
                }
                QListWidget::item:focus {
                    background: transparent;
                }
            """)
        for i in range(1, 21):
            item = QListWidgetItem(playlist)
            widget = TrackEntry("theme will go here", f"Track {i:02d}", "Sample Artist")

            item.setSizeHint(widget.sizeHint())
            playlist.addItem(item)
            playlist.setItemWidget(item, widget)
        layout.addWidget(playlist)


class TrackEntry(QWidget):
    def __init__(self, theme, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)

        # Labels
        self.title_label = QLabel(title)
        self.subtitle_label = QLabel(subtitle)

        self.title_label.setObjectName("title")
        self.subtitle_label.setObjectName("subtitle")

        # Layout
        text_layout = QVBoxLayout()
        # text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.subtitle_label)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.addLayout(text_layout)

        # Background + styling
        self.setObjectName("listEntry")
        self.setStyleSheet("""
                    QWidget#listEntry {
                        background-color: #2b2b2b;
                        border-radius: 6px;
                    }

                    QLabel#title {
                        color: white;
                        font-weight: 600;
                        font-size: 14px;
                    }

                    QLabel#subtitle {
                        color: #aaaaaa;
                        font-size: 11px;
                    }
                """)

        # Fixed height is important for list widgets
        self.setMinimumHeight(56)