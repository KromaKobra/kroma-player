"""Where a list model's selection lives.

Most lists own their selection outright. The queue and history do not: the
``QueueManager`` has to drop a row's selection as it moves between the two
lists, so it is the owner and the models read through to it. Rather than
special-casing that in the model, selection is a small pluggable store.

Row identity here is always the ``uid`` — the per-row instance id — never the
track id. The same track can sit in a queue twice, and selecting one copy must
not select the other.
"""
from __future__ import annotations

from typing import Iterable, Protocol


class SelectionStore(Protocol):
    """The selection operations a track list model needs."""

    def is_selected(self, uid: int) -> bool: ...

    def set_selected(self, uid: int, selected: bool) -> None: ...

    def set_exclusive(self, uid: int) -> None: ...

    def set_multiple(self, uids: Iterable[int]) -> None: ...

    def clear(self) -> None: ...

    def selected(self) -> set[int]: ...


class InMemorySelection:
    """The default: the model owns its own selection."""

    def __init__(self) -> None:
        self._selected: set[int] = set()

    def is_selected(self, uid: int) -> bool:
        return uid in self._selected

    def set_selected(self, uid: int, selected: bool) -> None:
        if selected:
            self._selected.add(int(uid))
        else:
            self._selected.discard(int(uid))

    def set_exclusive(self, uid: int) -> None:
        self._selected = {int(uid)}

    def set_multiple(self, uids: Iterable[int]) -> None:
        self._selected = {int(u) for u in uids}

    def clear(self) -> None:
        self._selected.clear()

    def selected(self) -> set[int]:
        return set(self._selected)

    def retain(self, uids: Iterable[int]) -> None:
        """Drop anything not in *uids*. Used when a list's contents change."""
        self._selected &= {int(u) for u in uids}


class QueueManagerSelection:
    """Reads and writes the queue or history selection held by QueueManager.

    The manager owns it because navigation moves rows between the two lists and
    has to discard their selection as they go — something the models cannot see.
    """

    def __init__(self, manager, history: bool = False) -> None:
        self._manager = manager
        self._history = history

    def is_selected(self, uid: int) -> bool:
        return self._manager.is_selected(uid, history=self._history)

    def set_selected(self, uid: int, selected: bool) -> None:
        self._manager.set_selected(uid, selected, history=self._history)

    def set_exclusive(self, uid: int) -> None:
        self._manager.set_selected_exclusive(uid, history=self._history)

    def set_multiple(self, uids: Iterable[int]) -> None:
        self._manager.set_selected_multiple(set(uids), history=self._history)

    def clear(self) -> None:
        self._manager.clear_selection(history=self._history)

    def selected(self) -> set[int]:
        return set(self._manager.selected_ids(history=self._history))

    def retain(self, uids: Iterable[int]) -> None:
        keep = {int(u) for u in uids}
        self.set_multiple(self.selected() & keep)
