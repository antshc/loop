"""Provider-specific evidence and recovery outcomes for Jenkins PR jobs."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Event


@dataclass(frozen=True)
class JenkinsFailed(Event):
    """A failed Jenkins build and bounded evidence tied to the current PR head."""

    job: str
    number: int
    head: str
    result: str
    attempts: int
    logs: str
    jobs: tuple[str, ...]

    @property
    def signal(self) -> str:
        """Invalidate diagnosis when the Jenkins build changes."""
        return f"{self.head}:{self.job}:{self.number}:{self.result}"


@dataclass(frozen=True)
class OtherCi(Event):
    """No configured Jenkins failure was selected; use existing CI handling."""


@dataclass(frozen=True)
class JenkinsWaiting(Event):
    """Jenkins is queued, running, or ahead of its stale GitHub failure status."""


type JenkinsObservation = JenkinsFailed | OtherCi | JenkinsWaiting
type JenkinsReadiness = OtherCi | JenkinsWaiting


@dataclass(frozen=True)
class JenkinsRestarted(Event):
    """A single restart was queued, or skipped because the remote state changed."""

    head: str
    job: str
    build: int
    queue: str | None


@dataclass(frozen=True)
class JenkinsCapacity(Event):
    """The failed head still has recovery capacity and is current."""
