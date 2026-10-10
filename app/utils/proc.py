"""Running child processes without a console window flashing in the user's face.

Every ffmpeg call in the app used to carry its own copy of the same
``subprocess.run`` wrapper, and none of them passed the Windows flags that keep
a console window from appearing. That was tolerable while ffmpeg only ran during
an import — one flash, buried under a progress bar — but export runs it once per
track, on demand, so the flashing became impossible to ignore.

The two halves live here together because they are only ever correct together:
:func:`no_window` is what makes a child process invisible, and :func:`run_ffmpeg`
is the only reason most callers spawn one.
"""
from __future__ import annotations

import subprocess
import sys

# The message is the same wherever ffmpeg turns out to be missing, and it needs
# to distinguish "you are running a release build and something is broken" from
# "you are running from source and skipped a setup step".
FFMPEG_MISSING = (
    "ffmpeg is not available — it ships inside release builds, so from "
    "source you need it installed and on PATH"
)


def no_window() -> dict:
    """Keyword arguments that keep a child process off the screen on Windows.

    Empty everywhere else, so callers can splat it unconditionally.
    """
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startupinfo,
            "creationflags": subprocess.CREATE_NO_WINDOW}


def run_ffmpeg(command: list[str]) -> None:
    """Run ffmpeg to completion, surfacing a readable error on failure.

    ffmpeg writes everything interesting to stderr and says nothing at all on
    success, so output is captured and only replayed as part of an exception.
    """
    try:
        subprocess.run(command, check=True, capture_output=True, text=True,
                       **no_window())
    except FileNotFoundError as exc:
        raise RuntimeError(FFMPEG_MISSING) from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"ffmpeg failed: {message}") from exc
