"""Native window management for GUI packages that draw their own title bar.

A GUI declares "I draw my own chrome" by asking for ``Qt.FramelessWindowHint``.
On Windows that hint costs far more than the frame. Qt maps it onto a bare
``WS_POPUP`` — no ``WS_THICKFRAME``, no ``WS_MINIMIZEBOX``, no
``WS_MAXIMIZEBOX`` — and Windows keys its window management off exactly those
bits, so the window quietly loses:

- snapping: dragging it to the top edge does not maximise it, and dragging a
  maximised window does not restore it to floating,
- minimising by clicking its taskbar button,
- Win+Arrow arrangement, and the shell's minimise/restore animations.

Qt then makes it worse. For a frameless window ``showMaximized()`` does not
maximise anything: it resizes the window to fill the screen and calls that
maximised. Windows still believes the window is a normal one that happens to be
large, so there is no state to restore from and no restore rectangle to restore
to — which is why a "maximised" window dragged by its title bar simply moves,
oversized, instead of shrinking back.

Both problems have one cause: the window is not a real window as far as the
shell is concerned. So rather than trying to reimplement the shell's behaviour
on top of a popup, this module makes the window ordinary again and removes the
frame one level lower down, where Qt cannot see it:

1. ``Qt.FramelessWindowHint`` is taken off the window, so Qt creates a normal
   overlapped window and drives maximise, restore and minimise natively.
2. ``WM_NCCALCSIZE`` is answered with a client area that covers the whole
   window, so the frame Qt just asked for is never given any space to draw in.
   Qt tracks the resulting zero frame margins by itself, so its geometry stays
   exact.
3. ``WM_NCHITTEST`` always answers ``HTCLIENT``. Without it the outermost
   pixels would still hit-test as resize borders and the strip below them as a
   caption, swallowing clicks meant for the GUI's own title bar and controls.
   Resizing therefore stays the GUI's business, through ``startSystemResize``,
   which keeps working.

While maximised the client area is set to the monitor's work area rather than
the window rectangle, which Windows deliberately oversizes by the frame width.
Taking the work area verbatim is simpler and more correct than subtracting
frame metrics: it follows the taskbar to whichever edge it is on, and needs no
DPI arithmetic of its own.

This belongs to the backend rather than to a GUI package because QML cannot
reach the native handle, and because every frameless GUI — including ones
nobody has written yet — needs the same repair. On X11 and Wayland the window
manager already drives frameless windows through ``startSystemMove`` and
``startSystemResize``, which is what a GUI calls anyway, so everywhere other
than Windows this module does nothing.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QGuiApplication, QWindow

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# -- Win32 --------------------------------------------------------------------

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084

HTCLIENT = 1

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200

MONITOR_DEFAULTTONEAREST = 0x0002

ABM_GETSTATE = 0x0004
ABM_GETTASKBARPOS = 0x0005
ABS_AUTOHIDE = 0x0001
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3

# Qt tags the message it hands to a native event filter differently depending on
# which event dispatcher is running.
WINDOWS_MSG_TYPES = (b"windows_generic_MSG", b"windows_dispatcher_MSG")


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


# ---------------------------------------------------------------------------
# frame removal
# ---------------------------------------------------------------------------


def _taskbar_autohide_edge(hwnd: int) -> int | None:
    """Return the screen edge an auto-hiding taskbar sits on, or ``None``.

    A maximised window covering the work area exactly would leave an
    auto-hidden taskbar unable to come back: the shell only raises it when the
    pointer reaches a strip of screen that no window is covering.
    """
    shell32 = ctypes.windll.shell32
    state = APPBARDATA()
    state.cbSize = ctypes.sizeof(APPBARDATA)
    if not (shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(state)) & ABS_AUTOHIDE):
        return None

    position = APPBARDATA()
    position.cbSize = ctypes.sizeof(APPBARDATA)
    if not shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(position)):
        return None

    # Only the bar on this window's own monitor can be in the way.
    user32 = ctypes.windll.user32
    on_window = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    on_bar = user32.MonitorFromRect(ctypes.byref(position.rc), MONITOR_DEFAULTTONEAREST)
    return position.uEdge if on_window == on_bar else None


def _work_area(hwnd: int) -> wintypes.RECT | None:
    """Return the usable area of the monitor the window is on."""
    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info.rcWork


def _fill_client_area(hwnd: int, lparam: int) -> None:
    """Answer ``WM_NCCALCSIZE`` with a client area that leaves no frame.

    ``rgrc[0]`` arrives holding the proposed window rectangle and is written
    back as the client rectangle. Left alone it means "the client is the whole
    window", which is exactly what a floating window wants.
    """
    if not ctypes.windll.user32.IsZoomed(hwnd):
        return

    work = _work_area(hwnd)
    if work is None:
        return

    rect = NCCALCSIZE_PARAMS.from_address(lparam).rgrc[0]
    rect.left, rect.top = work.left, work.top
    rect.right, rect.bottom = work.right, work.bottom

    edge = _taskbar_autohide_edge(hwnd)
    if edge == ABE_LEFT:
        rect.left += 1
    elif edge == ABE_TOP:
        rect.top += 1
    elif edge == ABE_RIGHT:
        rect.right -= 1
    elif edge == ABE_BOTTOM:
        rect.bottom -= 1


class _FramelessFilter(QAbstractNativeEventFilter):
    """Answers the two frame messages for every window that has been adopted."""

    def __init__(self) -> None:
        super().__init__()
        self._hwnds: set[int] = set()

    def watch(self, hwnd: int) -> None:
        self._hwnds.add(hwnd)

    def nativeEventFilter(self, event_type, message):  # noqa: N802 — Qt naming
        if event_type not in WINDOWS_MSG_TYPES:
            return False, 0

        msg = wintypes.MSG.from_address(int(message))
        if msg.hWnd not in self._hwnds:
            return False, 0

        if msg.message == WM_NCCALCSIZE and msg.wParam:
            _fill_client_area(msg.hWnd, msg.lParam)
            return True, 0

        if msg.message == WM_NCHITTEST:
            # Every pixel belongs to the GUI; see the module docstring.
            return True, HTCLIENT

        return False, 0


_filter: _FramelessFilter | None = None


def _ensure_filter() -> _FramelessFilter:
    global _filter
    if _filter is None:
        _filter = _FramelessFilter()
        QGuiApplication.instance().installNativeEventFilter(_filter)
    return _filter


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def enable_native_frameless(window: QWindow) -> bool:
    """Give a frameless *window* real window management, keeping it frameless.

    Returns True when the window was adopted. Windows that asked for a frame
    keep the one the platform gave them, and every platform other than Windows
    is left alone.
    """
    if not IS_WINDOWS or not (window.flags() & Qt.FramelessWindowHint):
        return False

    try:
        user32 = ctypes.windll.user32

        # A GUI restoring a maximised session has already asked Qt for its
        # imitation of maximised. Drop back to floating first: that is the
        # geometry worth carrying across the change, and the maximise is redone
        # for real at the end.
        was_maximized = window.visibility() == QWindow.Visibility.Maximized
        if was_maximized:
            window.showNormal()
        geometry = window.geometry()

        # Watch the handle before the frame appears, so the filter strips it on
        # the very first WM_NCCALCSIZE and none of it is ever painted.
        watcher = _ensure_filter()
        watcher.watch(int(window.winId()))
        window.setFlags(window.flags() & ~Qt.FramelessWindowHint)

        # Windows keeps the same handle across a style change, but a platform
        # that recreated it would leave the filter watching a dead one.
        hwnd = int(window.winId())
        watcher.watch(hwnd)
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
        )

        # Qt sized the window while it still expected a frame, so its idea of
        # the geometry grew by the frame it is no longer getting.
        window.setGeometry(geometry)
        if was_maximized:
            window.showMaximized()
    except Exception:
        # A GUI with an unusual window is not worth refusing to start over; it
        # simply keeps the behaviour it had before this module existed.
        logger.exception("Could not enable native window management")
        return False

    logger.info("Native window management enabled for frameless window")
    return True


def enable_for_root_objects(root_objects) -> None:
    """Adopt every frameless top-level window a GUI package created."""
    for obj in root_objects:
        if isinstance(obj, QWindow):
            enable_native_frameless(obj)
