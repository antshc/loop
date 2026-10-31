# Experiment: Jenkins PR job state from a GitHub PR's checks alone

Builds on [jenkins-log-search-without-downloading.md](./jenkins-log-search-without-downloading.md) (section 6). Run 2026-10-31. Code: [`docs/prototypes/jenkins-api/pr-check-state/pr_check_state.py`](./pr-check-state/pr_check_state.py) (disposable).

## Bottom line

- **A GitHub PR number alone is enough to find the Jenkins job and read its state.** The Jenkins entry is in the commit status of the PR head, not in the check runs. Use the commit status as the single entry point for CI state.
- **The job address is always derivable. The build address exists only after the build starts.** A queued build has no build number in its status, so a wait loop must read the GitHub status again on each poll. Key the loop on the job address.
- **GitHub and Jenkins states agree for the cases seen** (running = pending, success = success). The failure and aborted mappings are expected but **Not confirmed**. Test them on a failed PR build before relying on them.
- **Jenkins builds a merge of the PR head, not the head itself.** Comparing commit IDs gives false mismatches. Do not compare them, or accept the merge parent.

## Question

Starting only from a GitHub PR number, can we find the Jenkins job URL and read the PR job's state (building, result, number, timing, parameters, queue, last/last-completed build) through the Jenkins API, and does the GitHub status agree with Jenkins? Read-only; no build is triggered.

## Setup

- GitHub repo `example-org/example-repo`, PRs #518 (head `10d65e8532`, build running/queued during the run), #519, #525, #530 (finished). `gh` CLI, read-only.
- Jenkins `jenkins.example.com` 2.452.2, Basic auth from a credentials file named by `JENKINS_CREDENTIALS_FILE`; `requests` (+`verify=False`); only `GET …/api/json?tree=…`.
- Run: `JENKINS_CREDENTIALS_FILE=<file> /tmp/jenkins-venv/bin/python pr_check_state.py 518 [--wait --timeout S --interval S]`.

## Method

1. `gh api repos/R/pulls/N` -> `head.sha`, `merge_commit_sha`.
2. `gh api repos/R/commits/<sha>/statuses` and `…/check-runs`; take the first (newest) `continuous-integration/jenkins/pr-head` status; record whether any check-run mentions Jenkins.
3. Parse `target_url` into job URL and build URL (transformations below).
4. `GET <job>/api/json?tree=inQueue,queueItem[...],lastBuild,lastCompletedBuild,lastSuccessfulBuild,lastFailedBuild` and `GET <build>/api/json?tree=number,building,result,timestamp,duration,estimatedDuration,queueId,actions[parameters[name,value],lastBuiltRevision[SHA1],causes[shortDescription]]`. If the status has no build number, `lastBuild` is described instead.
5. Compare GitHub state vs Jenkins (`building`/`result`), the built SHA vs the PR, and the status build number vs `lastBuild`.
6. `--wait`: re-resolve the status and re-read Jenkins every `--interval` until the build exists, `building=false` and `result != null`; exit 0 when done, 2 on timeout.

## URL transformations

`target_url` is the Display URL API redirect.

| Input | Result |
|---|---|
| `…/job/PR-518/1/display/redirect` | build `…/job/PR-518/1/`, number `1`, job `…/job/PR-518/` |
| `…/job/PR-518/display/redirect` (no build number) | job `…/job/PR-518/`; build unknown (queued) |
| `…/job/PR-518/7/display/redirect?page=artifacts#x` | query/fragment dropped, number `7` |

Rule: keep scheme+host and the path only; strip trailing `/display/redirect`; match `((/job/<seg>)+)(/<digits>)?/?`; job URL = group 1 + `/`; build URL = job URL + number + `/`. Segments stay URL-encoded (`Official%20builds%20and%20PR%20checks`), so they are reused verbatim for `<url>api/json`. Full job path here: `/job/CI/job/ProductA/job/Official%20builds%20and%20PR%20checks/job/PR-<n>/`.

## Observations

- **Which GitHub API carries Jenkins:** the commit status API only. `check-runs` for PR 518's head had 9 runs (CodeQL, `Analyze (…)`, `jira-integration / …`, `repository-config`), none from Jenkins. The only status context on the sha is `continuous-integration/jenkins/pr-head`.
- **PR 518, finished-build path not available; two states seen on the same head sha (`10d65e8532`):**
  - Early probe: status `pending` "This commit is being built", `…/PR-518/1/display/redirect`.
  - Later (14:09:40Z status): `pending` "This commit is scheduled to be built", target_url **without** build number. Jenkins: job `inQueue: true`, queue item `Queue$BlockedItem`, `why: "Build #1 is already in progress (ETA: N/A)"` (consistent with `DisableConcurrentBuildsJobProperty`), `lastBuild #1`, `lastCompletedBuild/lastSuccessful/lastFailed: null`. Build #1: `building: true`, `result: null`, started 2026-10-31T13:52:51Z, `duration 0`, `estimatedDuration -1`, `queueId 9042408`, cause `Branch indexing`, no `ParametersAction` (parameters `{}`), ~1150 s elapsed at read time.
  - The script reports this as a mismatch note: status has no build number -> build still queued; `lastBuild #1` running.
  - Status history for the sha (`/statuses`) also holds stale entries from 2026-10-14 for `…/PR-518/1/…` (`pending`, then `error` "This commit cannot be built"), i.e. the same URL referred to an earlier incarnation of the job. Newest-first ordering matters; taking any older entry gives the wrong state.
