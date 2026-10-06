# Platform Adapter
**Type:** Architecture Pattern

## Purpose

Keep application and orchestration code independent of a specific repository/work-tracking platform such as GitHub or Azure DevOps.

Platform-specific CLIs, field names, states, and response shapes are translated at the infrastructure boundary into one normalized contract.

## Concept

A **Platform Adapter** exposes repository and work-management capabilities through a platform-neutral contract.

Application code depends only on the contract and normalized models such as `Ticket` and `PullRequest`. Each concrete adapter translates those operations to the native platform interface—for example, `gh` for GitHub or `az` for Azure DevOps—and maps native responses back to the normalized model.

Scope: this applies where Orb's own code calls a platform. Clients only a user's workflow calls, such as the shipped `GitHubClient`, are concrete helpers with no contract ([ADR 0003](../adr/0003-ship-orb-as-a-workflow-library-with-no-built-in-workflows.md)).

The concrete adapter is selected in the composition root, normally from explicit configuration or the repository remote. Platform detection and construction are outside application behavior.

```text
Application / Workflows
        |
        v
  PlatformAdapter
        ^
      /   \
     /     \
GitHub     Azure DevOps
Adapter      Adapter
   |           |
   v           v
 gh CLI       az CLI
```

The contract represents capabilities the application needs, not the command structure of any one provider.

## Rules

- MUST make application and workflow code depend on the platform-neutral adapter contract rather than a concrete platform implementation.
- MUST define the contract as an `abc.ABC` whose operations are `@abstractmethod`s, and MUST make each concrete adapter inherit it, so an adapter missing an operation fails at instantiation.
- MUST keep the contract in the application/runtime layer; adapters import it, never the reverse.
- MUST normalize platform-specific work-item and pull-request data before returning it through the contract.
- MUST translate platform-specific states, identifiers, labels or tags, assignees, and response shapes inside the concrete adapter.
- MUST keep native CLI invocation and parsing inside the concrete adapter.
- MUST provide one concrete adapter per supported platform.
- MUST select and construct the concrete adapter in the composition root or an equivalent infrastructure factory.
- MUST NOT expose `gh`, `az`, or provider-specific response models through the application-facing contract.
- MUST NOT branch on GitHub-versus-Azure-DevOps behavior inside application or workflow code when the difference can be handled by the adapter.
- SHOULD keep the contract limited to capabilities actually required by the application.
- SHOULD run a static type checker so overridden signatures are verified; `ABC` enforces only that the methods exist.
- SHOULD run one shared contract test suite against every adapter.
- SHOULD detect the platform from durable repository configuration such as the Git remote when no explicit platform configuration is supplied.

## Example

A normalized Python contract:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    id: int
    title: str
    state: str
    tags: tuple[str, ...]
    assigned_to: str | None
    url: str


class PlatformAdapter(ABC):
    @abstractmethod
    def list_tickets(
        self,
        *,
        tags: tuple[str, ...] = (),
        state: str | None = None,
        limit: int | None = None,
    ) -> list[Ticket]: ...

    @abstractmethod
    def add_comment(self, ticket_id: int, comment: str) -> None: ...

    @abstractmethod
    def create_branch(self, name: str, from_branch: str = "main") -> None: ...
```

GitHub translates the normalized operation to `gh`:

```python
import json
import subprocess


class GitHubAdapter(PlatformAdapter):
    def __init__(self, owner: str, repo: str) -> None:
        self._repo = f"{owner}/{repo}"

    def _gh(self, *args: str) -> str:
        return subprocess.run(
            ["gh", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def list_tickets(
        self,
        *,
        tags: tuple[str, ...] = (),
        state: str | None = None,
        limit: int | None = None,
    ) -> list[Ticket]:
        args = [
            "issue", "list",
            "--repo", self._repo,
            "--json", "number,title,state,labels,assignees,url",
        ]
        if state:
            args += ["--state", state]
        if limit:
            args += ["--limit", str(limit)]
        for tag in tags:
            args += ["--label", tag]

        issues = json.loads(self._gh(*args))
        return [
            Ticket(
                id=issue["number"],
                title=issue["title"],
                state=issue["state"].lower(),
                tags=tuple(label["name"] for label in issue["labels"]),
                assigned_to=(
                    issue["assignees"][0]["login"]
                    if issue["assignees"] else None
                ),
                url=issue["url"],
            )
            for issue in issues
        ]
```

Azure DevOps implements the same contract but translates it to `az boards` and maps fields such as `System.Id`, `System.Title`, `System.State`, and `System.Tags` into the same `Ticket` model.

Selection stays outside the application:

```python
def create_platform_adapter(remote_url: str) -> PlatformAdapter:
    if "dev.azure.com" in remote_url:
        org, project, repo = parse_azure_devops_remote(remote_url)
        return AzureDevOpsAdapter(org, project, repo)

    owner, repo = parse_github_remote(remote_url)
    return GitHubAdapter(owner, repo)
```

The caller remains platform-agnostic:

```python
items = platform.list_tickets(
    tags=("ready",),
    state="open",
)
```

## Benefits and Trade-offs

**Benefits**

- Keeps orchestration and use-case code independent of GitHub or Azure DevOps.
- Centralizes native CLI commands, parsing, and provider-specific mappings.
- Makes platform implementations replaceable without changing application behavior.
- Gives tests a small contract that can be replaced with a fake adapter.

**Trade-offs**

- The normalized contract can expose only the common or intentionally supported capability set.
- Adapters are coupled to the contract by inheritance; `ABC` checks that methods exist, not their signatures, so a type checker is still needed.
- Provider-specific features may require an explicit contract extension instead of leaking through the abstraction.
- State and field normalization adds mapping code that must be kept correct as provider behavior changes.

## Validation

A conforming implementation demonstrates that the same application code can operate through at least two concrete platform adapters without provider-specific branching, and that provider-native output is converted to normalized models before crossing the adapter boundary.

## References

- [Squad PlatformAdapter contract](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/types.ts)
- [Squad GitHubAdapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/github.ts)
- [Squad AzureDevOpsAdapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/azure-devops.ts)
- [Squad platform adapter factory](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/index.ts)
