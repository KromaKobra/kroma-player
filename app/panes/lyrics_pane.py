# panes/lyrics_pane.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit

class LyricsPane(QWidget):
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Lyrics")
        title.setProperty("class", "subtle")
        layout.addWidget(title)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText("Sample lyrics line 1\nSample lyrics line 2\n...")
        layout.addWidget(te)
