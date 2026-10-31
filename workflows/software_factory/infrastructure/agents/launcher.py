"""Fresh agent builders that all report to the Workflow's observer."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from loop import AgentBuilder, RunObserver, SessionLogReader


AgentFactory = Callable[[], AgentBuilder]


class AgentLauncher:
    """Builds one fresh agent per call, attached to the observer when there is one."""

    def __init__(
        self,
        new_agent: AgentFactory,
        observer: RunObserver | None = None,
        readers: Sequence[SessionLogReader] | None = None,
    ) -> None:
        self._new_agent = new_agent
        self._observer = observer
        self._readers = readers

    def __call__(self) -> AgentBuilder:
        builder = self._new_agent()
        if self._observer is None:
            return builder
        if self._readers is None:
            return builder.with_observer(self._observer)
        return builder.with_observer(self._observer, readers=self._readers)
