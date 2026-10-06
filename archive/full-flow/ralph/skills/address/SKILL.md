---
name: address
description: Address the review discussion on a pull request — group its comments into issues, investigate each against the code, fix what needs fixing, and reply to every thread. Rerunnable: a run with nothing left to answer stops at the gate. Use when given a PR URL and asked to handle its review comments.
argument-hint: '<PR URL> (e.g., "https://github.com/owner/repo/pull/1245")'
---

# Address review discussions

**Skill base directory:** `{{skillDir}}` is the absolute path of the directory holding this `SKILL.md`. Load `references/github-api.md` and `references/investigation.md` from there, and run `scripts/pr_discussion_state.py` from there — never via a bare relative path, which resolves against the runtime CWD (a worktree, not the skill folder).

The run is safe to repeat. What is left to do is decided by the gate in §0, never by memory of an earlier run.

## 0. Rerun gate

The first action of the run, before any checkout and before reading a single comment body:

```bash
python3 {{skillDir}}/scripts/pr_discussion_state.py "{{input}}" --pretty
```

The script resolves `$actingLogin`, fetches every review thread, discards the resolved ones and any comment still attached to an unsubmitted (`PENDING`) review, and reports each survivor as `answered` (its last comment is `$actingLogin`'s) or `pending`. A `fix!:` in that last comment of `$actingLogin`'s own keeps the thread `pending` — a self-note that still needs work, not an answer. Its JSON is the only source of the discussion this run; `pr` gives `{{owner}}`, `{{repo}}`, `{{number}}`, and `headRef`/`baseRef` give `$headRef`/`$baseRef`.

| `action` | Do |
|---|---|
| `skip` | Report `{{counts.answered}} threads, all answered — nothing to do` and **stop**. Do not check out, do not read a comment, do not analyse. |
| `stop` | Report `reason` verbatim and stop. |
| `proceed` | `threads[]` is the working set. Continue. |

Done when `action` is `proceed` and the working set is in hand.

## 1. Setup

**Load `references/github-api.md` now.** Every `gh` invocation in this run is copied from it, and nothing below restates one.

1. Run `/harness` skill from cwd. Unavailable, or exits reporting `missing` — use cwd as `$HARNESS_REPO_PATH`. Exits reporting `invalid` — **exit** and report. Otherwise `HARNESS_REPO_PATH := harnessRepoPath` from its emitted JSON.
2. Derive the checkout for `{{owner}}/{{repo}}` (the PR's repository, from §0): equals `$HARNESS_REPO_PATH`'s own `origin` remote (`git -C "$HARNESS_REPO_PATH" remote get-url origin`, normalized the same way as a `repos` entry) → `CODEBASE_REPO_PATH := $HARNESS_REPO_PATH`. Otherwise → `CODEBASE_REPO_PATH := $HARNESS_REPO_PATH/workspace/{{repo}}`. Confirm the checkout exists and its own `origin` normalizes to `{{owner}}/{{repo}}` — missing or a clone of another repository → **stop** before touching any review thread, post no replies, and report why. Never read `repos` to make this decision.
3. Run `/create-worktree` skill with `$CODEBASE_REPO_PATH $baseRef $headRef`. Parse the output to capture `WORKTREE_PATH`. Switch into `WORKTREE_PATH`.
4. Run `/ralph-build` skill with `$HARNESS_REPO_PATH $WORKTREE_PATH`. A non-pass build → **exit** and report. Never fix threads on a broken build.

Done when `git rev-parse --abbrev-ref HEAD` prints `$headRef` and the build gate has passed.

## 2. Learn how this repo verifies itself

Read `README.md`, `AGENTS.md`, and `copilot-instructions.md` for this repo's build and test commands; record them as `$buildCommand` and `$testCommand`.

Done when each holds a real command or is recorded as absent. Absent → say so in the final report and resolve issues without that gate.

## 3. Read the working set

Each entry in `threads[]` carries `id`, `anchor` (`path`, `line`, `startLine`, and `basis` — `original` means the thread outdated and the position is the one it was written against, with `diffHunk` holding the surrounding source), `mode`, and every comment in order.

| `mode` | Means | Read |
|---|---|---|
| `first-pass` | `$actingLogin` has never commented in this thread. | The whole thread. |
| `follow-up` | `$actingLogin` replied, and someone commented after. | `newComments` only — `myLastReply` is the answer already given, and repeating it is the one failure mode here. |

Done when every thread's target code has been located from its `anchor`, falling back to `diffHunk` and then to the symbol named in the comment when `basis` is `unknown`.

## 4. Group the discussion into issues

One `issue` is one underlying problem, however many comments describe it. Cluster on the same file and symbol, a comment that points at another ("same as above", "and here"), a reviewer restating one concern across several files, or a review body elaborated in a thread.

Record per `issue`: `issueId`, a one-sentence statement, its member thread ids, the affected paths and line ranges, and the verbatim comment excerpts.

Done when every thread in the working set belongs to exactly one `issue` and the assigned count equals the working-set count.

## 5. Triage each issue

| Class | The issue is | Next |
|---|---|---|
| `chore` | A rename, a typo, wording, formatting, an unused import — the reviewer's instruction is the whole specification. | Step 7 |
| `substantive` | Behaviour, correctness, design, performance, or security — knowing whether the reviewer is right requires reading the code. | Step 6 |

A `follow-up` issue whose `newComments` raise no fact the earlier answer did not already cover skips the investigation and goes straight to the reply.

Done when every `issue` carries one class.

## 6. Investigate every substantive issue

**Follow `references/investigation.md`** for the discovery command, the dispatch rules, the prompt template, and the verdict contract.

Done when every `substantive` issue holds a `verdict` backed by at least one `path:line` citation.

## 7. Resolve each issue on its own

Take the issues one at a time. Per `issue`:

1. `chore`, or `verdict: fix-needed` → apply the change, scoped to that `issue` alone.
2. Run `$buildCommand`, then `$testCommand` over the changed files. A failure this `issue` caused → `git checkout -- {{paths}}` and carry the `issue` forward as `unclear`.
3. `git commit`, naming the `issue` and the threads it answers.

An `issue` whose `verdict` is `no-change-needed` or `unclear` produces no commit.

Once every `issue` has been through the loop, `git push` to `$headRef` — once, for all of them. A rejected push → stop and report before replying; the remote has moved and every reply would be describing code that is not there.

Done when every `issue` has reached a terminal state and the branch is pushed.

## 8. Reply

Plain prose, the way an engineer answers a colleague. No prefix tag, no HTML marker, no canned sentence — nothing reads a reply body, so nothing needs to recognise it.

| Situation | Reply |
|---|---|
| Committed and pushed. | `Fixed!` — alone. The reviewer can read the diff. |
| Fixing it later in this run. | One line saying what you will change; a second comment `Fixed.` once pushed. |
| Agreed, but it would balloon the PR. | One sentence of agreement, then where it lands — follow-up PR, ticket, next round. |
| `no-change-needed`. | Two to five sentences of root cause carrying the `path:line` citations, then that you left the code as it is. |
| Their patch names symbols the repo lacks. | Name the missing symbol, then the smallest edit that meets their intent. |
| Ambiguous or underdetermined. | Ask outright: quote the phrase, state the reading you would implement. |

Name the exact type, method, or file. Cite the package, pinned version, and where the pin lives when the answer rests on third-party behaviour. Never restate an answer already in the thread, and never restate the reviewer's comment back at them. Match their register — a one-line typo report gets a one-line answer.

Done when every thread in the working set carries exactly one new comment authored by `$actingLogin` — which is exactly what makes the next run's gate return `skip`. Leave every thread open for the reviewer to close.

## 9. Cleanup

Run `/delete-worktree` skill with `$CODEBASE_REPO_PATH $WORKTREE_PATH $headRef`.

Done when `WORKTREE_PATH` no longer exists and the local `$headRef` branch is gone; the remote branch and the PR are untouched.

## 10. Report

| Section | Contents |
|---|---|
| Gate | The `counts` from §0 and the `action` that let the run continue. |
| Issues | One row per `issue`: `issueId`, class, verdict, commit sha or `none`, threads replied. |
| Verification | `$buildCommand` and `$testCommand`, or the note that they are absent and which issues went unverified. |
| Residual findings | Anything the investigation turned up that no reviewer asked about. Reported, not fixed. |

## Rules

- Push to `$headRef`, with a bare `git push`. `--force`, `--force-with-lease`, `--amend`, and rebasing a pushed commit stay off the table — the reviewer is reading these commits as you write them.
- One `issue`, one commit. One run, one push.
- Ask the reviewer outright the moment the `evidence` runs out. A question costs the reviewer less than a wrong guess.
- Every thread in the working set ends this run with a reply. A thread left unanswered is a thread the next run cannot tell from a new one.

