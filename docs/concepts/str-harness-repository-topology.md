# Harness Repository Topology
**Type:** Architecture Pattern

## Purpose

The harness contains agent instructions, skills, Specs, and Tickets. The implement_spec workflow runs agents on isolated Git worktrees without modifying its checkout.

## Harness and target repositories

`software_factory/legacy_slices/implement_spec/` reads Specs and Tickets from the immutable `HARNESS: Repository` configured in `model/repositories.py`; target repositories come from `composition/repositories.py` or a `use_repositories(...)` provider passed to `main`.
Each target is an explicit `RepositoryWorkflow` pairing a `Repository` with an
agent delegate. `RepositoryId` is the shared GitHub repository identity in `software_factory/shared/github/`; `Repository` pairs it with the checkout and worktrees and stays in implement_spec. With no target configuration, the workflow defaults to
implementation on the harness repository.

```python
class RepositoryId(NamedTuple):
    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class Repository:
    checkout: Path
    worktrees: Path
    owner_name: RepositoryId


ROOT = Path(__file__).resolve().parents[3]  # software_factory/legacy_slices/implement_spec/model/repositories.py
HARNESS = Repository(
    checkout=ROOT,
    worktrees=ROOT / "workspace" / f"{ROOT.name}.worktrees",
    owner_name=RepositoryId("antshc", "loop"),
)
```

- MUST resolve each Spec's `repo:target:<owner/repo>` label against the configured repositories, case-insensitively.
- MUST mark missing, malformed, duplicated, or unconfigured targets `hitl` when they block actionable Tickets, without fetching Git or opening a worktree.
- MUST reject duplicate repository configurations rather than choose one delegate implicitly.
- MUST use the selected repository's `checkout` for Git fetch and repository operations; the agent edits only its worktree.
- MUST create feature-branch worktrees beneath the selected repository's `worktrees`, one directory per branch.
- MUST keep Spec/Ticket reads, updates, and execution-log identities on `HARNESS.owner_name`.
- MUST publish PRs to the selected target's GitHub repository; create its `PullRequestClient` only when Git has work to publish, and keep it out of Ticket orchestration.
- `PullRequestClient` exposes only `create_draft_pull_request`; finding an existing open PR is internal, and PR editing and review-thread operations are outside this ticket-delivery module.
- MUST NOT clone a missing repository or fall back to a different target when a Spec label does not match.

## Directory layout

```text
loop/                           # HARNESS.checkout
├── .github/                    # instructions and skills
├── docs/
├── workflows/
├── tests/
└── workspace/
    └── loop.worktrees/         # HARNESS.worktrees
        └── <feature-branch>/   # isolated checkout for a Spec
```

## Multi-repository layouts

The same ticket workflow can deliver code to independently cloned repositories
under the Workspace Folder, each with different agent steps:

```text
harness/
├── workflows/
└── workspace/
    ├── api/                    # api checkout
    ├── api.worktrees/
    │   └── <feature-branch>/
    ├── web/                    # web checkout
    └── web.worktrees/
        └── <feature-branch>/
```

Specs remain on the harness tracker and identify their target and base branch by
labels. The selected agent delegate receives only the current Ticket's context
and the open `AgentWorkspace`, not the Spec or tracker. Ticket iteration,
Initiative history, retry/rollback, and one-commit validation do not vary with
the delegate. There is no directory discovery or repository cloning.

See the [multi-repository example](../../README.md#different-agent-workflows-per-repository)
for configuration and an Implement/Review delegate.
