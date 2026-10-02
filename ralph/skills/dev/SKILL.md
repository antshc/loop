---
name: dev
description: AFK development loop — implements approved sub-tickets of a spec issue, commits and pushes, then runs approved spec-wide functional-testing tickets through Testy.
argument-hint: '<spec-issue-number-or-url>'
---

# WORKTREE SETUP

Before entering the orchestrator loop, resolve the spec and set up the worktree.

## 0. Resolve harness settings

1. Run `/harness` skill from cwd; retain its `harnessRepoPath` from the emitted JSON. Use it as `$HARNESS_REPO_PATH` for all harness-repo operations (specs, issues).

2. Bring `HARNESS_REPO_PATH` up to date with its remote before any reads or the final push depend on it.
**GUARD**:  Run only when `/harness` found its settings file and emitted a non-empty `harnessRepoPath`.
```bash
git -C "$HARNESS_REPO_PATH" fetch --all --prune
git -C "$HARNESS_REPO_PATH" pull
```

If the pull exits non-zero (conflicts detected), discard local state in favor of the remote — the harness repo is only ever read from, so it is safe to reset:

```bash
git -C "$HARNESS_REPO_PATH" reset --hard "@{upstream}"
```

`/harness` unavailable, or exits reporting `missing` → use cwd as `$HARNESS_REPO_PATH`. `/harness` exits reporting `invalid` → **exit** and report.

## 1. Resolve spec

A `<spec-issue-number-or-url>` argument is **required**. If not provided, **exit** and report `Usage: /dev <spec-issue-number-or-url>`.

Assign its issue number once and reuse everywhere as `$spec`:

```bash
spec="<spec-issue-number>"
```

Fetch the spec issue:

```bash
repo=$(git -C "$HARNESS_REPO_PATH" remote get-url origin | sed -E 's#^git@[^:]+:##; s#^https?://[^/]+/##; s#\.git$##')
gh issue view "$spec" --repo "$repo" --json number,title,body,labels,state
```

`repo` resolves the harness remote (tasks live there) and is reused for all harness-repo commands below. Run this before the worktree is created.

If the issue is missing or lacks the `spec` label, **exit** and report "Spec not found: `$spec`".

Parse the fenced ```` ```metadata ```` block from `spec.body` — the only source read; legacy bold header lines (`**Initiative ID:**`, `**Target Branch:**`, `**Feature ID:**`) are never parsed, even when a `metadata` block is absent:
- `initiative_id`
- `target_branch` — this branch lives in the **source repository** the worktree is created from, not necessarily the harness repo.

Read `repository` (`owner/name`) from the spec's single `repo:<owner>/<name>` label.

Any field missing, or no/several `repo:` labels → **exit** before creating any worktree, leave ticket labels unchanged, and report "Spec is missing required metadata."

Derive the checkout for `repository`: equals `$HARNESS_REPO_PATH`'s own `origin` remote (`git -C "$HARNESS_REPO_PATH" remote get-url origin`, normalized the same way as a `repos` entry) → `CODEBASE_REPO_PATH := $HARNESS_REPO_PATH`. Otherwise → `CODEBASE_REPO_PATH := $HARNESS_REPO_PATH/workspace/<name>` (`<name>` is `repository`'s part after the slash). Confirm the checkout exists and its own `origin` normalizes to `repository` — missing or a clone of another repository → **exit** before creating any worktree, leave ticket labels unchanged, and report why. Never read `repos` to make this decision.

## 2. Compute feature branch name

Format: `<version_underscored>_<spec-title-slug>` — or just `<spec-title-slug>` when `target_branch` carries no version.

Rules:
- Take the version from the target branch (e.g. `release/1.3.10` → `1.3.10`), replace dots with underscores → `1_3_10`. No version segment found (e.g. `main`, `develop`) → the branch name is the slug alone, with no version prefix.
- Slugify the full spec title: lowercase, replace spaces and special chars (including `:`) with hyphens, strip consecutive hyphens, max 50 chars

Example: spec `PROJ-1234: Azure Storage Circuit Breaker`, target `release/1.3.10` → `1_3_10_proj-1234-azure-storage-circuit-breaker`

## 3. Create worktree

Run `/create-worktree` skill:

```
/create-worktree $CODEBASE_REPO_PATH <target-branch> <feature-branch>
```

Parse the output to capture `WORKTREE_PATH` and `BRANCH`; assign the latter to `branch` and reuse it as `$branch` for the rest of this skill. All subsequent code, git, and PR commands run inside `WORKTREE_PATH`; only the spec/issue commands target the harness `repo`.

## 4. Build

Run `/ralph-build` skill with `$HARNESS_REPO_PATH $WORKTREE_PATH`:

A non-pass build → **exit** and report. Never enter the orchestrator loop on a broken build.

---

# ORCHESTRATOR LOOP

Repeat the following loop until no eligible implementation tasks remain, then continue to **FUNCTIONAL TESTING**. Coding agents retain their focused repository/accessor/proxy integration verification before commit; the later phase verifies the whole spec.

## 1. Read state

Resolve `DEV_SKILL_DIR` from this installed `SKILL.md`'s folder. From `WORKTREE_PATH`, read recent commits and run the hook-synced shared issue fetcher against the harness repository and spec:

```bash
git log -n 5 --format="%H%n%ad%n%B" --date=short
python3 "$DEV_SKILL_DIR/github/fetch_issues.py" "$repo" --spec "$spec" --kind implementation
```

Run the commands sequentially and check each exit code. Parse the fetcher's JSON output as the task array; review the commits as recent-change context. The shared fetcher owns pagination, spec sub-issue selection, issue/comment serialization, and `IssueFilter` selection. `implementation` excludes `tests`, `spec`, and `hitl` (case-insensitive). A failed fetch exits rather than becoming an empty queue; an empty array ends only the implementation loop. Edit shared code in `tools/src/modules/github/`; `.githooks/pre-commit` generates this skill's `github/` copy.

## 2. Select next task

Pick the next task. Prioritize in this order (first match wins); break ties within a tier by lowest issue number:

1. Critical bugfixes
2. Development infrastructure — tests, types, dev scripts are precursors to features
3. Tracer bullets — tiny end-to-end slices that validate the approach early
4. Polish and quick wins
5. Refactors

**Emit** the selected `#<number> — <title>` before **Invoke implementation agent**. Fetch its current body and comments with `gh issue view <number> --repo "$repo" --json number,title,body,comments,labels,state`; recheck eligibility before dispatch. Fetch the selected issue directly to refresh all comments beyond the shared fetcher's bounded comment preview.

