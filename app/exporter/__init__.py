"""Copying library audio back out to a folder the user owns.

The library stores every track as Opus at a path derived from its database id —
``mix/0001-0100/002A.opus`` — which is fine for a program and useless to a
person. Export is the other direction: readable filenames, a format the
destination device actually plays, and tags taken from the database rather than
from whatever happens to be inside the file.

The pieces:

``formats``  the two supported outputs and their shared vocabulary
``naming``   filename sanitising and collision handling (pure, heavily tested)
``encode``   the ffmpeg transcode to MP3
``tagging``  writing ID3 frames and converting the AVIF cover to JPEG
``export``   the engine that ties them together
``workers``  the Qt thread wrapper — the only module here that imports Qt
"""
