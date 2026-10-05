from __future__ import annotations

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