## 3. Invoke implementation agent

Read the installed `codey-*.agent.md` descriptions in the crew plugin's `agents/` directory. Choose the agent whose description best fits the selected issue's title and body; if several fit, choose the one central to the requested outcome. No matching technology → `general-purpose`. **Emit**: "Primary agent: <agent>."

After changing to `WORKTREE_PATH`, run the selected agent (or `general-purpose` if unavailable) via `runSubagent`. Its invocation directory is the worktree. For a general-purpose fallback, instruct it to implement the task, run focused verification, and return the five-field Codey report (including honest verification results). Use the following prompt (substitute actual values):

```
## TASK
- Title: <title>
- Body: <body>
- Comments: <comments>

## RECENT CHANGES
<last 5 commits from step 1>
```

## 4. Distill

Distill Codey's SUMMARY into Implementation Decisions. Use this in **Commit & push** (commit body) and **Update Spec** (spec update).

**Implementation Decisions** — 1–3 compressed technical bullets:
- Short, implementation-oriented statements.
- No file paths or code snippets.
- No filler — every word carries information.

## 5. Stage Codey's changes (source repo)

Operate in `WORKTREE_PATH`. Stage Codey's changes regardless of `STATUS` (**complete**, **partial**, or **blocked**) so Chorey has a staged diff to review; this only updates the index, no commit yet:

```bash
git add -A
```

## 6. Review (Chorey)

Run only when Codey's `STATUS` is **complete** and `chorey` is available; otherwise continue directly to **Commit & push** — reviewing unverified or broken work cannot preserve behavior that was never established.

After changing to `WORKTREE_PATH` (same invocation directory as Codey), run the `chorey` agent via `runSubagent` with no arguments; it reviews the staged diff directly (`git diff --cached`). Retain Chorey's report for use in **Commit & push**. Chorey's `STATUS` is informational only — it never changes the `STATUS` recorded in **Handle task result**, which always reflects Codey's report from **Invoke implementation agent**.

## 7. Commit & push (source repo)

Operate in `WORKTREE_PATH`. Build a single commit:

