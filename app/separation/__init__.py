"""Local AI vocal removal, run in a separate interpreter.

Kroma cannot do this in-process. PyTorch publishes no wheels for the Python
this app runs on, and even where it does, the right build depends on the user's
accelerator — a CUDA wheel, a ROCm wheel and a CPU wheel are three different
downloads of very different sizes. Pinning one would be wrong for most people.

So separation is delegated to an interpreter the user owns and configures, the
same arrangement Kroma already has with ffmpeg and deno: not a Python package,
resolved at call time, and absent simply means this one feature is unavailable
rather than the app being broken.

* :mod:`app.separation.models` — what is installed in ``.data/models/``.
* :mod:`app.separation.runner` — spawns the sidecar and reads its progress.
* :mod:`app.separation.encode` — ffmpeg either side of it.
* :mod:`app.separation.sidecar` — the script that runs *over there*. It imports
  nothing from ``app``.
"""
