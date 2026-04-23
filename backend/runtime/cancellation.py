"""Run-scoped cooperative cancellation primitives."""
from __future__ import annotations

import threading

from backend.runtime.errors import CancelledError


class CancellationController:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise CancelledError("user_cancelled")
