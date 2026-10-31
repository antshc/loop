# [Research] Jenkins API for loop CI handling (PR check state, build trigger, log download, log search)

## Bottom line

- **Use the Jenkins API directly, with the GitHub commit status as the entry point.** A PR number gives the Jenkins job address, and the job address gives state, parameters and build history. This needs no plugin and no CSRF token on this server. Build the CI wait step on it.
- **Start builds with one parameterized trigger call and treat the result as "queued".** It reproduced the Blue Ocean dialog exactly and started build #2 with the right values. The job runs one build at a time, so a queued build can wait a long time. Poll the queue item without a short limit and never retry the trigger.
- **Download whole logs with the streaming download. Follow running builds by offset.** Compression makes a 31 MB log cost about 2 MB and 2 seconds, and memory stays low. Prefer it to the python-jenkins console call, which needs about 3.5 times the log size in memory.
- **Search logs on the client in three steps, cheapest first.** There is no server-side log search on this Jenkins. (1) Read the last 64 KB through Blue Ocean and grow by four times on no match. (2) Find a failed step from step status and error message, without reading logs. This works only when a step carries the failed result. In the failed build checked, no stage or step did, so read the log end instead. (3) Download the whole log once for a line in the middle.
- **Do not build on Timestamper, forward early-stop reading, or per-stage log downloads for search.** They cost the same as, or more than, a full download. Their savings are lost to network buffers or duplicated output.
- **A failed build shows in GitHub as state `error`, and the combined state is `failure`.** A later check of a failed PR build confirmed this. The latest PR head can have no Jenkins status, because Jenkins reports on the commit it built. Treat both `error` and `failure` as "CI failed", and read the status on the built commit. See [the failed build check](./failed-build-check.md).
- **Two gaps remain.** The aborted status mapping was never observed because no aborted PR build exists. External log stores (for example Elasticsearch) could not be checked because the plugin list is closed to this user. **Not confirmed**

Reasoning: logs compress about 15 to 1, so a full download is cheap and is the safe fallback. Most questions in a CI loop ("did it pass", "why did it fail") sit at the end of the log or in step errors. Those cost 2 to 8 KB. Only a search in the middle of a big log needs the full download.

## Prototypes

| Folder | Purpose |
|---|---|
| `pr-check-state/` | Read the Jenkins state of a PR from its GitHub commit status |
| `trigger-build/` | Start a PR build with the dialog parameters |
| `download-logs/` | Save a build log whole, by offset, or as a tail |
| `log-search/` | Compare client-side strategies to find lines in a log |

Settings go in `jenkins.json.user` in this folder (`jenkinsUrl`, `jenkinsUser`, `jenkinsApiToken`). Git ignores it. Do not commit a token.

### Babysit integration follow-up

A later read-only check of the selected PR-518 job found builds #1 and #2 both
successful. A guarded trigger `--dry-run` read live parameter definitions and
constructed the four-parameter payload without a POST. A bounded progressive
console read also succeeded. These observations validate reads and payload
construction, not failed-build recovery or current write permission.

The builds expose a PR SCM revision's `pullHash` as well as multiple generic
`lastBuiltRevision.SHA1` values from different checkouts. Match the PR revision;
do not select the first checkout SHA or infer identity from the job name alone.
The demonstrated branch-based payload does not pin an immutable head. Recheck
the current head before submission and reconcile the actual built revision.

Verified TLS failed for this internal server's self-signed certificate. The
follow-up used an explicitly authorized insecure TLS mode for that server only.
Production clients should verify certificates by default, accept a configured
trusted CA, and require explicit opt-in for insecure mode.

The prototypes are reference experiments, not production imports. Several
credential defaults still point to the former `docs/prototypes/jenkins-api`
location: set `JENKINS_JSON` explicitly for the trigger, and
`JENKINS_CREDENTIALS` for log-search tooling. Inspect each tool's options before
running it; a default log-search run writes a baseline, and trigger dry-run
still performs a live GET and prints definitions/payload values.

## What we did

- **PR check state:** Read four PRs through the GitHub commit status and Jenkins, read-only. State, result, parameters and queue phase were all found. **Confirmed**
- **Build trigger:** Sent one real trigger to the PR-518 job with the four dialog values. The build waited for build #1, then ran as build #2 with the same values. **Confirmed**
- **Log download:** Downloaded a finished 31 MB log and followed a running build to its end. Checksums matched after line endings were aligned. **Confirmed**
- **Failed build check:** Read a failed PR build (PR-526 build 3) in Jenkins and GitHub, read-only. Jenkins `FAILURE` shows as GitHub `error`, and no stage is marked failed. **Confirmed** for failed builds. The aborted case was not found. **Not confirmed**
- **Log search:** Measured six client strategies against a full download on two builds, and probed for server-side search. No server-side search exists. **Confirmed.** Failed-build cases used two other PRs, because the requested builds passed.

## Remaining work

- Run the state mapping on an aborted PR build. The failed case is done.
- Ask a Jenkins admin whether a log store or a Log Parser plugin is installed, or request read access to the plugin list.
- Change the PR check lookup to read the status on the commit Jenkins built, not on the current PR head.
- Add offset saving to the streaming download so an interrupted follow can resume.

## Next steps

1. Keep Jenkins integration in the software factory, not the installed Loop library. Observe commit statuses, validated job addresses, and queue-then-build phases; `building=false` alone is not proof of success.
2. Use these prototypes as reference for a bounded, authenticated factory adapter, not production imports. Submit once, persist the queue identity, and reconcile uncertain outcomes rather than retrying blindly.
3. Keep the credentials file outside git, as now (`*.user` is ignored).

## References

- [Research: log search without downloading](./jenkins-log-search-without-downloading.md)
- [Failed build check (PR-526 build 3)](./failed-build-check.md)
- [Experiment: PR check state](./jenkins-pr-check-state.md)
- [Experiment: trigger build](./jenkins-trigger-build.md)
- [Experiment: download logs](./jenkins-download-logs.md)
- [Experiment: log search](./jenkins-log-search.md)
- [Code: PR check state](./pr-check-state/pr_check_state.py)
- [Code: trigger build](./trigger-build/trigger_build.py)
- [Code: download logs](./download-logs/download_logs.py)
- [Code: verify downloaded logs](./download-logs/verify.py)
- [Code: log search](./log-search/log_search.py)
