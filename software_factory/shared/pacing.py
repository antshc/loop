"""The waiting between polls, behind a seam so tests never sleep."""

from __future__ import annotations

import time
from collections.abc import Callable


class Pacer:
    """Waits `seconds` through the injected `sleep`."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    def wait(self, seconds: float) -> None:
        """Blocks for `seconds`."""
        self._sleep(seconds)
