"""Cancellation + progress primitives shared by every stage.

Progress is reported as *real* work units (bytes read, rows processed) per stage; the
pipeline turns these into an overall percentage using fixed stage weights.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .errors import Cancelled


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Raise :class:`Cancelled` if cancellation was requested. Call this in hot loops."""
        if self._event.is_set():
            raise Cancelled()


# callback(done_units, total_units_or_None)
ProgressFn = Callable[[int, int | None], None]


def noop_progress(done: int, total: int | None) -> None:  # pragma: no cover - trivial
    return None
