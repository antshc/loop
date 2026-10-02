---
name: fix
description: Fix review comments by applying the suggested changes.
argument-hint: '<PR URL> (e.g., "https://github.com/owner/repo/pull/1245")'
---

# Setup

Parse `{{input}}` to extract `<owner>`, `<repo>`, `<number>` from `https://github.com/{owner}/{repo}/pull/{number}`.

1. Run `/harness` skill from cwd. Unavailable, or exits reporting `missing` — use cwd as `$HARNESS_REPO_PATH`. Exits reporting `invalid` — **exit** and report. Otherwise `HARNESS_REPO_PATH := harnessRepoPath` from its emitted JSON.
2. Derive the checkout for `<owner>/<repo>`: equals `$HARNESS_REPO_PATH`'s own `origin` remote (`git -C "$HARNESS_REPO_PATH" remote get-url origin`, normalized the same way as a `repos` entry) → `CODEBASE_REPO_PATH := $HARNESS_REPO_PATH`. Otherwise → `CODEBASE_REPO_PATH := $HARNESS_REPO_PATH/workspace/<repo>`. Confirm the checkout exists and its own `origin` normalizes to `<owner>/<repo>` — missing or a clone of another repository → **stop** before creating any worktree or touching any review thread, post no replies, and report why. Never read `repos` to make this decision.
3. Get PR branch names:
  ```bash
  eval "$(gh pr view <number> --repo <owner>/<repo> --json headRefName,baseRefName \
    -q '"branch=\(.headRefName)\ntarget_branch=\(.baseRefName)"')"
  ```
4. Run `/create-worktree` skill:
   ```
  /create-worktree $CODEBASE_REPO_PATH $target_branch $branch
   ```
   Parse the output to capture `WORKTREE_PATH`. Switch into `WORKTREE_PATH`.
5. Run `/ralph-build` skill with `$HARNESS_REPO_PATH $WORKTREE_PATH`:
   A non-pass build → **exit** and report. Never fix threads on a broken build.
6. Run thread fetch from inside the worktree: `python3 <skill-directory>/github/fetch_threads.py <pr_url>`

Output is a JSON array of actionable threads. Each thread has this structure:

```json
{
  "thread_id": "str",
  "prefix": "str",
  "path": "str",
  "lines": "str",
  "actionable_comment": "str",
  "comments": [{"author": "str", "body": "str"}]
}
```

## Apply fix to thread

`actionable_comment` contains the actual fix instructions. `comments` contains the history of the thread conversation.

## 1. Explore

- Read the file. Review each thread's `lines`, `actionable_comment`, and `comments` if needed for more context history.
- **If a thread is unclear** (vague, ambiguous, conflicting interpretations, missing context) — mark it for clarification, DO NOT fix it.

## 2. Implement

- Fix the thread actionable comment by making only the necessary changes to address the issue without altering unrelated code.

## 3. Verify

- Run `/ralph-build` skill, then run the tests for changed files.

## 4. Commit, push

- `git commit` with summary of changes, files, and any blockers.
- `git push` (no flags).

## 5. Reply

Reply to each thread via GraphQL:

```
gh api graphql -f query='
  mutation($threadId: ID!, $body: String!) {
    addPullRequestReviewThreadReply(input: { pullRequestReviewThreadId: $threadId, body: $body }) {
      comment { id }
    }
  }
' -f threadId=<thread_id> -f body='<reply>'
```

- **Fixed threads:** reply with `Fixed.`
- **Unclear threads:** reply with `question: <specific clarification question>` — reference the ambiguity, offer options when possible. No generic questions.

Do not resolve threads.

## 6. Cleanup

```
/delete-worktree $CODEBASE_REPO_PATH $WORKTREE_PATH $branch
```

# Rules

- Never push to base branches (`main`, `master`); always push only to the PR branch. Never force-push, delete the remote branch, or rebase/amend pushed commits.
- If `git push` to the PR branch is rejected, stop and report.
- If unclear, reply `question:` — don't guess. Next run auto-skips threads with `question:`.
- Don't skip actionable threads without a reason.
