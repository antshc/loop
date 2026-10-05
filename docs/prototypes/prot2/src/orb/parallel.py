from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class Settled(Generic[R]):
    value: R | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def parallel_settled(
    items: Sequence[T],
    worker: Callable[[T], R],
    *,
    max_parallel: int,
) -> list[Settled[R]]:
    """Run `worker` over `items` with bounded concurrency; one failure never stops the others."""
    if max_parallel < 1:
        raise ValueError("max_parallel must be at least 1")
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = [pool.submit(worker, item) for item in items]
        outcomes: list[Settled[R]] = []
        for future in futures:
            error = future.exception()
            outcomes.append(Settled(error=error) if error else Settled(value=future.result()))
        return outcomes
