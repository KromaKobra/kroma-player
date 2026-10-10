"""``image://plugin/<id>/<file>`` — pictures a plugin ships, for QML.

A plugin draws no QML of its own at this API level, but it does ship icons: the
glyph beside an id of its kind, its import source's menu icon, its own logo in
Settings → Plugins. This serves them straight out of the plugin's folder.

Only from inside that folder. The path after the id is resolved and checked to
be under the plugin's directory, so ``image://plugin/x/../../.ssh/id_rsa`` is a
blank pixel rather than a file read. Nothing is served for a plugin the host
does not know.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

logger = logging.getLogger(__name__)

SCHEME = "plugin"


def icon_url(plugin_id: str, relative: str) -> str:
    """The ``image://`` URL QML loads one of a plugin's pictures through."""
    if not plugin_id or not relative:
        return ""
    return f"image://{SCHEME}/{plugin_id}/{relative}"


def resolve(host: Any, ident: str) -> Optional[Path]:
    """The file *ident* (``<id>/<path>``) names, if it is inside that plugin."""
    plugin_id, _, relative = str(ident or "").partition("/")
    record = host.record(plugin_id) if host is not None else None
    if record is None or record.path is None or not relative:
        return None
    root = Path(record.path).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


class PluginIconProvider(QQuickImageProvider):
    """Serves pictures out of plugin folders, and nowhere else."""

    def __init__(self, host: Any) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._host = host

    def requestImage(self, id: str, size, requestedSize):  # noqa: A002 (Qt signature)
        path = resolve(self._host, id)
        image = QImage(str(path)) if path is not None else QImage()
        if image.isNull():
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(0)
        elif requestedSize is not None and requestedSize.isValid() and not requestedSize.isEmpty():
            image = image.scaled(requestedSize)
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image
