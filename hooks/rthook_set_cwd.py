import os
import sys

# When frozen by PyInstaller, relative paths like "qml/main.qml" and
# "assets/icon.png" must resolve inside the unpacked bundle, not the
# directory the user launched the .exe from.
if hasattr(sys, "_MEIPASS"):
    os.chdir(sys._MEIPASS)
