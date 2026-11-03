"""Recovery from ledgers written before the GitHub integration moved."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from loop import Event, Pass, Services, Stage, Workflow, encode, run
from loop.software_factory.infrastructure.ledger import Ledger

from software_factory.src.spec_delivery.infrastructure.github.issues.graph import (
    SpecGraphSnapshot,
)
from software_factory.tests.infrastructure.github.builders import (
    build_graph,
    build_ticket,
)
from tests.runtime.harness import WorkCtx
from tests.runtime.support import NoInput


class Crash(RuntimeError):
    """A process death after an effect's reply was persisted."""


@dataclass(frozen=True)
class SnapshotRead(Event):
    graph: SpecGraphSnapshot


@pytest.mark.parametrize(
    "historical_prefix",
    ["loop.shared.github.", "workflows.software_factory.infrastructure.github."],
)
def test_old_github_reply_resumes_without_repeating_the_effect(
    tmp_path: Path, historical_prefix: str
) -> None:
    """Given a legacy executed GitHub reply, when resumed, then its nested models decode and the effect is not repeated."""
    graph = build_graph(tickets=(build_ticket(2),))
    effects = []
    gated = []

    def effect(_):
        effects.append("read")
        return graph

    def gate(_, reply, __):
        gated.append(reply)
        if len(gated) == 1:
            raise Crash("before gate")
        return Pass(SnapshotRead(reply))

    stage = (
        Stage("read", NoInput, SnapshotRead)
        .effect(effect, reconcile=lambda _: None)
        .gate(gate)
    )
    workflow = Workflow("test").context(WorkCtx).stage(stage)
    ledger = Ledger(tmp_path)
    work = WorkCtx()
    with pytest.raises(Crash):
        run(workflow, work, services=Services(), ledger=ledger)
    path = ledger.item("test", work.id).path
    legacy = path.read_text().replace(
        "software_factory.src.spec_delivery.infrastructure.github.", historical_prefix
    )
    assert f"{historical_prefix}issues.graph.SpecGraphSnapshot" in legacy
    path.write_text(legacy)

    result = run(workflow, work, services=Services(), ledger=Ledger(tmp_path))

    assert result.status == "completed"
    assert result.epoch == 1
    assert effects == ["read"]
    assert [encode(reply) for reply in gated] == [encode(graph), encode(graph)]
    assert isinstance(gated[-1], SpecGraphSnapshot)
    assert gated[-1].tickets[0].number == 2
    assert path.read_text().startswith(legacy)
    history = ledger.item("test", work.id).history(SnapshotRead)
    assert encode(history) == encode((SnapshotRead(graph),))


def test_relocation_preserves_text_and_torn_tail_recovery(tmp_path: Path) -> None:
    """Given a legacy tag, user text and a torn tail, when read and appended, then only the tag changes in memory and the intact bytes remain."""
    log = Ledger(tmp_path).item("test", "item")
    text = "loop.shared.github.issues.issue.Issue"
    log.seed([{"kind": "note", "text": text, "reply": {"type": text, "fields": {}}}])
    original = log.path.read_bytes()
    with log.path.open("ab") as stream:
        stream.write(b'{"kind":')

    restored = Ledger(tmp_path).item("test", "item")
    record = restored.records()[0]
    assert record["text"] == text
    assert (
        record["reply"]["type"]
        == "loop.software_factory.infrastructure.github.issue.Issue"
    )
    assert record["reply"]["type"] != text
    assert log.path.read_bytes() == original + b'{"kind":'
    with restored.lock():
        restored.append({"kind": "note", "text": "new"})

    assert log.path.read_bytes().startswith(original)
    assert len(Ledger(tmp_path).item("test", "item").records()) == 2


@pytest.mark.parametrize(
    ("historical", "expected"),
    [
        (
            "software_factory.core.events.targets.RepositoryTarget",
            "loop.software_factory.events.targets.RepositoryTarget",
        ),
        (
            "software_factory.core.events.checks.ChecksPassed",
            "loop.software_factory.events.checks.ChecksPassed",
        ),
        (
            "software_factory.core.stages.checks.ChecksInput",
            "software_factory.src.pull_requests.stages.checks.ChecksInput",
        ),
        (
            "workflows.software_factory.stages.workspaces.PullRequestWorktreeInput",
            "software_factory.src.pull_requests.stages.workspaces.PullRequestWorktreeInput",
        ),
        (
            "software_factory.core.agents._kit.status.StatusEnvelope",
            "loop.software_factory.agents.status.StatusEnvelope",
        ),
        (
            "workflows.software_factory.shared.events.feedback.FeedbackBatch",
            "software_factory.src.pull_requests.events.feedback.FeedbackBatch",
        ),
        (
            "workflows.software_factory.events.specs.RepositoryTarget",
            "loop.software_factory.events.targets.RepositoryTarget",
        ),
        (
            "workflows.software_factory.shared.events.tickets.TicketClosed",
            "software_factory.src.spec_delivery.events.tickets.TicketClosed",
        ),
        (
            "workflows.software_factory.shared.stages.pacing.Poll",
            "software_factory.src.pull_requests.stages.pacing.Poll",
        ),
        (
            "workflows.software_factory.shared.infrastructure.github.issues.issue.Issue",
            "loop.software_factory.infrastructure.github.issue.Issue",
        ),
    ],
)
def test_factory_type_tags_relocate_without_changing_user_text(
    tmp_path: Path, historical: str, expected: str
) -> None:
    """Only structured type tags migrate; persisted user text stays unchanged."""
    log = Ledger(tmp_path).item("test", "item")
    log.seed(
        [
            {
                "kind": "note",
                "text": historical,
                "item": {"type": historical, "fields": {}},
            }
        ]
    )

    record = Ledger(tmp_path).item("test", "item").records()[0]
    assert record["text"] == historical
    assert record["item"]["type"] == expected
    assert historical in log.path.read_text()


def test_pre_installation_core_events_replay_from_unchanged_ledger(
    tmp_path: Path,
) -> None:
    """Decode a persisted core event after its type moves into the installed package."""
    from loop.software_factory.events.checks import ChecksPassed

    log = Ledger(tmp_path).item("test", "item")
    log.seed(
        [
            {
                "kind": "attempt",
                "status": "passed",
                "event": {
                    "type": "software_factory.core.events.checks.ChecksPassed",
                    "fields": {},
                },
            }
        ]
    )
    original = log.path.read_bytes()

    recovered = Ledger(tmp_path).item("test", "item")
    assert recovered.history(ChecksPassed) == (ChecksPassed(),)
    assert log.path.read_bytes() == original
