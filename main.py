import os
import sys
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

os.environ["QSG_RHI_BACKEND"] = "d3d11"   # Best on Windows | Apparently forces GPU rendering
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

app = QGuiApplication(sys.argv)

engine = QQmlApplicationEngine()
engine.load(QUrl.fromLocalFile("app/qml/Main.qml"))

sys.exit(app.exec())
