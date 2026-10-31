# Experiment: trigger a PR-518 Jenkins build through the API

Research under test: [jenkins-log-search-without-downloading.md](./jenkins-log-search-without-downloading.md) §5, §8.3. Run 2026-10-31 against `https://jenkins.example.com` (self-signed TLS). Code: [trigger_build.py](./trigger-build/trigger_build.py) (disposable).

## Bottom line

- **One API call starts a PR build with the same values as the Blue Ocean dialog.** The call needs no CSRF token on this server. Use it as the only trigger path, with parameter definitions read from the live job.
- **A successful trigger means "queued", not "started".** The job allows one build at a time, so the new build waited until build #1 finished. It then started as build #2, with all four dialog values and the correct user cause (checked after the experiment ended, read-only). Treat a blocked queue item as a normal state and poll without a short time limit.
- **The GitHub status reacts at once.** It moves to "scheduled to be built" and loses the build number until the build starts. Do not expect a build address straight after the trigger.
- **Each trigger creates a real build on shared CI.** One call is cheap to make and costly to repeat. Send one trigger per request and never retry blindly.

## Question

Does `POST <job>/buildWithParameters` with the four parameters from the Blue Ocean "Input required" dialog (`IMAGE_TO_BUILD=image-production`, `RunFrontEndBuild=true`, `GUI_BRANCH=release_1.0`, `BRANCH_TO_BUILD=main`) start a new build of the `PR-518` job, and how do the queue item, the build and the GitHub status reflect it? Out of scope: Blue Ocean's own `POST .../runs/` endpoint, retries, waiting for a full build.

## Setup

- Job: `https://jenkins.example.com/job/CI/job/ProductA/job/Official%20builds%20and%20PR%20checks/job/PR-518/`, derived from the PR's `continuous-integration/jenkins/pr-head` status `target_url` by stripping `/1/display/redirect`.
- Auth: Basic user + API token from a git-ignored JSON file (`$JENKINS_JSON`). No crumb (`/crumbIssuer` is 404). Token never printed or stored in outputs.
- Python 3.12 venv with `requests` 2.34.2 and `python-jenkins` 1.8.3.
- Authorized: exactly one real trigger.

## Method

1. `GET <job>/api/json?tree=property[parameterDefinitions[name,type,defaultParameterValue[value],choices]]` and build the payload from the live definitions; overrides come from `--param`.
2. `--dry-run`: print derived job URL, definitions and the payload only.
3. Capture the "before" state: `gh api repos/example-org/example-repo/commits/<head sha>/status` and the job's builds.
4. One real run (`trigger_build.py <check target_url> --param ...`): POST urlencoded, expect `201` + `Location: .../queue/item/<id>/`, poll the queue item for `executable.number` (cap 180 s), then poll the build.
5. Read-only afterwards: queue item parameters/causes, job builds, GitHub status "after".
6. python-jenkins: only inspect `build_job_url(...)` (no request sent).

## Observed result

**Live definitions** match the dialog exactly: `IMAGE_TO_BUILD` (Choice, default `image-production`, 8 choices), `RunFrontEndBuild` (Boolean, default `true`), `GUI_BRANCH` (String, default `release_1.0`), `BRANCH_TO_BUILD` (String, default `main`). Dialog values equal the defaults.

**Dry run** printed the job URL (spaces as `%20`) and payload `{"IMAGE_TO_BUILD": "image-production", "RunFrontEndBuild": "true", "GUI_BRANCH": "release_1.0", "BRANCH_TO_BUILD": "main"}`; nothing posted.

**Trigger:** `201 Created`, `Location: https://jenkins.example.com/queue/item/9042632/`.

**Queue item (polled every 3 s for 180 s, 53 polls):** stayed `hudson.model.Queue$BlockedItem`, `blocked: true`, `why: "Build #1 is already in progress (ETA: N/A)"`, no `executable`, never cancelled. Build #1 (the indexing-started build, running since before the trigger; ~22 min old at the last check, stage `Checkout` done) was still `building: true` ~5 minutes later, so the new build **did not start within the experiment**. The job reported `inQueue: true`. `wfapi/pendingInputActions` for #1 was `[]`, so #1 is genuinely running, not waiting for input. This is the `DisableConcurrentBuildsJobProperty` effect predicted in the research.