- When **Review (Chorey)** ran and its `FILES` field is not "none": run `git add -A` again to stage Chorey's cleanup, then build the commit from both reports —
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
git push -u origin "$branch"
```

## 8. Handle task result

Maintain a per-issue attempt counter for this session, keyed by issue number.

Read Codey's `STATUS` field from **Invoke implementation agent** — never Chorey's:

- **complete**: Close the issue with `gh issue close <number> --repo "$repo"`.
- **partial**: Increment the issue's attempt counter. If this is the 2nd consecutive `partial` for the issue, add `hitl` with `gh issue edit <number> --repo "$repo" --add-label "hitl"`; otherwise comment with the agent's SUMMARY using `gh issue comment <number> --repo "$repo" --body "..."`.
- **blocked**: Add `hitl` label with `gh issue edit <number> --repo "$repo" --add-label "hitl"`.


## 9. Update Spec

Using the Implementation Decisions from **Distill**, update the spec issue.

1. Fetch the open spec issue:
   ```bash
   gh issue view "$spec" --repo "$repo" --json number,body
   ```
2. If the spec is closed or unreadable, skip steps 3-4 below.
3. For the `Implementation Decisions` section, apply the merge logic, keeping the leading `metadata` block untouched:
   - If the section is absent from the spec body, append it.
   - Replace any entry that conflicts with or is superseded by a new decision.
   - Append decisions that are additive.
4. Write the updated body back:
   ```bash
   gh issue edit "$spec" --repo "$repo" --body "<updated-body>"
   ```

Return to **Read state**.

# CREATE PULL REQUEST

Run once, after the implementation loop ends (an empty implementation queue, or the iteration cap). Check whether a PR already exists for `$branch` targeting `<target-branch>`. Run from inside `WORKTREE_PATH` so the command targets the source repository's remote:

```bash
existing_pr=$(gh pr list \
  --head "$branch" \
  --base "<target-branch>" \
  --state open \
  --json url \
  --jq '.[0].url' 2>/dev/null)
```

**If `existing_pr` is non-empty**, a PR already exists — print `"PR already exists: $existing_pr"` and skip creation.

**Otherwise**, open a draft PR from inside `WORKTREE_PATH`:

```bash
gh pr create --draft \
  --title "[<initiative-id>]: <spec-title>" \
  --body "**Initiative ID:** \`<initiative-id>\`" \
  --base "<target-branch>" \
  --head "$branch"
```

# FUNCTIONAL TESTING

Run after **CREATE PULL REQUEST** and before **COMMIT & PUSH HARNESS REPO**. This phase processes only approved `tests` tickets; its outcomes never enter Codey's partial/blocked retry loop, and never amend the PR created above — a functional-test failure opens a `hitl` investigation issue instead (Step 4), without failing this run or posting anything to the pull request.

Copy this checklist and check off items as you complete them:
```markdown
Functional Testing Progress:
- [ ] 1. Commit/push the source revision and select approved tests tickets.
- [ ] 2. Resolve each ticket's spec and completed implementation dependencies.
- [ ] 3. Run Testy and retry transient network failures within budget.
- [ ] 4. Save evidence and close or escalate each testing ticket.
```

## 1. Publish revision and select tickets

Ensure all implementation and review changes are committed through **Commit & push**. Skip an empty commit, push `$branch`, and record its HEAD as `testedCommit`; a failed commit/push exits before running tests. This also applies to a resumed invocation with no implementation work. Keep the tested source revision fixed throughout this phase.

Refresh approved testing tickets with the same shared fetcher: `python3 "$DEV_SKILL_DIR/github/fetch_issues.py" "$repo" --spec "$spec" --kind tests`. Check its exit status before parsing its JSON output. This selects only open `tests` tickets without `spec` or `hitl`; implementation tickets never enter this phase. No eligible tickets → continue to **COMMIT & PUSH HARNESS REPO**. Track handled ticket numbers for this invocation so no ticket is processed twice.

## 2. Check readiness

For each selected ticket in issue-number order, read its current body, comments, labels, and state; skip it if approval was withdrawn or it closed. Resolve its **Parent Spec** (it must be `$spec`) and read that spec. Resolve every **Blocked by** issue and confirm the spec's implementation dependencies are complete: closed as completed, with implementation evidence. An open, inaccessible, missing, or closed-as-not-planned dependency is not completion. Report pending dependencies and continue to other testing tickets without running this one; never treat an empty coding queue as proof of completion.

## 3. Execute and retry

From `WORKTREE_PATH`, run `testy` via `runSubagent` with explicit inputs:

```text
## TASK
<testing ticket number, title, body, comments, and full parent spec>
## REVISION
<testedCommit, WORKTREE_PATH, HARNESS_REPO_PATH, known target environment>
```

Testy discovers applicable `testing-*` skills from every available scope; its report owns test selection, execution evidence, and failure classification. Missing Testy, missing guidance, or an interrupted invocation is `unverified`, handled below; do not route it to Codey or stop the phase.

Retry only the reported transient network-failed subset, at most **two retries after the initial attempt (three attempts total)** per testing ticket in this invocation. Retain the counter and all reports across context resets. Pass the subset, evidence, reset procedure, and attempt number under `## RETRY`. Require the safe rerun/reset procedure reported by Testy; if unavailable, report the subset unverified instead of replaying side effects. Do not reset the budget for different commands or add another retry layer. Assertion, authorization, configuration, and unknown failures are not network retries. A mixed report may retry its network subset while preserving assertion failures and passed results. Continue covered independent scenarios when others are unverified.

