# Jenkins: search / inspect build logs without downloading the whole log

Date: 2026-10-31. Method: primary sources only (jenkins.io, plugins.jenkins.io, `github.com/jenkinsci/*` source, python-jenkins docs/source on PyPI/RTD, GitHub REST docs) plus read-only (GET/HEAD) probing of the live server.
Every claim carries a URL. Claims with no primary source are marked **[assumption]** or **[unknown]**. Live observations are kept in their own section and are labelled **[observed]**. Source links point at `master`/`latest`, so line content can drift; the named symbol is the stable handle.

Target server (from the question): `https://jenkins.example.com`, PR job `…/job/CI/job/ProductA/job/Official%20builds%20and%20PR%20checks/job/PR-518/`.

## 0. Bottom line

- Jenkins core has **no server-side log search/grep and no HTTP `Range` support**. The only partial-read primitive in core is the `start=<byte offset>` parameter of `logText/progressiveText`, which means "from this offset to the current end".
- Cheapest "tail without download" on this server: `HEAD …/logText/progressiveText?start=0` returns `X-Text-Size` (the raw log size) with no body. Then `GET …/logText/progressiveText?start=<size-N>`.
- Blue Ocean's `…/runs/N/log/` returns only the **last 150 KB by default** and accepts `thresholdInKB`, so it needs no size probe. Blue Ocean is deprecated, but it is installed and working here.
- To narrow the search, use Pipeline structure first (`wfapi/describe`, Blue Ocean `nodes/` and `steps/`) to find the failing stage or step, then read only that step's log.
- For real "search without downloading", log storage must move off the Jenkins controller: OpenTelemetry plugin → Elasticsearch or Loki, or the Logstash plugin. Search then happens in that backend's own API. Not checked: whether any of these is installed here (the plugin list needs admin rights).

## 1. Core endpoints

