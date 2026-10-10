"""The vocal-removal sidecar script.

``separate.py`` is never imported by Kroma — it is handed to a *different*
Python interpreter as a file path. This package exists so the directory is
carried along by tooling that walks packages; see ``kroma-player.spec``, which
also has to ship the script as a data file, because a frozen build has no
source tree to point the child interpreter at.
"""
