# Git commands per strategy

`git -C <repository>`; `<target>` = `root_path/<branch>`.

## head

No git commands.

## merge-to-head

```
# branch = tmp_<hex>, created from HEAD
git check-ref-format --branch <branch>
git show-ref --verify --quiet refs/heads/<branch>             # missing -> create
git show-ref --verify --quiet refs/remotes/origin/<branch>    # missing -> start from HEAD
git branch <branch> HEAD
git worktree add <target> <branch>

# ... agent runs ...

git worktree remove <target>
# on success only:
git merge --ff-only <branch>
git branch -d <branch>
```

If the run fails, only `worktree remove` runs and the temp branch is kept.

## branch (branch, base_branch?)

```
git fetch --all --prune
git check-ref-format --branch <branch>

# local branch exists (exit 0): reuse as-is, no branch command
git show-ref --verify --quiet refs/heads/<branch>

# otherwise create it from origin/<branch> if that exists, else base_branch (default HEAD)
git show-ref --verify --quiet refs/remotes/origin/<branch>
git branch <branch> origin/<branch>        # or: git branch <branch> <base_branch|HEAD>

git worktree add <target> <branch>

# ... agent runs ...

git worktree remove <target>               # branch is kept
```