- **Finished PRs (generalisation), all `pending`->`success` agreeing:**

  | PR | status | build | Jenkins | started (UTC) | duration | cause | params |
  |---|---|---|---|---|---|---|---|
  | 519 | success "This commit looks good" | #4 | `SUCCESS`, building=false | 2026-10-25T21:38:03Z | 1,379,582 ms (~23 min) | Branch indexing | 4 (`IMAGE_TO_BUILD=image-production`, `RunFrontEndBuild=true`, `GUI_BRANCH=release_1.0`, `BRANCH_TO_BUILD=main`) |
  | 525 | success | #6 | `SUCCESS` | 2026-10-29T14:31:25Z | 1,492,892 ms (~25 min) | Started by user (manual) | same 4 |
  | 530 | success | #1 | `SUCCESS` | 2026-10-28T14:32:24Z | 2,052,986 ms (~34 min) | Branch indexing | none recorded |

  `lastBuild == lastCompletedBuild == status build` for all three. PR 519: `lastSuccessfulBuild #4`, `lastFailedBuild #1`; PR 525: successful #6, failed #5; PR 530: failed `null`. No mismatches reported for any of them.
- **`--wait`:** on finished PR 519 it returned after 1 poll, exit 0, 4.5 s wall (almost all `gh` + TLS latency). On running PR 518 with `--timeout 6 --interval 2` it returned `finished: false`, 2 polls, exit 2 (~12 s: each poll is ~2 `gh` calls plus 2-3 Jenkins calls).
- **Display URL redirect** (GET, not followed): `…/PR-518/1/display/redirect` -> 302 `/blue/organizations/jenkins/CI%2FProductA%2FOfficial%20builds%20and%20PR%20checks/detail/PR-518/1/`; without the build number -> 302 to the multibranch Blue Ocean page, **losing the PR**. So the redirect is not a reliable way to get the classic URL; deriving it textually is.
- **SHA check pitfall:** the build's `BuildData.lastBuiltRevision.SHA1` is the **merge commit** of the PR head into the target (PR 518 build #1: `ed0e82958f`, a "Merge 10d65e85… into 794f8f29…" commit), never the head sha. GitHub's `merge_commit_sha` matches it only while the target branch has not moved (PR 519: built `490fe75dcc`, GitHub's current `381c094c82`). Correct check: built sha is the head, or `merge_commit_sha`, or a commit whose parents include the head (one `gh api commits/<sha>`). A naive `built == head` check gave false mismatches on 519, 530 and 518.
- **Secrets / TLS:** `REQUESTS_CA_BUNDLE` in the environment overrides `session.verify = False` in `requests`; `verify=False` must be passed on each request.

## Conclusion

Hypothesis **holds**. From PR number alone: PR -> head sha -> commit **status** (not check-runs) `continuous-integration/jenkins/pr-head` -> `target_url` -> job URL (always derivable) and build URL (only when the URL has a build number) -> `api/json`. This yields building flag, result, number, timestamp, duration, queue state, parameters, and last/last-completed build, and GitHub state maps cleanly: building/`null` result <-> `pending`, `SUCCESS` <-> `success` (observed); `FAILURE` <-> `error` ("This commit cannot be built"; combined state `failure`) was observed later on PR-526 build 3 (see [failed-build-check.md](./failed-build-check.md)); `ABORTED` is **not observed**. That check also showed that Jenkins reports on the commit it built, which can differ from the latest PR head.

Implications for a loop implementation:

- "Waiting for CI" has two phases: *queued* (no build number in the status, job `inQueue`/`queueItem.why`) and *building*. A poll must re-read the GitHub status each time, because the build number appears only later.
- Treat the status that the newest `/statuses` entry reports as authoritative; do not use the combined `/status` endpoint's cached view without checking the build number against `lastBuild`.
- Do not compare built SHAs with the head; use the merge-parent check, or skip it.
- The job URL is the stable key; the build URL is optional.

## Caveats

- Only `SUCCESS` and running/queued states were observed; `FAILURE`/`UNSTABLE`/`ABORTED` mapping (`EXPECTED_GITHUB_STATES`) is from knowledge of the Branch Source plugin, not tested here.
- PR 518's queued state came from a real in-flight run (a new build blocked behind #1); its eventual outcome was not awaited, so `--wait` was only tested on a finished build (519) and on a timeout (518).
- Build parameters are only present for runs started with the parameter dialog (519 #4 via indexing also recorded defaults; 530 #1 recorded none), so `parameters == {}` does not mean "no parameters defined".
- `merge_commit_sha` can be `null` on unmergeable PRs; the script then only accepts the head or a merge of it.
- Statuses are read with `per_page=100`; the sha has few, so pagination was not exercised.
- Credentials are loaded from a git-ignored file and never printed; raw outputs sit in the git-ignored `tmp/`.
