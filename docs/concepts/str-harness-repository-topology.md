# Harness Repository Topology
**Type:** Architecture Pattern

## Purpose

The harness contains agent instructions, skills, Specs, and Tickets. The implement_spec workflow runs agents on isolated Git worktrees without modifying its checkout.

## Harness and target repositories

`software_factory/workflows/implement_spec/` reads Specs and Tickets from the harness repository (`HARNESS_SLUG`, `harness_target()` in `__main__.py`); target repositories are `RepositoryTarget` values (slug, checkout, worktrees, and whether it plans, runs the chore and reviews) the entry point passes as `Targets`, which tests replace through `main(argv, services=...)`.
`RepositoryId` is the shared GitHub repository identity in `software_factory/shared/github/`. With no other target configuration, the workflow defaults to
implementation on the harness repository.

```python
class RepositoryId(NamedTuple):
    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class RepositoryTarget:
    slug: str
    checkout: Path
    worktrees: Path
    plan: bool = True
    chore: bool = True
    review: bool = True


ROOT = Path(__file__).resolve().parents[3]  # software_factory/workflows/implement_spec/__main__.py
HARNESS = RepositoryTarget(
    slug="antshc/loop",
    checkout=ROOT,
    worktrees=ROOT / "workspace" / f"{ROOT.name}.worktrees",
)
```

- MUST resolve each Spec's `repo:target:<owner/repo>` label against the configured repositories, case-insensitively.
- MUST mark missing, malformed, duplicated, or unconfigured targets `hitl` when they block actionable Tickets, without fetching Git or opening a worktree.
- MUST reject duplicate repository configurations rather than choose one implicitly.
- MUST use the selected repository's `checkout` for Git fetch and repository operations; the agent edits only its worktree.
- MUST create feature-branch worktrees beneath the selected repository's `worktrees`, one directory per branch.
- MUST keep Spec/Ticket reads and updates on the harness repository; the Execution ledger keys each Spec's work item by its number.
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
labels. Which optional activities (planning, chore, review) run is data on the routed target, not a Python delegate. Ticket iteration,
Initiative history, retry/rollback, and one-commit validation do not vary with
the target. There is no directory discovery or repository cloning.

See the [multi-repository example](../../README.md#different-agent-workflows-per-repository)
for configuration and an Implement/Review delegate.