## 4. Save results and investigate

Aggregate all attempts by scenario, replacing only a retried scenario's prior transport outcome while retaining its attempt history. Every required scenario must have a final outcome. A missing/malformed report, zero intended tests, skipped required scenarios, unknown target revision, or missing coverage is unverified. Preserve available test output, relevant application logs, stack traces, and correlation IDs before worktree cleanup; publish concise redacted excerpts or durable artifact links, not temporary local paths. Unavailable logs do not block reporting.

Use the harness tracker through `/manage-backlog` actions: bind its `REPO` to the resolved harness `$repo`, never the worktree remote, and pass the current spec and ticket inputs explicitly. Run `/manage-backlog` skill **Comment on ticket** to save the tested commit/environment, requirement-to-scenario-to-test mapping, exact commands, outcomes, attempt counts, gaps, and evidence on the original testing ticket.

- **All scenarios passed:** Run `/manage-backlog` skill **Close ticket** with the execution evidence. Never infer a pass from a successful command alone.
- **Any failed or unverified scenario:** Keep the original ticket open with `tests`; Run `/manage-backlog` skill **Label ticket** to add `hitl` before creating follow-up work. Run `/manage-backlog` skill **Create sub-ticket** under `$spec` for one investigation containing all outstanding failures and gaps for this testing ticket: label `bug,hitl,repo:<repository>` if any test failed or network retries were exhausted; otherwise `hitl,repo:<repository>` for missing coverage or prerequisites. Include the parent spec and testing-ticket links, tested commit/environment, failed or uncovered scenarios, expected versus actual results, reproduction commands, retry history, and useful available logs. Reuse and update an already-linked open investigation covering these findings instead of duplicating it. Run `/manage-backlog` skill **Comment on ticket** to link the investigation back to the original ticket.

Report unsuccessful verification and continue with other eligible testing tickets, then **COMMIT & PUSH HARNESS REPO**. Removal of `hitl` is required for a later rerun. Investigation issues remain outside autonomous implementation while labeled `hitl`. A tracker write failure still exits and reports what was not saved; do not claim escalation succeeded.

Before leaving the phase, account for every testing ticket as passed, escalated, awaiting approval, or pending dependencies in this run's own report — never in the pull request created above. Functional failures never fail this run, never reopen or edit the pull request, and are never described as passing verification.

# COMMIT & PUSH HARNESS REPO

Run **once**, after **FUNCTIONAL TESTING** completes, including deferred or unsuccessful verification. Operate in `$HARNESS_REPO_PATH` (resolved in **Resolve harness settings**) — never the worktree.

- Stage **any change** in the harness root (`git add -A`), on top of whatever is already staged.
- If nothing is staged, skip the commit (no empty commits).
- **Emit** the commit SHA, or "nothing to commit".

Stage all changes, commit if anything is staged, and push — using the appropriate shell syntax for the current platform.

# CLEANUP WORKTREE

Run **once**, after **Commit & Push Harness Repo** completes — development on `$branch` is finished for this invocation.

```
/delete-worktree $CODEBASE_REPO_PATH $WORKTREE_PATH $branch
```

# RULES

- ONE TASK AT A TIME. Each Codey or Testy invocation handles one ticket; Ralph processes tickets sequentially.
- ALWAYS re-read state before selecting the next task — context changes after each commit.
- An empty implementation queue proceeds to **CREATE PULL REQUEST**, then **FUNCTIONAL TESTING**. Report unresolved implementation dependencies and testing outcomes honestly; the `spec`-labeled issue is owned by the user and stays open.
- IMPLEMENTATION ITERATION CAP: after 2x the initial eligible implementation-task count, stop the implementation loop and continue to **CREATE PULL REQUEST**; incomplete dependencies remain pending. Functional tickets use their own once-per-Ralph-invocation handling and network retry budget; context resets retain both.
- Failed functional-test execution or its testing-skill invocation is evidence for **FUNCTIONAL TESTING**, not an orchestrator exit. Other failed skill invocations, commits, `git push`, or `gh` calls still exit with the error; never claim a bug or approval label was saved when its tracker write failed.
