---
name: dev
description: AFK development loop — implements approved sub-tickets of a spec issue, commits and pushes, then runs approved spec-wide functional-testing tickets through Testy.
argument-hint: '<spec-issue-number-or-url>'
---

## 1. Read state

The last comments fidlet by the ccode prefix:
```bash
git log -n 5 --format="%H%n%ad%n%B" --date=short
```


## 2. Select next task

Pick the next task. Prioritize in this order (first match wins); break ties within a tier by lowest issue number:

1. Critical bugfixes
2. Development infrastructure — tests, types, dev scripts are precursors to features
3. Tracer bullets — tiny end-to-end slices that validate the approach early
4. Polish and quick wins
5. Refactors

**Emit** the selected `#<number> — <title>` before **Invoke implementation agent**. Fetch its current body and comments with `gh issue view <number> --repo "$repo" --json number,title,body,comments,labels,state`; recheck eligibility before dispatch. Fetch the selected issue directly to refresh all comments beyond the shared fetcher's bounded comment preview.

## 3. implementation task


## 4. Distill

Distill Codey's SUMMARY into Implementation Decisions. Use this in **Commit & push** (commit body) and **Update Spec** (spec update).

**Implementation Decisions** — 1–3 compressed technical bullets:
- Short, implementation-oriented statements.
- No file paths or code snippets.
- No filler — every word carries information.

## 7. Commit (source repo)

Operate in `WORKTREE_PATH`. Build a single commit:

  - **SUBJECT** → Use **ccode:** prefix, then Codey's one-line commit summary
  - **SUMMARY** → commit body: Implementation Decisions block, plus Chorey's `SUMMARY` when it changed files
  - **FILES** → Codey's list of files changed, plus Chorey's
  - **NOTES** → Codey's `NOTES`, plus Chorey's findings not applied
- Otherwise (Chorey did not run, or ran and changed nothing): build the commit from Codey's report fields and the distilled outputs from **Distill** alone —
  - **SUBJECT** → Use **ccode:** prefix, then one line commit summary
  - **SUMMARY** → commit body (Implementation Decisions block)
  - **FILES** → list of files changed
  - **NOTES** → blockers or context for the next iteration

Commit and push regardless of Codey's `STATUS` (**complete**, **partial**, or **blocked**). Skip an empty commit when no staged changes exist, but still push. Check each command separately; a failed commit or push exits before functional testing:

```bash
git add -A
git commit -m "<SUBJECT>" -m "<SUMMARY>" -m "<FILES>" -m "<NOTES>"
```

# COMMIT HARNESS REPO

Run **once**, after **FUNCTIONAL TESTING** completes, including deferred or unsuccessful verification. Operate in `$HARNESS_REPO_PATH` (resolved in **Resolve harness settings**) — never the worktree.

- Stage **any change** in the harness root (`git add -A`), on top of whatever is already staged.
- If nothing is staged, skip the commit (no empty commits).
- **Emit** the commit SHA, or "nothing to commit".

Stage all changes, commit if anything is staged, and push — using the appropriate shell syntax for the current platform.



# Handle task result

Maintain a per-issue attempt counter for this session, keyed by issue number.
- **complete**: Close the issue with `gh issue close <number> --repo "$repo"`.
- **partial**: Increment the issue's attempt counter. If this is the 2nd consecutive `partial` for the issue, add `hitl` with `gh issue edit <number> --repo "$repo" --add-label "hitl"`; otherwise comment with the agent's SUMMARY using `gh issue comment <number> --repo "$repo" --body "..."`.
- **blocked**: Add `hitl` label with `gh issue edit <number> --repo "$repo" --add-label "hitl"`.


# RULES

- ONE TASK AT A TIME. Each Codey or Testy invocation handles one ticket; Ralph processes tickets sequentially.
- ALWAYS re-read state before selecting the next task — context changes after each commit.
- An empty implementation queue proceeds to **CREATE PULL REQUEST**, then **FUNCTIONAL TESTING**. Report unresolved implementation dependencies and testing outcomes honestly; the `spec`-labeled issue is owned by the user and stays open.
- IMPLEMENTATION ITERATION CAP: after 2x the initial eligible implementation-task count, stop the implementation loop and continue to **CREATE PULL REQUEST**; incomplete dependencies remain pending. Functional tickets use their own once-per-Ralph-invocation handling and network retry budget; context resets retain both.
- Failed functional-test execution or its testing-skill invocation is evidence for **FUNCTIONAL TESTING**, not an orchestrator exit. Other failed skill invocations, commits, `git push`, or `gh` calls still exit with the error; never claim a bug or approval label was saved when its tracker write failed.