| Endpoint | What it does | Source |
|---|---|---|
| `<build>/consoleText` | Streams the whole log as plain text (`IOUtils.copy` from `getLogInputStream()` through `PlainTextConsoleOutputStream`). No offset parameter, no Range handling in the handler. | [Run.java `doConsoleText`/`doConsoleTextImpl`](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Run.java) |
| `<build>/consoleFull` | HTML console page (UI view). | [Run.java](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Run.java) (view only); behaviour seen in §7 |
| `<build>/logText/progressiveText?start=N` | Incremental plain text from byte offset `N`. Returns headers `X-Text-Size` and `X-More-Data`. | [Stapler `LargeText.doProgressText`](https://github.com/jenkinsci/stapler/blob/master/core/src/main/java/org/kohsuke/stapler/framework/io/LargeText.java); [Run.getLogText → AnnotatedLargeText](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Run.java) |
| `<build>/logText/progressiveHtml?start=N` | Same, HTML-annotated. | [Run.java `getLogText`](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Run.java) |

### progressiveText semantics (from `LargeText.doProgressTextImpl`)

Source: [LargeText.java](https://github.com/jenkinsci/stapler/blob/master/core/src/main/java/org/kohsuke/stapler/framework/io/LargeText.java).

- `start` defaults to 0 and is a **byte offset into the raw log file**.
- If `start > file length`, the code resets `start = 0` ("text rolled over") and sends the **full log**. Do not pass a guessed huge offset.
- Response header `X-Text-Size` is the value to use as the next `start`. In the default code path it equals the file length.
- `X-More-Data: true` is added only while the log is not complete (`!completed`). A finished build has no `X-More-Data` header.
- If the log file does not exist yet, it returns `X-Text-Size: 0` and `X-More-Data: true`.
- There is **no `end`/length parameter**, so the response always runs to the current end of the log. A client can close the connection early to stop reading.
- Newer Stapler has an `Accept: multipart/form-data` streaming mode that also accepts a negative `start` (tail). Negative `start` in the plain mode is not handled there. Same file, `doProgressTextStreaming`. This does **not** work on this server (§7.3).
- Java servlet container `HEAD` handling is not documented in the sources read. See §7.3 for what this server does.

### HTTP Range

- `doConsoleTextImpl` has no Range logic (see above), and I found no Range handling in `LargeText.doProgressTextImpl`. **[assumption]** Range is unsupported in core. **[observed]** The server ignored `Range` (§7.3).

### Official docs on these endpoints

- Remote access API page lists "Obtaining progressive console output" among the API capabilities but does not document header semantics: <https://www.jenkins.io/doc/book/using/remote-access-api/>. The header semantics above come from the Stapler source.

## 2. Pipeline-specific endpoints

### 2.1 Pipeline Stage View REST API (`wfapi`)

Source: [pipeline-stage-view-plugin `rest-api/README.md`](https://github.com/jenkinsci/pipeline-stage-view-plugin/blob/master/rest-api/README.md); plugin page <https://plugins.jenkins.io/pipeline-stage-view/>.

- `GET /job/<job>/wfapi/runs`: run history with stages.
- `GET /job/<job>/<run>/wfapi/describe`: run with `stages[]` (id, name, status, timings).
- `GET /job/<job>/<run>/execution/node/<id>/wfapi/describe`: stage detail with `stageFlowNodes[]` (steps) and `_links.log`.
- `GET /job/<job>/<run>/wfapi/pendingInputActions`: pending `input` steps (id, message, `proceedUrl`, `abortUrl`).
- `GET /job/<job>/<run>/execution/node/<id>/wfapi/log`: JSON `{nodeId, nodeStatus, length, hasMore, text, consoleUrl}`.
- **Truncation:** `FlowNodeLogExt` returns at most `MAX_RETURN_CHARS` = 10 KiB (system property `…FlowNodeLogExt.maxReturnChars`, default `10 * 1024`), and `text` is HTML. It writes the **last** `length` chars via `logText.writeHtmlTo(logLen - length, …)`. So `wfapi/log` is a "tail of a step", with `hasMore` meaning "older text was cut". Source: [FlowNodeLogExt.java](https://github.com/jenkinsci/pipeline-stage-view-plugin/blob/master/rest-api/src/main/java/com/cloudbees/workflow/rest/external/FlowNodeLogExt.java).
- There is **no run-level** `wfapi/log` (a 404 here, §7.4). The README lists only the node-level one.
- `/job/<job>/<run>/execution/node/<id>/log` is the HTML console URL for one node. It is the `consoleUrl` link in the JSON above, per the same README.

### 2.2 Blue Ocean REST

Source: [blueocean-plugin `blueocean-rest/README.md`](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-rest/README.md). Status: plugin page marks Blue Ocean **deprecated**: <https://plugins.jenkins.io/blueocean/>.

- Paths: `/blue/rest/organizations/jenkins/pipelines/<…>/runs/<N>/{nodes/,steps/,log/}`. A multibranch branch/PR is addressed as `…/pipelines/<folder>/pipelines/<multibranch>/branches/<PR-n>/runs/<N>/` (shape confirmed in §7.5).
- Log API, quoted from the README ("Fetching logs"): *"By default only last 150 KB log data is returned … fetch full log by sending start=0 … override … using thresholdInKB"*; `X-Text-Size` is *"the byte offset of the raw log file client should use in the next request as value of start"*; `X-More-Data: true` means repeat later.
- Implementation: [`LogResource.java`](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-rest-impl/src/main/java/io/jenkins/blueocean/service/embedded/rest/LogResource.java). `DEFAULT_LOG_THRESHOLD = 150` KB. If `start` is absent and the log is bigger than the threshold, `offset = length - threshold`. Adds `X-Text-Size`, `X-Text-Delivered`, and `X-More-Data`. `?download=true` adds `Content-Disposition`. A negative `start` ends in an exception → HTTP 500 "Failed to get logText" (seen in §7.3; the code does `writeLogTo(offset, …)` with no guard).
- Per-step log: `…/runs/<N>/nodes/<nodeId>/steps/<stepId>/log/` (README "Get log for a Pipeline step"). **[observed]** `…/runs/<N>/steps/<id>/log/` also works here.
- A multibranch project itself has no log; a run is needed (README note under "Get log for a Pipeline run").
- Blue Ocean's `/log/` body is plain text, so a client can do `?thresholdInKB=<small>` for cheap tail, no `HEAD` needed.

## 3. Plugins that give log search or filtering

None of the plugins below adds a "grep this build log by regex and return matches" HTTP endpoint that I could confirm from primary sources.

| Plugin | What it does | API for search? |
|---|---|---|
| [Log Parser](https://plugins.jenkins.io/log-parser/) ([README](https://raw.githubusercontent.com/jenkinsci/log-parser-plugin/master/README.md)) | Post-build rule-based parse; categorises lines into ERROR/WARNING/INFO/DEBUG, shows summaries and links. Build-page URL `parsed_console` (`LogParserAction.getUrlName`: [source](https://github.com/jenkinsci/log-parser-plugin/blob/master/src/main/java/hudson/plugins/logparser/LogParserAction.java)). | A Stapler `doDynamic` page and graph handlers exist (same file). **[unknown]** whether it is JSON-exposed. Only works for builds where a parse step ran. |
| [Timestamper](https://plugins.jenkins.io/timestamper/) ([README](https://raw.githubusercontent.com/jenkinsci/timestamper-plugin/master/README.adoc)) | Adds per-line timestamps. Scripting endpoint `<build>/timestamps/?time=…&elapsed=…&appendLog&currentTime`. | The query parser also accepts **`startLine` and `endLine`** ([TimestampsActionQuery.java](https://github.com/jenkinsci/timestamper-plugin/blob/master/src/main/java/hudson/plugins/timestamper/action/TimestampsActionQuery.java)), giving a **line-range** read. Not a content search. Timestamps allow time-based offset estimation. |
| [Logstash](https://plugins.jenkins.io/logstash/) ([README](https://raw.githubusercontent.com/jenkinsci/logstash-plugin/master/README.md)) | Ships console log to Elasticsearch/Logstash/Redis/RabbitMQ/Syslog; `logstashSend` publishes the log tail (`maxLines`); wrapper step sends each line. | Search happens in the target indexer, e.g. [Elasticsearch `_search`](https://www.elastic.co/guide/en/elasticsearch/reference/current/search-search.html). No Jenkins-side search API. |
| [OpenTelemetry](https://plugins.jenkins.io/opentelemetry/) ([build-logs doc](https://github.com/jenkinsci/opentelemetry-plugin/blob/master/docs/build-logs.md)) | Version 2.0.0+: sends Pipeline build logs over OTLP to Elastic (8.1+) or Loki. Options: view in Kibana/Grafana only, or mirror and still show in Jenkins. With Elastic and Jenkins visualisation it reads logs back from Elasticsearch (needs a read-only role/user). | Search is done in the backend: Kibana, or [Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/). Best match for the goal. The "Loki only" mode removes logs from Jenkins screens. |
| [Pipeline CloudWatch Logs](https://plugins.jenkins.io/pipeline-cloudwatch-logs/) | Ships Pipeline logs to AWS CloudWatch. | Backend search (not verified further here). **[unknown]** feature details; page only confirmed to exist. |
| "Pipeline Log Highlighter" | Named in the question. | **[unknown]**: I found no repo for it under `jenkinsci` and did not verify it. |

## 4. python-jenkins

Sources: [API docs](https://python-jenkins.readthedocs.io/en/latest/api.html) and the 1.8.3 wheel source (`jenkins/__init__.py`, <https://pypi.org/project/python-jenkins/>).

| Method | Behaviour |
|---|---|
| `get_build_console_output(name, number)` | `GET <folder-url>job/<short>/<n>/consoleText` and returns the **whole** text as `str`. No offset. Constant `BUILD_CONSOLE_OUTPUT`. |
| `get_build_info(name, number, depth=0)` | `…/<n>/api/json?depth=<d>`. Includes `actions[]` (parameters, causes), `building`, `result`. |
| `get_job_info(name, depth=0, fetch_all_builds=False)` | `…/api/json?depth=<d>`. Includes `property[ParametersDefinitionProperty]`, `builds`, `nextBuildNumber`. |
| `build_job(name, parameters=None, token=None)` | POSTs `build_job_url(...)`. `build_job_url` picks `…/buildWithParameters` **only if `parameters` is truthy**, otherwise `…/build`. Reads the `Location` header (`…/queue/item/<id>/`) and returns the integer queue id. Raises `EmptyResponseException` if no `Location`. Docs say the queue id is valid only ~5 minutes after completion. |
| `get_queue_item(number, depth=0)` | `queue/item/<n>/api/json`. Has `why` while waiting; has `executable` (number/URL) once started or finished. |
| `get_build_stages(name, number)` | `…/<n>/wfapi/describe/` (constant `BUILD_STAGES`). |
| Crumb | `maybe_add_crumb` GETs `crumbIssuer/api/json` once. `404`/empty → `self.crumb = False` and **no crumb header** is sent; otherwise the crumb header is attached. |
| Folders | `_get_job_folder` splits a name on `/` → `job/<a>/job/<b>/…`. So `"CI/ProductA/Official builds and PR checks/PR-518"` works as a name. URL-encoding of the spaces is done by `_get_encoded_params`, which I did not read. **[assumption]** it encodes them. |
| Progressive log | **None.** The module source contains no `progressive`/`logText` reference (grep of 1.8.3). Use `Jenkins.jenkins_request(requests.Request('GET', url), stream=True)` (public method; returns the `requests` response, so headers are readable). **[assumption]** this also works for `HEAD`; not tested. |

Gotcha: `build_job(name)` or `build_job(name, {})` on a parameterized job goes to `/build`, not `/buildWithParameters`. See §5.3.

## 5. Triggering, parameters and "Input required"

### 5.1 Parameters vs `input` step: two different things

- `parameters { … }` (Declarative) defines job-level parameters (`ParametersDefinitionProperty`). Supported types incl. `string`, `text`, `booleanParam`, `choice`, `password`: <https://www.jenkins.io/doc/book/pipeline/syntax/#parameters>. **[observed]** PR-518 carries this property (§7.2).
- The `input` step pauses a **running** build mid-pipeline and may request parameters from the submitter: <https://www.jenkins.io/doc/pipeline/steps/pipeline-input-step/>. Pending ones are listed by `wfapi/pendingInputActions` (README above) and Blue Ocean `steps/` entries have an `input` field ("Get Pipeline Steps with Input" in the Blue Ocean README). **[observed]** none pending on PR-518 #1.

### 5.2 The Blue Ocean "Input required" dialog

- It is **not** an `input` step. It is `ParametersRunButton`, shown when the user presses Run on a job that has parameter definitions. The header text is the i18n key `parameterized.pipeline.header=Input required`: [ParametersRunButton.jsx](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-core-js/src/js/parameter/ParametersRunButton.jsx), [Messages.properties](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-web/src/main/resources/jenkins/plugins/blueocean/web/Messages.properties).
- It builds the form from the job's parameter definitions (one widget per type: Choice, Boolean, String, Text, Password: [components](https://github.com/jenkinsci/blueocean-plugin/tree/master/blueocean-core-js/src/js/parameter/components)), pre-filled with each `defaultParameterValue` ([ParameterService.js](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-core-js/src/js/parameter/services/ParameterService.js) `parametersToSubmitArray`).
- On submit it POSTs JSON `{"parameters":[{"name":…,"value":…}, …]}` to `…/runs/` ([ParameterApi.js](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-core-js/src/js/parameter/rest/ParameterApi.js) `startRunWithParameters`; README "Start a parameterized build").
- Server side, `RunContainerImpl.create` rejects a parameterized job with no `parameters` body (`400 "This is parameterized job, requires parameters"`), converts the list to `ParametersAction`, and schedules via `Queue.schedule2`: [RunContainerImpl.java](https://github.com/jenkinsci/blueocean-plugin/blob/master/blueocean-rest-impl/src/main/java/io/jenkins/blueocean/service/embedded/rest/RunContainerImpl.java). This is the same queue entry point as core's `buildWithParameters`.
- Mapping for PR-518: dialog fields = the four definitions in §7.2 → `IMAGE_TO_BUILD` (choice), `RunFrontEndBuild` (boolean), `GUI_BRANCH` (string), `BRANCH_TO_BUILD` (string; the "branch" field). Core equivalent: `POST <job>/buildWithParameters` with `--data-urlencode IMAGE_TO_BUILD=… --data-urlencode RunFrontEndBuild=true --data-urlencode GUI_BRANCH=… --data-urlencode BRANCH_TO_BUILD=…`.

### 5.3 Core trigger endpoints

Source: [ParameterizedJobMixIn.java](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/jenkins/model/ParameterizedJobMixIn.java), [ParametersDefinitionProperty.java](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/ParametersDefinitionProperty.java), docs <https://www.jenkins.io/doc/book/using/remote-access-api/>.

- Non-parameterized: `POST <job>/build` (docs: "perform an HTTP POST on JENKINS_URL/job/JOBNAME/build"; also for Multibranch/Organization folders, where it triggers a **scan**, not a build).
- Parameterized: `POST <job>/buildWithParameters` with form/query parameters (docs example: `curl JENKINS_URL/job/JOB_NAME/buildWithParameters --user USER:TOKEN --data id=123 --data verbosity=high`).
- `doBuild` on a parameterized job: **not POST** → renders the parameter form and schedules nothing; **POST** → `_doBuild`, which reads a submitted **form** (`req.getSubmittedForm()`, JSON field `parameter`). A bare `POST …/build?delay=0` therefore does **not** look like a valid trigger for a parameterized job. **[assumption]** it errors (400-style) because no form JSON is submitted; not tested (no POSTs allowed). `delay` is a `TimeDuration` query parameter (default = quiet period).
- Parameters omitted from `buildWithParameters` fall back to the definition's default when one exists: `ParameterDefinition.createValue(req)` "returns a default value if possible, or null" ([ParameterDefinition.java](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/ParameterDefinition.java)); null values are skipped in `buildWithParameters`.
- Response: `201 Created` with `Location: <jenkins>/queue/item/<id>/` when a new queue item is created; `303 See Other` to the existing item when an equivalent item is already queued (`!scheduleResult.isCreated()`). Both are in `ParametersDefinitionProperty.buildWithParameters` (same sources).
- Queue → build: `GET /queue/item/<id>/api/json`: `why` while waiting; once started `executable.number` and `executable.url` (`Queue.LeftItem.getExecutable` is `@Exported`: [Queue.java](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Queue.java); python-jenkins docs above).

### 5.4 Auth and CSRF

- Requests authenticated with **user + API token are exempt from CSRF protection**; crumbs from `/crumbIssuer/api/json` are for session/password-based calls: <https://www.jenkins.io/doc/book/security/csrf-protection/>. The remote access page says the same ("API tokens are preferred instead of crumbs").
- python-jenkins handles both (see §4, `maybe_add_crumb`).

## 6. Multibranch PR URL structure, and deriving it from a GitHub check

- PR jobs are named `PR-<number>` (also `PR-<n>-merge` / `PR-<n>-head` with some strategies): [PullRequestSCMHead.java](https://github.com/jenkinsci/github-branch-source-plugin/blob/master/src/main/java/org/jenkinsci/plugins/github_branch_source/PullRequestSCMHead.java).
- URL: `<jenkins>/job/<folder>/job/<folder>/job/<multibranch>/job/PR-<n>/[<build>/]`, each segment URL-encoded. Here: `…/job/CI/job/ProductA/job/Official%20builds%20and%20PR%20checks/job/PR-518/`.
- Jenkins status context written by the GitHub Branch Source plugin: `continuous-integration/jenkins/pr-head` (head), `…/pr-merge` (merge), `…/branch`: [GitHubNotificationContext.java `getDefaultContext`](https://github.com/jenkinsci/github-branch-source-plugin/blob/master/src/main/java/org/jenkinsci/plugins/github_branch_source/GitHubNotificationContext.java).
- The `target_url` points at the Display URL API redirect `…/<build>/display/redirect[?…]`: [DisplayURLProviderTest.java](https://github.com/jenkinsci/display-url-api-plugin/blob/master/src/test/java/org/jenkinsci/plugins/displayurlapi/DisplayURLProviderTest.java) shows that URL shape; plugin: <https://plugins.jenkins.io/display-url-api/>.
- Commit status API: [GitHub docs](https://docs.github.com/en/rest/commits/statuses): `GET /repos/{owner}/{repo}/commits/{ref}/status` returns the combined status with `statuses[].context/state/target_url`. Check-runs API: <https://docs.github.com/en/rest/checks/runs> (`GET …/commits/{ref}/check-runs`).
- Derivation recipe:
  1. `sha=$(gh api repos/example-org/example-repo/pulls/518 --jq .head.sha)`
  2. `gh api repos/example-org/example-repo/commits/$sha/status --jq '.statuses[]|select(.context|startswith("continuous-integration/jenkins/"))|.target_url'`
  3. Strip the query and a trailing `/display/redirect`. What remains is `<jenkins>/job/…/PR-518/<build>/`. The last path segment is the build number; the parent is the job URL.
  4. Then use `<build>/logText/progressiveText`, `<build>/wfapi/describe`, etc.

## 7. Live server observations [observed]

All probes were GET/HEAD only, run on 2026-10-31 with HTTP Basic auth loaded from the credentials file inside the probe script. No builds were triggered, no POSTs, no config changes. Credentials are not reproduced here.

### 7.1 Server and plugins

- `GET /api/json?tree=version` → 200, header `X-Jenkins: 2.452.2`.
- `GET /api/json?tree=mode,useCrumbs,numExecutors` → `mode: EXCLUSIVE`, **`useCrumbs: false`**, `numExecutors: 1`.
- `GET /crumbIssuer/api/json` → **404** (CSRF crumb issuer is not enabled).
- `GET /pluginManager/api/json…`, `/pluginManager/installed`, `/manage/pluginManager/api/json` → **403** for this user. **The installed-plugin list could not be read.** Presence was inferred from working endpoints:
  - Pipeline Stage View REST (`/wfapi/…`): present (200s below).
  - Blue Ocean REST (`/blue/rest/organizations/jenkins/`): present (200). Response classes `io.jenkins.blueocean.rest.impl.pipeline.*`.
  - Timestamper (`<build>/timestamps/`): present (200 text/plain).
  - Log Parser: `<build>/parsed_console/` → 404 on the probed build. **Inconclusive** (the page exists only for builds where a parse step ran).
  - Logstash, OpenTelemetry, CloudWatch: **not probed / unknown**.
- Build `actions[]` include `org.jenkinsci.plugins.workflow.job.views.FlowGraphAction`, `…pipeline.modeldefinition.actions.RestartDeclarativePipelineAction`, `…displayurlapi.actions.RunDisplayAction`, `…libs.LibrariesAction`, `…cps.EnvActionImpl`, `jenkins.branch.BranchIndexingCause`, `jenkins.metrics.impl.TimeInQueueAction`, `hudson.plugins.git.util.BuildData`, `com.axis.system.jenkins.plugins.downstream.yabv.BuildFlowAction`.

### 7.2 PR-518 job

- Job class `org.jenkinsci.plugins.workflow.job.WorkflowJob`; properties: `BranchJobProperty`, `ParametersDefinitionProperty`, `BuildDiscarderProperty`, `DisableConcurrentBuildsJobProperty`. The parent folder class is `WorkflowMultiBranchProject`.
- Builds when probed: `#1` (in progress, started by `BranchIndexingCause`). It was still running during the probes, so sizes below grew between calls.
- Parameter definitions (name / type / default):
  - `IMAGE_TO_BUILD` / `ChoiceParameterDefinition` / `image-production`; choices: `image-production, image-aws, image-aws-local, image-aws-mock, image-azure, image-azure-local, image-azure-mock, image-all`.
  - `RunFrontEndBuild` / `BooleanParameterDefinition` / `true`.
  - `GUI_BRANCH` / `StringParameterDefinition` / `release_1.0`.
  - `BRANCH_TO_BUILD` / `StringParameterDefinition` / `main`.
- Other PR jobs' completed builds (PR-519 #4, PR-525 #5/#6) carry a `ParametersAction` with exactly those four names; the choice value is recorded as a `StringParameterValue`. PR-525 #6 shows a `UserIdCause` ("Started by user …") alongside parameters, i.e. a manual run with the dialog values. PR-530 #1 (indexing) shows no `ParametersAction` in its actions.
- `DisableConcurrentBuildsJobProperty` is set, so a new trigger while a run is active will wait in the queue. (Property presence observed; queueing consequence follows from the property's name, not tested.)
- `/wfapi/pendingInputActions` → `[]`; `/wfapi/nextPendingInputAction` → `null`.
- `GET /queue/api/json` works and shows unrelated blocked items (e.g. `datapath_driver_multibranch`: "At maximum indexing capacity").

### 7.3 Log endpoints, PR-518 #1 and PR-519 #4

- `GET …/logText/progressiveText?start=0` → 200, `Content-Type: text/plain;charset=utf-8`, `X-Text-Size: <n>`, `X-More-Data: true` (running build), gzip + chunked (no `Content-Length`).
- **`X-Text-Size` is larger than the returned body.** E.g. header 177387 vs 155592 body bytes. Cause not confirmed from source; likely stripped console annotations. **[assumption]** Use `X-Text-Size` only as an offset, never as a body length.
- `GET …?start=<X-Text-Size>` → 200, **empty body**, same `X-Text-Size`, `X-More-Data: true` (nothing new yet).
- `GET …?start=99999999` (beyond end) → 200 and the **whole log from byte 0** (matches the `start > length` reset).
- `GET …?start=<size-3000>` → 200, ~3019 chars (a mid-line first fragment; drop the first partial line).
- `GET …?start=-5000` → **500 HTML error page** (negative start not supported).
- `GET …?start=-5000` with `Accept: multipart/form-data` → **500** (streaming mode absent in 2.452.2).
- **`HEAD …/logText/progressiveText?start=0` → 200 with `X-Text-Size` (and `X-More-Data` while running), no body.** This is the cheap size probe. Also works through the Blue Ocean `…/log/?start=0` (200 with `X-Text-Size`).
- `HEAD …/consoleText` → 200 with no size header. `GET …/consoleText` → 200 gzip + chunked.
- **`Range: bytes=0-99` on `…/consoleText` → 200, full body** (no 206, no `Content-Range`, no `Accept-Ranges`) on the running build (also with `Accept-Encoding: identity`) and on finished PR-519 #4 (a full 30,886,896-byte body was returned).
- `GET …/progressiveHtml?start=0` → 200 `text/html` with `X-Text-Size`/`X-More-Data`.
- `GET …/consoleFull` → 200 `text/html` with a small `Content-Length` (≈6.8 KB gzip) on the running build, i.e. a page shell, not the log. **[assumption]** a finished build's page behaves the same.
- Closing a streamed `GET …/consoleText` after the first 2000 bytes worked (client-side early stop).
- Size of a finished log: PR-519 #4 `X-Text-Size: 30965742` (~31 MB), a real example of why a full download is wasteful.
- `<build>/timestamps/?time=HH:mm:ss&appendLog&startLine=100&endLine=104` → 200, 5 lines returned on PR-519 #4. The lines carried no timestamp prefix there, so timestamps may be absent for that region. Whether `endLine` is inclusive is not verified.
- Log text lines carry an inline ISO prefix like `[2026-10-31T13:57:19.830Z]`, usable for time-based offset estimation.

### 7.4 Pipeline-specific endpoints

- `…/1/wfapi/describe` → 200 JSON: `status: IN_PROGRESS`, `stages[]` of `{id, name, status, …}` (ids 16, 33, 43, 49, 51, 54, 56, 72).
- `…/PR-518/wfapi/runs` → 200. `…/1/wfapi/allDescribe` → 404. `…/wfapi/changesets`, `…/wfapi/artifacts` → 200 `[]`.
- `…/1/wfapi/log` (run level) → **404**.
- `…/execution/node/72/wfapi/describe` → 200 with `stageFlowNodes[]`; each has `_links.log` and `_links.console`.
- `…/execution/node/80/wfapi/log` → 200 JSON `length: 10240, hasMore: true`, `text` is HTML with `<span class="timestamp">…` markup (matches the 10 KiB tail rule). Nodes with no output: `length: 0`.
- `…/execution/node/79/log` → 200 HTML. `…/execution/node/72/log` → **404** (stage node without `LogAction`).
- Blue Ocean `…/branches/PR-518/runs/1/` → 200 JSON; response header `X-Blueocean-Refresher`.
- Blue Ocean `…/runs/1/nodes/` → 200 (fields `id, displayName, result, state, durationInMillis, edges, firstParent, input, restartable, causeOfBlockage…`); `…/runs/1/steps/` → 200.
- Blue Ocean `…/runs/1/log/` (no `start`) → X-Text-Size 289851, **X-Text-Delivered 153600 (= 150 KB)**, `X-More-Data: true`.
- Blue Ocean `…/log/?thresholdInKB=4` → `X-Text-Delivered 4096`. `…/log/?start=0` → full log (`X-Text-Delivered` = full size).
- Blue Ocean `…/log/?start=-2000` → **500** `{"message":"Failed to get logText: Negative seek offset"}`.
- Blue Ocean `…/steps/<id>/log/?start=0` → 200 with `X-Text-Size` (0 for steps without output, 2229 for step 24); `HEAD` works on it too. `…/nodes/<id>/log/` → 200 (2645 bytes for stage node 16).

### 7.5 URL / GitHub derivation (PR-518)

- `gh api repos/example-org/example-repo/pulls/518` → state `open`, head sha present.
- `gh api repos/example-org/example-repo/commits/<head sha>/status` → combined `state: pending`; one status, context **`continuous-integration/jenkins/pr-head`**, `target_url`: `https://jenkins.example.com/job/CI/job/ProductA/job/Official%20builds%20and%20PR%20checks/job/PR-518/1/display/redirect`.
- `gh api …/commits/<sha>/check-runs` → only GitHub Actions / CodeQL / `repository-config` entries; **no Jenkins check-run**. So for this repo Jenkins reports through commit **status**, not check-runs.
- Blue Ocean path form seen: `/blue/rest/organizations/jenkins/pipelines/CI/pipelines/ProductA/pipelines/Official%20builds%20and%20PR%20checks/branches/PR-518/runs/1/`.

## 8. Recommended approach

### 8.1 Choosing the method

1. **Narrow by structure first.** `GET <build>/wfapi/describe` → find stages with a non-SUCCESS `status`. Then use `…/execution/node/<id>/wfapi/describe` (steps) or Blue Ocean `…/runs/N/nodes/` and `…/steps/` (they expose `result`/`state` per step). Read the step's log (`…/steps/<id>/log/`, small) instead of the whole build log. Note `wfapi/log` is capped at the last 10 KiB (§2.1), so it is only good for the failure tail of a step.
2. **Tail the whole log:** `HEAD progressiveText?start=0` → `X-Text-Size = S`, then `GET progressiveText?start=max(0, S-N)`. Alternatively Blue Ocean `…/log/?thresholdInKB=N` (one request, but Blue Ocean is deprecated).
3. **Grep older content without storing it:** stream `GET progressiveText?start=<a>` and close the connection when the match or the byte budget is reached (the server has no end parameter, §1). For timestamped logs, `/timestamps/?…&startLine=&endLine=` gives line windows (Timestamper, if present).
4. **For durable server-side search:** ship logs to Elasticsearch or Loki (OpenTelemetry plugin, or Logstash) and query the backend API. Requires an administrator to install and configure. Do not expect this to be present: the plugin list could not be read here.

### 8.2 progressiveText tail/grep strategy

1. `size = HEAD(start=0)['X-Text-Size']` (bytes). One cheap call.
2. Tail window: `start = max(0, size - N)` with N = 64–256 KiB. The first line is usually partial (offsets are byte offsets): drop everything up to the first `\n`. A start that lands inside a multi-byte character or a console annotation may produce garbage at the start; **[unknown]** exactly how annotations behave at an arbitrary offset. Dropping the first partial line avoids it.
3. Regex search in the tail. If not found and you want more history, step back: `start = max(0, start - N)`, but fetch it as a bounded read (stream, stop at the previous `start`) so you do not re-download the tail. Repeat until a match or `start == 0`. Never pass `start > size` (it returns the whole log).
4. For running builds, poll: keep `next = X-Text-Size` from each response and request `start=next` again while `X-More-Data` is `true`. Sleep between polls. An empty body is normal. When `X-More-Data` is absent, the build is done.
5. Always send `Accept-Encoding: gzip` (default in `requests`): log text compresses well (observed gzip + chunked).
6. With python-jenkins: use `Jenkins.jenkins_request(requests.Request('GET', f"{base}/job/…/{n}/logText/progressiveText?start={off}"), stream=True)`; read `resp.headers['X-Text-Size']` and iterate `resp.iter_lines()`. Avoid `get_build_console_output` for large builds (full `str` in memory: ~31 MB for PR-519 #4).

### 8.3 Triggering PR-518 (when needed; not run during this research)

- Use `POST <job-url>/buildWithParameters` with the four names from §7.2 (override only what you need; omitted ones take defaults). A bare `/build` is not the right endpoint for a parameterized job (§5.3). With python-jenkins, `build_job(name, {"BRANCH_TO_BUILD": "main", …})`; passing an empty dict hits `/build`.
- Auth: Basic user + API token. On this server `crumbIssuer` is 404, so no crumb is needed. python-jenkins tolerates that.
- Read `Location` (`…/queue/item/<id>/`) from the 201 (or 303), poll `queue/item/<id>/api/json` until `executable.number` appears, then use that number in the build URL. Expect waiting in the queue if a run is already active (`DisableConcurrentBuildsJobProperty` is set).

## 9. Unknowns / not verified

- Installed plugin list (needs admin; 403). Log Parser, Logstash, OpenTelemetry, CloudWatch installation unknown. Log Parser output not checked on a parsed build.
- "Pipeline Log Highlighter": no primary source found.
- Whether `HEAD` on `progressiveText` is guaranteed behaviour or only an artefact of the servlet container: the live server returns `X-Text-Size`, but no doc states it.
- Exact reason `X-Text-Size` > returned text length (annotations?), and how a mid-annotation `start` behaves.
- Whether `POST /build` with no form on a parameterized job returns 400/500 (source-derived; not tested because POSTs were out of scope).
- python-jenkins URL-encoding of spaces in folder names (`_get_encoded_params` not read); `jenkins_request` with `HEAD` untested.
- Whether Jenkins posts a GitHub **check run** for this repo under some configuration; only a **commit status** was observed.
- Whether `endLine` in Timestamper is inclusive (5 lines returned for 100–104).
