from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..observe import RunObserver, WorktreeEvent
from .options import GitOptions
from .runtime import GitService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class ObservingGitService:
    """Decorates a GitService: reports readiness, release attempts, and actual removal or retention."""

    def __init__(
        self,
        inner: GitService,
        observer: RunObserver,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._inner = inner
        self._observer = observer
        self._clock = clock

    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]:
        return self._open(cwd, options)

    @contextmanager
    def _open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        path: Path | None = None
        body_ok = False
        removed_ok = False
        try:
            with self._inner.open(cwd, options) as path:
                self._emit("ready", path, None)
                try:
                    yield path
                    body_ok = True
                finally:
                    self._emit("removing", path, body_ok)
            # Reached only when the body and the inner removal both succeeded.
            removed_ok = body_ok
        finally:
            if path is not None:
                self._emit("retained" if path.exists() else "removed", path, removed_ok)

    def _emit(self, point: str, path: Path, ok: bool | None) -> None:
        try:
            self._observer.worktree(WorktreeEvent(point, path, self._clock(), ok))
        except (
            Exception
        ) as exception:  # An observer is caller code; it must never fail the run.
            logger.warning("observer failed on worktree %s: %r", point, exception)