**Recorded parameters** (read from the queue item's `actions`, since no build exists yet): `ParametersAction` with `IMAGE_TO_BUILD` = `image-production` (`StringParameterValue`), `RunFrontEndBuild` = `true` (`BooleanParameterValue`, JSON boolean), `GUI_BRANCH` = `release_1.0`, `BRANCH_TO_BUILD` = `main`; `CauseAction` = `UserIdCause` "Started by user Jane Doe". Build #1 (indexing) carries no `ParametersAction`, only `BranchIndexingCause`. The build's own `actions[parameters]` could not be read because no build #2 exists yet.

**GitHub status** (`continuous-integration/jenkins/pr-head`, combined state `pending` both times):

| | `description` | `target_url` | `updated_at` |
|---|---|---|---|
| before | `This commit is being built` | `.../PR-518/1/display/redirect` | 13:53:10Z |
| after (~1 min post-trigger) | `This commit is scheduled to be built` | `.../PR-518/display/redirect` (no build number) | 14:09:40Z |

So a queued manual build immediately overwrites the status for the same context and drops the build number from the link. Its link will presumably gain `/2/` once started (not observed).

**python-jenkins URL building** (no request sent): `build_job_url("CI/ProductA/Official builds and PR checks/PR-518", {"BRANCH_TO_BUILD": "main"})` → `.../job/CI/job/ProductA/job/Official%20builds%20and%20PR%20checks/job/PR-518/buildWithParameters?BRANCH_TO_BUILD=main`. Spaces in folder names are percent-encoded and `/` splits into `job/` segments. Values are form-encoded (`"x y"` → `x+y`, `a&b` → `a%26b`). With `{}` or `None` the URL ends in `/build`, as the research noted. A Python bool becomes `True` (capital); whether Jenkins accepts that for a Boolean parameter was **not tested**, so pass `"true"`/`"false"` strings.

## Conclusion

**Follow-up (read-only check later the same day): fully confirmed.** After build #1 finished, the queued item started as PR-518 #2 (`building=true`). Its recorded parameters were `IMAGE_TO_BUILD=image-production`, `RunFrontEndBuild=True`, `GUI_BRANCH=release_1.0`, `BRANCH_TO_BUILD=main`, with cause "Started by user Jane Doe". The queue item then returned 404, as expected once it has started.

Original result at the end of the run: partly confirmed. A `buildWithParameters` POST built from the live definitions was accepted (`201`, queue item with the dialog's four parameter values and a user cause), and the GitHub status reacted at once. The build **start** and the build's recorded parameters were **not observed**, because build #1 was still running and concurrent builds are disabled for the job. Implication for tooling: treat a successful trigger as "queued", poll the queue item without a short cap (or report `why`), and handle `BlockedItem` as a normal state, not a failure.

## Caveats

- A later GET-only follow-up found builds #1 and #2 successful and completed a
  guarded live-definition dry-run without submitting a build. It did not test
  failed-build recovery or renewed write permission. Multiple SCM actions were
  present; use the PR revision's `pullHash`, not an arbitrary checkout SHA.
- Set `JENKINS_JSON` explicitly: the disposable script's default still names the
  former prototype directory. Its dry-run reads the network and prints live
  defaults/payload values. Review output sensitivity before capturing it.
- The script disables TLS verification. The follow-up first failed with normal
  certificate verification and proceeded only after explicit authorization for
  insecure TLS on the selected internal server. Prefer a trusted CA in production.
- One trigger only, by instruction; no second run to see the build start. The queued item (id 9042632) is left in the queue and will start after #1 finishes; cancelling it (`POST /queue/cancelItem?id=9042632`) was not done.
- Dialog values equal the defaults, so this does not show that overrides of non-default values work.
- The `303` path (equivalent item already queued) was not exercised.
- `ruff` was not available in the environment, so formatting was kept to ~100 columns by hand.
- `requests` ignores `session.verify=False` when `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` is set; the script passes `verify=False` per request.
