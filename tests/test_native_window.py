"""Frameless windows are still real windows as far as the shell is concerned.

The claim being tested is the one every symptom came from: a GUI that draws its
own title bar must not lose snapping, taskbar minimising, or a working
``showMaximized()``. Only the native handle can answer that, so this runs in a
subprocess with the real platform plugin rather than the offscreen one the rest
of the suite uses — ``QT_QPA_PLATFORM`` is fixed for the life of a process, and
an offscreen window has no HWND to ask.

The drag gestures themselves (snap to the top edge, drag a maximised window
back to floating) are deliberately not driven here: injecting real mouse input
would take over the machine running the tests. What is checked instead is
everything those gestures depend on — the style bits Windows keys its window
management off, and the maximise state actually reaching the shell.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="native window management is Windows-only"
)

# Style bits the shell requires: without WS_THICKFRAME and WS_MAXIMIZEBOX a
# window cannot be snapped, and without WS_MINIMIZEBOX its taskbar button
# cannot minimise it.
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_THICKFRAME = 0x00040000

FLOATING = (300, 300, 700, 450)  # x, y, width, height


def _probe() -> dict:
    """Run the probe below in a subprocess and return what it measured."""
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)  # a real window, so there is a real HWND
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(f"probe failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def probe() -> dict:
    return _probe()


def test_frameless_window_is_adopted(probe):
    assert probe["adopted"] is True


def test_shell_can_manage_the_window(probe):
    """The bits snapping and taskbar minimising are keyed off."""
    style = probe["style"]
    assert style & WS_THICKFRAME, "no sizing frame: the window cannot be snapped"
    assert style & WS_MAXIMIZEBOX, "no maximise box: dragging to the top edge does nothing"
    assert style & WS_MINIMIZEBOX, "no minimise box: the taskbar button cannot minimise it"


def test_no_frame_is_left_to_draw(probe):
    """The client area covers the window, so the GUI still draws every pixel."""
    assert probe["floating"]["client"] == probe["floating"]["window"]


def test_geometry_survives_the_change(probe):
    """Qt's geometry is what the GUI asked for, not that plus a frame."""
    x, y, w, h = FLOATING
    assert probe["floating"]["qt"] == [w, h, x, y]


def test_the_whole_window_belongs_to_the_gui(probe):
    """Nothing hit-tests as frame, or clicks near the edges would be swallowed."""
    assert probe["hit_tests"] == [1, 1, 1]


def test_maximising_reaches_the_shell(probe):
    """Qt imitates maximising for frameless windows; it must not here.

    This is what made a maximised window impossible to drag back to floating:
    Windows never knew it was maximised, so it had nothing to restore.
    """
    assert probe["maximized"]["zoomed"] is True
    assert probe["maximized"]["visibility"] == "Maximized"
    assert probe["maximized"]["client"][:2] == probe["work_area"]


def test_restoring_returns_to_the_floating_geometry(probe):
    x, y, w, h = FLOATING
    assert probe["restored"]["zoomed"] is False
    assert probe["restored"]["qt"] == [w, h, x, y]


# ---------------------------------------------------------------------------
# the subprocess side
# ---------------------------------------------------------------------------


def _run_probe() -> None:
    """Measure an adopted frameless window and print one JSON line."""
    import ctypes
    from ctypes import wintypes

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow

    from app.gui.native_window import enable_native_frameless

    user32 = ctypes.windll.user32
    x, y, w, h = FLOATING

    app = QGuiApplication(sys.argv[:1])
    window = QQuickWindow()
    window.setFlags(Qt.Window | Qt.FramelessWindowHint)
    window.setGeometry(x, y, w, h)
    window.show()

    report = {"adopted": enable_native_frameless(window)}
    hwnd = int(window.winId())

    def measure() -> dict:
        win, client = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(win))
        user32.GetClientRect(hwnd, ctypes.byref(client))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        return {
            "zoomed": bool(user32.IsZoomed(hwnd)),
            "visibility": window.visibility().name,
            "window": [win.right - win.left, win.bottom - win.top, win.left, win.top],
            "client": [client.right, client.bottom, origin.x, origin.y],
            "qt": [window.width(), window.height(), window.x(), window.y()],
        }

    def finish() -> None:
        report["style"] = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
        report["floating"] = measure()

        # Corner, the strip a caption would occupy, and the middle.
        win = report["floating"]["window"]
        points = [(win[2] + 2, win[3] + 2),
                  (win[2] + win[0] // 2, win[3] + 20),
                  (win[2] + win[0] // 2, win[3] + win[1] // 2)]
        report["hit_tests"] = [
            user32.SendMessageW(hwnd, 0x0084, 0, (py << 16) | (px & 0xFFFF))
            for px, py in points
        ]

        work = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)
        report["work_area"] = [work.right - work.left, work.bottom - work.top]

        window.showMaximized()
        QTimer.singleShot(600, second)

    def second() -> None:
        report["maximized"] = measure()
        window.showNormal()
        QTimer.singleShot(600, third)

    def third() -> None:
        report["restored"] = measure()
        print(json.dumps(report))
        app.quit()

    QTimer.singleShot(600, finish)
    app.exec()


if __name__ == "__main__":
    if "--probe" in sys.argv:
        _run_probe()
