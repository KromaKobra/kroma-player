"""What the cover providers keep in memory, and for how long.

Both image providers cache decoded ``QImage`` objects so that a row scrolling
back into view does not pay for its artwork twice. The thing worth pinning is
the *bound*, because getting it wrong does not look like a memory problem — it
looks like scroll lag.

A count-bounded cache only helps when the count exceeds the number of distinct
covers on screen over the course of a scroll. Below that it never converges:
every pass evicts the front of itself before reaching the back, so the same
covers are decoded again on every pass, forever. Measured on a 781-track
library with 667 distinct covers against the old 256-entry bound, roughly 60%
of rows missed on every sweep.

That cost is not confined to the worker thread it runs on. ``requestImage`` is
Python, so a decode holds the GIL — and the thread it is being held away from
is the one running the list model's ``data()`` and the QML bindings. Hence a
byte budget: it is the only bound that means the same thing for a 64 KB
thumbnail and a 1 MB now-playing cover.
"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.artwork.cover_provider import CoverImageProvider

# A 128px RGBA thumbnail, which is what a list row asks for.
THUMB_BYTES = THUMB_SIZE * THUMB_SIZE * 4


class CountingStore:
    """A CoverStore stand-in that records every lookup.

    Counting is the point: "the cache converged" is only observable as the
    store going quiet, and several of these tests have no other way to tell a
    cache hit from a decode that happened to be fast.
    """

    def __init__(self, hashes: set[str] | None = None) -> None:
        self._hashes = hashes if hashes is not None else set()
        self.lookups: list[tuple[str, int]] = []

    def get(self, cover_hash: str, size: int) -> bytes | None:
        self.lookups.append((cover_hash, size))
        return b"avif" if cover_hash in self._hashes else None


def _provider(monkeypatch, store, budget_bytes, decoded_edge=THUMB_SIZE):
    """A provider whose decode is stubbed to a known size.

    The AVIF decode itself is Pillow's and is not what these tests are about;
    what matters is how many bytes the result occupies once cached.
    """
    from app.artwork import cover_provider

    def fake_decode(_data: bytes) -> QImage:
        return QImage(decoded_edge, decoded_edge, QImage.Format.Format_RGBA8888)

    monkeypatch.setattr(cover_provider, "_decode_to_qimage", fake_decode)
    return CoverImageProvider(store, lru_bytes=budget_bytes)


def _request(provider, cover_hash, tier=THUMB_SIZE):
    return provider.requestImage(cover_hash, QSize(), QSize(tier, tier))


# ======================================================================
# The budget
# ======================================================================


def test_a_warm_cover_is_never_looked_up_twice(monkeypatch):
    store = CountingStore({"a"})
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 4)

    _request(provider, "a")
    store.lookups.clear()
    _request(provider, "a")

    assert store.lookups == [], "the second request was served from memory"


def test_the_cache_evicts_by_bytes_oldest_first(monkeypatch):
    store = CountingStore({"a", "b", "c", "d"})
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 3)

    for cover_hash in ("a", "b", "c", "d"):
        _request(provider, cover_hash)

    assert provider._bytes <= THUMB_BYTES * 3
    store.lookups.clear()

    _request(provider, "a")
    assert store.lookups, "the oldest entry went to make room for the fourth"

    store.lookups.clear()
    _request(provider, "d")
    assert store.lookups == [], "the newest entry stayed"


def test_reading_an_entry_keeps_it_alive(monkeypatch):
    """LRU, not FIFO — otherwise the row under the cursor is evicted on schedule."""
    store = CountingStore({"a", "b", "c"})
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 2)

    _request(provider, "a")
    _request(provider, "b")
    _request(provider, "a")   # 'a' is now the most recent, so 'b' is next out
    _request(provider, "c")

    store.lookups.clear()
    _request(provider, "a")
    assert store.lookups == [], "'a' survived because it had just been read"


def test_an_oversized_cover_does_not_evict_itself(monkeypatch):
    """A cache that cannot hold its own most recent answer is worse than none.

    Without the floor, a single image larger than the whole budget is inserted
    and immediately dropped, so every request for it decodes again — the exact
    thrashing the byte budget exists to end.
    """
    store = CountingStore({"big"})
    provider = _provider(
        monkeypatch, store, budget_bytes=1024, decoded_edge=LARGE_SIZE,
    )

    _request(provider, "big", tier=LARGE_SIZE)
    store.lookups.clear()
    _request(provider, "big", tier=LARGE_SIZE)

    assert store.lookups == [], "the one entry stayed despite exceeding the budget"


def test_tiers_are_cached_separately(monkeypatch):
    """A list row and the now-playing panel must not evict each other by turns."""
    store = CountingStore({"a"})
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 40)

    _request(provider, "a", tier=THUMB_SIZE)
    _request(provider, "a", tier=LARGE_SIZE)
    store.lookups.clear()

    _request(provider, "a", tier=THUMB_SIZE)
    assert store.lookups == [], "the thumbnail survived the large request"


# ======================================================================
# Covers that are not there
# ======================================================================


def test_a_missing_cover_is_only_looked_up_once(monkeypatch):
    """Failing to find something is worth remembering as much as finding it.

    A hash whose blob is absent walks all three store tiers before giving up.
    That is a stable fact about the library, so without caching the negative
    answer those three lookups run again every single time the row is painted
    into view — on every scroll, for the life of the process.
    """
    store = CountingStore(set())
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 4)

    _request(provider, "ghost")
    first_pass = len(store.lookups)
    assert first_pass > 0

    store.lookups.clear()
    _request(provider, "ghost")
    assert store.lookups == [], "the absence was remembered"


def test_a_missing_cover_still_returns_a_drawable_image(monkeypatch):
    """QML shows its own placeholder, so this must not be null or broken."""
    store = CountingStore(set())
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 4)

    img = _request(provider, "ghost")

    assert not img.isNull()
    assert img.size() == QSize(1, 1)


def test_the_reported_size_matches_the_image(monkeypatch):
    """Qt reads the out-parameter to lay the Image out before it has pixels."""
    store = CountingStore({"a"})
    provider = _provider(monkeypatch, store, budget_bytes=THUMB_BYTES * 4)

    size = QSize()
    provider.requestImage("a", size, QSize(THUMB_SIZE, THUMB_SIZE))

    assert size == QSize(THUMB_SIZE, THUMB_SIZE)
