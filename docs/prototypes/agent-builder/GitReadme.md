```
# 1. refresh origin refs (BranchService.fetch)
git -C /path/to/clone fetch --all --prune

# 2. validate the branch name
git -C /path/to/clone check-ref-format --branch loop/run-1a2b3c4d

# 3. create or move the local branch at the start ref

Check whether the local branch exists:
git -C /path/to/clone show-ref --verify --quiet refs/heads/loop/run-1a2b3c4d

Exit code 0 means it exists, so use git branch -f.
A non-zero exit code means it doesn't, so use git branch.

#    start ref = origin/<branch> if it exists, else origin/<base>
git -C /path/to/clone branch loop/run-1a2b3c4d origin/main          # new
git -C /path/to/clone branch -f loop/run-1a2b3c4d origin/main       # branch already exists locally

# 4. attach the worktree at <target> (= worktree_root/<branch>)
git -C /path/to/clone worktree add /path/to/worktree_root/loop/run-1a2b3c4d loop/run-1a2b3c4d
```