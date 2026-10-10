"""Backend-owned update preferences.

Four keys, and the reasoning for each is different.

``updates_check_on_launch``
    Whether to ask GitHub, once a day, whether there is a newer release.
    Defaults **on**, and that is the one genuinely contested decision in this
    feature — ADR 0012's objection to network staleness checks is softened by an
    explicit click and not softened at all by a check nobody asked for. The
    mitigations that make it defensible are the once-a-day gate below, silent
    degradation when there is no network, and the fact that nothing downloads
    without a click. ADR 0014 argues it properly.
``updates_last_checked``
    When the last automatic check happened, ISO-8601 in UTC. This is what
    implements the gate. Anonymous GitHub allows 60 requests an hour per IP, so
    without it a shared-IP office trips the limit and every user there gets a
    silent failure — the worst possible outcome, since it looks exactly like
    "there is no update".
``updates_skipped_version``
    A version the user said "skip" to. It silences the *automatic* announcement
    only: a manual check still offers it, because somebody who pressed "Check
    for updates" is asking, whatever they said last week. Deliberately a single
    string rather than a set: skipping 1.2.0 and later being offered 1.3.0 is
    the correct behaviour, so only the most recent refusal is worth remembering,
    and a set would only accumulate versions nobody will ever be offered again.
``updates_pending_version``
    The version an update was handed off to the installer for, written just
    before the app quits for it. The next launch reads it: still running an
    older build than that means the update did not take, and the user is told
    so and pointed at the installer's log rather than left wondering. Written
    through to disk at once, because the app is about to exit and the shutdown
    that would otherwise save it is the part an update most often cuts short.

Shaped like :mod:`app.settings.backup`. Constructed with
``None`` it serves the defaults, so a test or a script built without a session
still gets the documented behaviour.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.settings.store import SettingsStore

CHECK_ON_LAUNCH_KEY = "updates_check_on_launch"
LAST_CHECKED_KEY = "updates_last_checked"     # ISO-8601, UTC
SKIPPED_VERSION_KEY = "updates_skipped_version"
PENDING_VERSION_KEY = "updates_pending_version"

DEFAULT_CHECK_ON_LAUNCH = True
DEFAULT_LAST_CHECKED = ""
DEFAULT_SKIPPED_VERSION = ""
DEFAULT_PENDING_VERSION = ""

# How long an automatic check waits after the last one. A release happens on the
# scale of weeks; a day is already far more often than anything changes, and it
# is what keeps a whole office inside GitHub's anonymous rate limit.
CHECK_INTERVAL = timedelta(hours=24)


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_text(value, default: str) -> str:
    return value if isinstance(value, str) else default


class UpdateSettings:
    """Typed accessors over the four update keys."""

    def __init__(self, store: Optional[SettingsStore]) -> None:
        self._store = store

    @property
    def check_on_launch(self) -> bool:
        """Whether to look for a newer release shortly after startup."""
        if self._store is None:
            return DEFAULT_CHECK_ON_LAUNCH
        return _as_bool(
            self._store.app_get(CHECK_ON_LAUNCH_KEY, DEFAULT_CHECK_ON_LAUNCH),
            DEFAULT_CHECK_ON_LAUNCH,
        )

    @check_on_launch.setter
    def check_on_launch(self, value: bool) -> None:
        if self._store is not None:
            self._store.app_set(
                CHECK_ON_LAUNCH_KEY, _as_bool(value, DEFAULT_CHECK_ON_LAUNCH)
            )

    @property
    def last_checked(self) -> str:
        """When the last check ran, ISO-8601 UTC, or ``""`` if never."""
        if self._store is None:
            return DEFAULT_LAST_CHECKED
        return _as_text(
            self._store.app_get(LAST_CHECKED_KEY, DEFAULT_LAST_CHECKED),
            DEFAULT_LAST_CHECKED,
        )

    @last_checked.setter
    def last_checked(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(LAST_CHECKED_KEY, _as_text(value, DEFAULT_LAST_CHECKED))

    def stamp_checked(self, when: datetime | None = None) -> None:
        """Record that a check just happened.

        UTC, always. A local timestamp would make the gate behave differently
        either side of a timezone change or a daylight-saving boundary, for no
        benefit — nobody reads this value as a wall clock; the view model
        formats :attr:`last_checked` for display separately.
        """
        moment = when or datetime.now(timezone.utc)
        self.last_checked = moment.astimezone(timezone.utc).isoformat()

    def due_for_check(self, now: datetime | None = None) -> bool:
        """Whether an *automatic* check is allowed to run.

        A manual click never asks this — somebody who pressed the button is owed
        an answer, and one request is not what trips a rate limit.

        An unreadable or missing stamp reads as "due". Failing open is right
        here: the cost of one extra request is nothing, and failing closed would
        mean a single corrupt value silently disabling update checks forever.
        """
        if not self.check_on_launch:
            return False

        stamp = self.last_checked
        if not stamp:
            return True
        try:
            last = datetime.fromisoformat(stamp)
        except ValueError:
            return True
        if last.tzinfo is None:
            # A value written by something that did not say. Reading it as UTC
            # matches what this module writes and is at worst a few hours out.
            last = last.replace(tzinfo=timezone.utc)

        return (now or datetime.now(timezone.utc)) - last >= CHECK_INTERVAL

    @property
    def skipped_version(self) -> str:
        """The version the user last chose to skip, or ``""``."""
        if self._store is None:
            return DEFAULT_SKIPPED_VERSION
        return _as_text(
            self._store.app_get(SKIPPED_VERSION_KEY, DEFAULT_SKIPPED_VERSION),
            DEFAULT_SKIPPED_VERSION,
        )

    @skipped_version.setter
    def skipped_version(self, value: str) -> None:
        if self._store is not None:
            self._store.app_set(
                SKIPPED_VERSION_KEY, _as_text(value, DEFAULT_SKIPPED_VERSION)
            )

    @property
    def pending_version(self) -> str:
        """The version last handed to the installer, or ``""``."""
        if self._store is None:
            return DEFAULT_PENDING_VERSION
        return _as_text(
            self._store.app_get(PENDING_VERSION_KEY, DEFAULT_PENDING_VERSION),
            DEFAULT_PENDING_VERSION,
        )

    @pending_version.setter
    def pending_version(self, value: str) -> None:
        if self._store is None:
            return
        self._store.app_set(PENDING_VERSION_KEY, _as_text(value, DEFAULT_PENDING_VERSION))
        # Now rather than at shutdown — see the module docstring.
        flush = getattr(self._store, "flush", None)
        if callable(flush):
            flush()
