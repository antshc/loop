from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    state: str
    tags: tuple[str, ...]
    url: str


@dataclass(frozen=True)
class PullRequest:
    id: str
    title: str
    url: str
    branch: str


@dataclass(frozen=True)
class ReviewThread:
    id: str
    path: str
    body: str
    resolved: bool


class PlatformAdapter(ABC):
    @abstractmethod
    def list_specs(self) -> list[WorkItem]: ...

    @abstractmethod
    def list_pull_requests(self) -> list[PullRequest]: ...

    @abstractmethod
    def review_threads(self, pull_request_id: str) -> list[ReviewThread]: ...

    @abstractmethod
    def reply_to_thread(self, pull_request_id: str, thread_id: str, body: str) -> None: ...
