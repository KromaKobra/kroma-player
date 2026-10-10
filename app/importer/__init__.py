"""Local media import subsystem.

Turns user-supplied audio files/folders — and wanted tracks an audio source can
fetch — into library tracks, through fixed steps whose providers may be absent:

1. **Probe** — scan + read each local file's tags, embedded art and any external
   ids it carries (``media_probe``). Always core.
2. **Identify** — ask the identity provider which recording each file is
   (``import_workers.ImportSearchWorker``). Skipped with no provider: the row is
   the file's own tags.
3. **Audio** — for a wanted track only, fetch it from an audio source
   (``fetch``). Absent with no source, and so is every way of asking for one.
4. **Finalize** — transcode to Opus, tag, write the row, move the file into the
   vault (``stash``). Host only, and the one step that writes.

Long-running steps run off the UI thread via the QObject workers in
``import_workers``.
"""
