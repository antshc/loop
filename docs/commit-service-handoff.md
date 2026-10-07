# Commit Service Deep Module

Goal: extract commit/history behavior from `GitClient` into a focused deep module based on current `loop` usage.

## Commit entity

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Commit:
    path: Path
    sha: str
    subject: str
    body: str = ""

    @property
    def message(self) -> str:
        return self.subject if not self.body else f"{self.subject}\n\n{self.body}"
```

Meaning:

```text
path    repository/worktree containing the commit
sha     commit hash
subject first line of commit message
body    remaining commit message
message subject + body
```

Current workflow mainly uses `sha` and `subject`. `body` is included so the entity represents the complete commit message and can later expose richer history to prompts without changing the contract.

## Commit service

```python
from pathlib import Path
from typing import Protocol


class CommitService(Protocol):
    def head(self, path: Path) -> Commit:
        """
        Return current HEAD as a Commit.

        Internal Git logic:
            git log -1 --format=<sha, subject, body>

        Replaces separate:
            head()
            head_subject()

        Caller should not need to query HEAD hash and message separately.
        """

    def since(self, commit: Commit) -> list[Commit]:
        """
        Return commits after `commit` through current HEAD, oldest first.

        Internal Git logic:
            git log --reverse <commit.sha>..HEAD

        Parse each commit into:
            Commit(path, sha, subject, body)

        Used to determine what an agent produced during one attempt.

        Replaces:
            commits_between(...)
        """

    def find_since(
        self,
        path: Path,
        base: str,
        *,
        subject_prefix: str,
    ) -> list[Commit]:
        """
        Return commits after `base` whose subjects start with `subject_prefix`,
        oldest first.

        Internal Git logic:
            git log --reverse --fixed-strings
                --grep=<subject_prefix>
                <base>..HEAD

        Git --grep may also match commit bodies, therefore implementation MUST
        additionally verify:
            commit.subject.startswith(subject_prefix)

        Used for Initiative commit history inserted into agent prompts.

        Replaces:
            commits_with_prefix(...)
            recent_commits(...) where no longer required
        """

    def restore(self, commit: Commit) -> None:
        """
        Restore the worktree to `commit` and discard everything produced after it.

        Internal Git logic:
            git reset --hard <commit.sha>
            git clean -fd

        Used after a failed agent attempt.

        Replaces:
            reset_to(...)
        """
```

## Intended usage

### Capture attempt boundary

```python
before = commits.head(worktree)

# run agent

created = commits.since(before)
```

The workflow no longer handles raw `HEAD` hashes.

### Validate one delivery commit

```python
created = commits.since(before)

if len(created) != 1:
    ...

commit = created[0]

if not commit.subject.startswith(expected_prefix):
    ...

if dev_result.commit != commit.sha:
    ...
```

This replaces:

```text
head()
commits_between()
head_subject()
```

with one commit-oriented abstraction.

### Roll back failed attempt

```python
commits.restore(before)
```

The caller does not know that restore means:

```text
git reset --hard
git clean -fd
```

### Initiative history for prompt

```python
history = commits.find_since(
    path=worktree,
    base=run.base_branch,
    subject_prefix=f"ccode({run.initiative}|",
)
```

Prompt rendering can preserve current behavior:

```python
initiative_commits = [
    f"{commit.sha[:7]} {commit.subject}"
    for commit in history
]
```

Current prompt therefore still receives:

```text
abc1234 ccode(Checkout|9): earlier work
```

The entity nevertheless retains `body` for future prompt enrichment.

## Responsibility boundary

```text
Commit
    path
    sha
    subject
    body
    full message

CommitService
    current HEAD commit
    commits produced since a commit
    filtered history since a base
    restore a previous commit point

Workflow
    expected commit subject convention
    exactly-one-commit rule
    compare reported SHA
    decide when rollback is required
    decide how commits are rendered into prompts
```

## Keep outside CommitService

```text
Commit.subject_prefix(...)
```

`ccode(<identifier>):` is a workflow convention, not Git behavior.

```text
is_clean(...)
has_changes(...)
```

These describe working-tree state, not commits. Keep them in a repository/worktree status service.

```text
commit(...)
```

The current `loop` workflow does not create delivery commits itself. The coding agent creates them.

## Deep-module rule

The workflow should operate on `Commit` entities and intent:

```python
before = commits.head(worktree)
created = commits.since(before)
commits.restore(before)
```

It should not need to know about:

```text
HEAD
git log formatting
Git revision ranges
git reset --hard
git clean -fd
--grep behavior
```
