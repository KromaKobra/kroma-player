# panes/playlist_pane.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget
from PySide6.QtCore import Qt

class PlaylistPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("")  # can be used for qss
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Playlist")
        lbl.setProperty("class", "subtle")
        layout.addWidget(lbl)
        lst = QListWidget()
        for i in range(1, 21):
            lst.addItem(f"Track {i:02d} — Sample Artist")
        layout.addWidget(lst)
