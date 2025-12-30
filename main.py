import sys
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QObject, Property, Slot, Signal

# Minimal Theme QObject exposing a few properties for QML
class Theme(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._app_margins = 20
        self._menubar_height = 34
        self._playlist_margins = 12
        self._card_radius = 12
        self._primary_color = "#121212"
        self._card_color = "#1f1f1f"
        self._accent_color = "#ff6b6b"
        self._text_color = "#e6e6e6"

    def getAppMargins(self):
        return self._app_margins

    def getMenubarHeight(self):
        return self._menubar_height

    def getPlaylistMargins(self):
        return self._playlist_margins

    def getCardRadius(self):
        return self._card_radius

    def getPrimaryColor(self):
        return self._primary_color

    def getCardColor(self):
        return self._card_color

    def getAccentColor(self):
        return self._accent_color

    def getTextColor(self):
        return self._text_color

    appMargins = Property(int, getAppMargins)
    menubarHeight = Property(int, getMenubarHeight)
    playlistMargins = Property(int, getPlaylistMargins)
    cardRadius = Property(int, getCardRadius)
    primaryColor = Property(str, getPrimaryColor)
    cardColor = Property(str, getCardColor)
    accentColor = Property(str, getAccentColor)
    textColor = Property(str, getTextColor)


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)

    engine = QQmlApplicationEngine()

    theme = Theme()
    # Expose the Theme instance to QML as "theme"
    engine.rootContext().setContextProperty("theme", theme)

    qml_file = "qml/Main.qml"
    engine.load(qml_file)
    if not engine.rootObjects():
        sys.exit(-1)
    sys.exit(app.exec())
