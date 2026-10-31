"""Read persisted factory type tags across package relocations."""

from __future__ import annotations

from typing import Any

from loop import Ledger as LoopLedger
from loop import WorkItemLog

_OLD_GITHUB = "loop.shared.github."
_OLD_FACTORY = "workflows.software_factory."
_OLD_PACKAGES = frozenset({"agents", "events", "flows", "infrastructure", "stages"})
_PR_EVENTS = frozenset({"ci", "feedback", "jenkins", "pull_requests"})
_PR_STAGES = frozenset(
    {
        "address",
        "ci",
        "conflicts",
        "copilot",
        "feedback",
        "jenkins",
        "pacing",
        "pull_requests",
        "readiness",
    }
)
_CORE_STAGES = frozenset({"checks", "workspaces"})
_PR_ROLES = frozenset({"address", "maintainer"})


class Ledger(LoopLedger):
    """Reads old type tags without changing the append-only log on disk."""

    def item(self, workflow: str, item: str) -> WorkItemLog:
        """The work item's log with relocated type tags normalized on read."""
        return _RelocatedLog(super().item(workflow, item).path.parent)


class _RelocatedLog(WorkItemLog):
    def _read(self) -> list[dict[str, Any]]:
        return [_relocated(record) for record in super()._read()]


def _relocated(value: Any) -> Any:
    if isinstance(value, list):
        return [_relocated(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _relocated(item) for key, item in value.items()}
    tag = result.get("type")
    if isinstance(result.get("fields"), dict) and isinstance(tag, str):
        result["type"] = _relocated_type(tag)
    return result


def _relocated_type(tag: str) -> str:
    """Map persisted factory type names to capability modules, without rewriting logs."""
    if tag.startswith(_OLD_GITHUB):
        old = "infrastructure.github." + tag.removeprefix(_OLD_GITHUB)
    elif tag.startswith(_OLD_FACTORY):
        old = tag.removeprefix(_OLD_FACTORY)
    elif tag.startswith("software_factory.shared."):
        old = tag.removeprefix("software_factory.shared.")
    elif tag.startswith("software_factory."):
        old = tag.removeprefix("software_factory.")
        if old.partition(".")[0] not in _OLD_PACKAGES:
            return tag
    else:
        return tag

    if old.startswith("shared."):
        old = old.removeprefix("shared.")
    package, _, rest = old.partition(".")
    if package == "workflows":
        return "software_factory." + old
    if package not in _OLD_PACKAGES or not rest:
        return tag

    module = rest.partition(".")[0]
    if package == "events" and rest.startswith("specs.RepositoryTarget"):
        return "software_factory.core.events.targets." + rest.removeprefix("specs.")
    if package == "events":
        domain = (
            "core"
            if module == "checks"
            else ("pull_requests" if module in _PR_EVENTS else "spec_delivery")
        )
    elif package == "stages":
        domain = (
            "core"
            if module in _CORE_STAGES
            else ("pull_requests" if module in _PR_STAGES else "spec_delivery")
        )
        if rest.startswith("workspaces.FeatureWorktreeInput"):
            domain = "spec_delivery"
    elif package == "flows":
        domain = "pull_requests"
    elif package == "agents":
        role = rest.split(".")[1] if rest.startswith("roles.") and "." in rest else ""
        domain = (
            ("pull_requests" if role in _PR_ROLES else "spec_delivery")
            if role
            else "core"
        )
    elif rest.startswith("github.issues.issue."):
        return "software_factory.core.infrastructure.github.issue." + rest.removeprefix(
            "github.issues.issue."
        )
    elif rest.startswith("github.issues."):
        domain = "spec_delivery"
    elif rest.startswith("github.pull_requests.") or rest.startswith("jenkins."):
        domain = "pull_requests"
    else:
        domain = "core"
    return f"software_factory.{domain}.{package}.{rest}"
