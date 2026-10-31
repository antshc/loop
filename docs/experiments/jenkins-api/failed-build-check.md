# [Research] Failed PR build state mapping (PR-526 build 3)

Closes the first gap in [README.md](./README.md). Run 2026-10-31, read-only (GET only), Jenkins 2.452.2 at `jenkins.example.com`, repo `example-org/example-repo`, job `CI/ProductA/Official builds and PR checks/PR-526`.

## Bottom line

- **A failed Jenkins build shows in GitHub as state `error`, not `failure`.** Build 3 ended `FAILURE`. Its newest status on the built commit is `error` with the text "This commit cannot be built". The combined commit state is `failure`. Map Jenkins `FAILURE` to the status state `error`, and read the combined state as `failure`.
- **The status history gives a full timeline.** The entries run: "scheduled to be built" (no build number), then two "being built" entries (build 3), then `error`. Take the newest entry. This confirms the queued, building and finished phases for a failed build.
- **Jenkins reports on the commit it built, which is not always the latest PR head.** The PR (now closed) has a later head commit with no Jenkins status at all. A lookup by the current PR head finds nothing and reads as "pending". Look up the status on the commit that Jenkins built, or on the commit from the status of the run you track.
- **A failed build marks no stage as failed.** The pipeline status is `FAILED`. Every stage is `SUCCESS` or `NOT_EXECUTED` (Blue Ocean: `NOT_BUILT` or `SKIPPED`). The failure comes from the pipeline's own error step ("script returned exit code 1", after an echoed "Build Failed!"). Do not search for a failed stage. Read the last lines of the log.
- **The aborted mapping is still not observed.** No aborted PR build was available. **Not confirmed**

## What we did

- **Jenkins state:** Read builds 1 to 3 of the PR-526 job. Builds 1 and 2 are `SUCCESS`. Build 3 is `FAILURE`, finished, with 27 minutes duration. Build 3 had the same four parameters as the build of the trigger experiment. The last failed and last unsuccessful builds are build 3. **Confirmed**
- **Stage state:** Read the run through the pipeline stage API and the Blue Ocean API. No stage and no node has the failed result. All later stages are not executed. **Confirmed**
- **Log end:** Read the last 8 KB of the log through Blue Ocean. It ends with "Build Failed!", "script returned exit code 1", "GitHub has been notified of this commit's build result" and `Finished: FAILURE`. **Confirmed**
- **GitHub state:** Read the status history, the combined status and the check runs of the built commit. Check runs have no Jenkins entry, as in the earlier experiment. **Confirmed**
- **Aborted build:** Did not check. No such build exists for this job. **Not confirmed**

## Observations

| Source | Value |
|---|---|
| Jenkins build 3 | `FAILURE`, not building, 27 min |
| Pipeline stage API | `FAILED`; later stages `NOT_EXECUTED` |
| Blue Ocean nodes | no failed node; later nodes `NOT_BUILT` / `SKIPPED` |
| GitHub status (newest) | `error`, "This commit cannot be built" |
| GitHub combined state | `failure` |
| GitHub status (older) | `pending` "being built" (build 3) twice, `pending` "scheduled to be built" (no build number) |
| Status on latest PR head | none; combined state reads `pending` with 0 statuses |

Full mapping seen so far:

| Jenkins | GitHub status state | Description |
|---|---|---|
| queued | `pending` | scheduled to be built |
| building | `pending` | being built |
| `SUCCESS` | `success` | (earlier experiment) |
| `FAILURE` | `error` | cannot be built |
| `ABORTED` | not observed | not observed |

## Remaining work

- Find or create an aborted PR build, and record its GitHub status state and text.
- Change the PR check experiment to read the status on the commit Jenkins built, not on the current PR head.

## Next steps

1. Treat both `error` and `failure` as "CI failed" in the loop CI wait step.
2. For a failed build, read the end of the log first, then the step error messages.

## References

- [Experiment: PR check state](./jenkins-pr-check-state.md)
- [Experiment: log search](./jenkins-log-search.md)
- [Research: log search without downloading](./jenkins-log-search-without-downloading.md)
