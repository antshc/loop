# Harness Repository Topology
**Type:** Architecture Pattern

## Purpose

The harness contains agent instructions, skills, Specs, and Tickets. The dev workflow runs agents on isolated Git worktrees without modifying its checkout.

## Current implementation: single repository

`workflows/dev.py` targets only the harness repository. A single immutable `HARNESS: Repository` constant declares the checkout, the worktree parent folder, and its GitHub identity. No repository registry or runtime target resolution is required.

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
    identity: RepositoryId


ROOT = Path(__file__).resolve().parents[1]
HARNESS = Repository(
    checkout=ROOT,
    worktrees=ROOT / "workspace" / f"{ROOT.name}.worktrees",
    identity=RepositoryId("antshc", "loop"),
)
```

- MUST validate each Spec's `repo:target:<owner/repo>` label against `str(HARNESS.identity)`, case-insensitively.
- MUST mark missing, malformed, duplicated, or foreign targets `hitl` without fetching Git or opening a worktree.
- MUST use `HARNESS.checkout` for Git fetch and repository operations; the agent edits only its new worktree.
- MUST create feature-branch worktrees beneath `HARNESS.worktrees`, one directory per branch.
- MUST use `HARNESS.identity.owner` and `HARNESS.identity.repo` for GitHub identity and execution logs; do not split slugs at call sites.
- MUST publish PRs to the harness GitHub repository using its repository-bound `PullRequests` client.
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

A harness wrapping independent codebase repositories remains an architectural possibility but is **not supported by the current dev workflow**. Supporting it requires an explicit target repository configuration, separate repository-scoped PR clients, and new tests. Do not implicitly reintroduce registry or pool behavior into the single-repository workflow.
