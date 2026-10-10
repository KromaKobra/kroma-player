"""Qt role numbers for the field catalog.

Kept apart from ``fields`` so the catalog itself — and everything below it,
including the repositories — stays free of Qt. This module is the adapter: it
is the only place that knows a field has a number as well as a name.

Role numbers are assigned by position in ``FIELDS`` and are **not** part of the
GUI API. A GUI reads roles by name, via ``roleNames()``; one that hardcoded
``Qt.UserRole + 15`` would break silently the day a field is inserted.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from app.library.fields import FIELDS, Field

_ROLE_BASE = int(Qt.ItemDataRole.UserRole) + 1

FIELD_TO_ROLE: dict[str, int] = {f.key: _ROLE_BASE + i for i, f in enumerate(FIELDS)}
ROLE_TO_FIELD: dict[int, Field] = {FIELD_TO_ROLE[f.key]: f for f in FIELDS}
ROLE_NAMES: dict[int, bytes] = {FIELD_TO_ROLE[f.key]: f.key.encode() for f in FIELDS}

# The roles Python itself needs to name.
ROLE_TRACK_ID = FIELD_TO_ROLE["trackId"]
ROLE_UID = FIELD_TO_ROLE["uid"]
ROLE_IS_SELECTED = FIELD_TO_ROLE["isSelected"]
# Computed rather than stored, so the model that serves it has to name it in
# `dataChanged` whenever the facts under it move — see ImportViewModel.
ROLE_IMPORT_STATE = FIELD_TO_ROLE["importState"]
