# Experiment: finding error lines in a Jenkins build log without downloading all of it

Builds on [jenkins-log-search-without-downloading.md](./jenkins-log-search-without-downloading.md) (research, in the main checkout). Run 2026-10-31, Jenkins 2.452.2, read-only (GET/HEAD).

## Bottom line

- **This Jenkins has no server-side log search.** Log endpoints ignore query text, and the search endpoints cover only job names. The plugin list is not readable for this user, so external log stores are **Not confirmed**. Search on the client.
- **The final result and the last error of a build are found with 2 to 4 KB.** That is about 0.2 % of the compressed log, in under 2 seconds. Read the last 64 KB through Blue Ocean, then grow the window by four times if nothing matches.
- **The failed step is found without reading any log.** Blue Ocean steps give the failing step and its error message in about 8 KB. Pick failed steps, not failed stages, because failure spreads to later stages.
- **A line in the middle of a big log is not cheaper than a full download.** The growing-window method matches the full download at about 50 % depth. Download the whole log once for such searches.
- **Do not use Timestamper or forward early-stop reading.** They cost more than the full download or hide their cost in network buffers.

## Question

Can we find error lines or the failed stage in a Jenkins build log while transferring far fewer bytes than the full log, and is any server-side log search available? Hypothesis: yes, on the client side, with `HEAD`+`progressiveText` tail, Blue Ocean `thresholdInKB`, or per-step logs.

## Setup

- Server `https://jenkins.example.com` (self-signed TLS, Basic auth from a git-ignored JSON file; no crumb needed). No build was triggered.
- Builds (job `CI/ProductA/Official builds and PR checks`):

| Build | Result | Raw log | Lines | Gzip body of `consoleText` |
|---|---|---|---|---|
| PR-519 #4 | SUCCESS | 30.9 MB | 108,089 | 1,941 KB |
| PR-518 #1 | SUCCESS (it finished since the research note) | 26.8 MB | 92,110 | 1,893 KB |
| PR-520 #13 (added) | FAILURE | 26.7 MB | 92,222 | 1,873 KB |
| PR-519 #1 (added) | FAILURE | 2.0 MB | 9,712 | 171 KB |

  Neither requested build failed, so two failed builds of the same job were added to test the failed-stage path.
- Code: [`docs/prototypes/jenkins-api/log-search/log_search.py`](./log-search/log_search.py) (`requests` only, disposable).

## Method

`python log_search.py BUILD_URL --pattern REGEX [--strategy ...]`. The pattern is case-insensitive and applied per line. Per strategy it reports requests, **body KB** (bytes read off the connection after TLS and HTTP framing: gzip-compressed, headers excluded), **rx KB** (body KB plus bytes the server had already pushed into the kernel socket buffer but the client never read when it stopped early, measured with `FIONREAD` 0.3 s after the stop), **text KB** (decoded) and wall seconds.

| Strategy | What it does |
|---|---|
| `baseline` | `GET consoleText` in full to `tmp/`, grep locally |
| `stage` | `wfapi/describe` → failed stages → `execution/node/<id>/wfapi/describe` → failed steps → `wfapi/log` (10 KiB tail) plus the step's `error.message` |
| `stage-bo` | Blue Ocean `nodes/` → `nodes/<id>/steps/` → failed steps → `steps/<id>/log/?thresholdInKB=16` |
| `tail` | `HEAD progressiveText` for `X-Text-Size`, then `GET ?start=size-N`, N = 64 KB, ×4 until a match |
| `blueocean` | `GET runs/N/log/?thresholdInKB=N`, N = 64 KB, ×4 until a match (no size probe) |
| `timestamps` | `<build>/timestamps/?appendLog&startLine=&endLine=`: doubling then bisection to find the line count, then backwards windows of 500, 1000, 2000 … lines |
| `chunked` | Stream `progressiveText?start=0`, close the connection at the first matching line |
| `probe` | Status and query-honoring of candidate server-side search endpoints |

## Observed result

### Final verdict lines (`error|fail|exception|FAILURE|Finished:`; the answer is the last matching line, `Finished: <RESULT>`)

| Build | Strategy | Reqs | Body KB | rx KB | Sec | Found |
|---|---|---|---|---|---|---|
| PR-519 #4 | baseline | 1 | 1941.1 | 1941.1 | 2.8 | yes (8,395 matches) |
| | `tail` 64 KB | 2 | 3.3 | 3.3 | 1.7 | yes, `Finished: SUCCESS` |
| | `blueocean` 64 KB | 1 | 3.3 | 3.3 | 0.35 | yes |
| | `timestamps` | 25 | 78.1 | 78.1 | 31.8 | yes |
| | `chunked` first match | 1 | 32.0 | 504.4 | 1.4 | yes, but L446 (`0 Error(s)`), not the verdict |
| | `stage` / `stage-bo` | 1 | 7.4 / 1.5 | same | 0.4 | n/a: no failed stage |
| PR-518 #1 | baseline | 1 | 1892.8 | 1892.8 | 2.6 | yes |
| | `tail` / `blueocean` | 2 / 1 | 3.4 / 3.4 | 3.4 | 1.7 / 0.36 | yes |
| | `timestamps` | 25 | 78.9 | 78.9 | 29.2 | yes |
| | `chunked` first match | 1 | 32.0 | 421.7 | 1.5 | yes, L426 (`0 Error(s)`) |

### Failed builds, pattern `script returned exit code` (appears once, near the end)

| Build | Strategy | Reqs | Body KB | Sec | Found |
|---|---|---|---|---|---|
| PR-520 #13 | baseline | 1 | 1873.2 | 2.0 | L92218 |
| | `stage` | 6 | 45.9 | 2.8 (once 27) | yes, via the step `error.message`; not in the 10 KiB step log |
| | `stage-bo` | 6 | 7.9 | 2.3 | yes (2 hits) |
| | `tail` | 2 | 2.2 | 1.7 | yes |
| | `blueocean` | 1 | 2.2 | 0.35 | yes |
| | `timestamps` | 25 | 36.1 | 28.7 | yes |
| | `chunked` | 1 | 1872.7 | 2.0 | yes, but it read the whole log (match near the end) |
| PR-519 #1 | baseline / `tail` / `blueocean` | 1 / 2 / 1 | 170.7 / 3.7 / 3.7 | 0.9 / 1.2 / 0.35 | yes |
| | `stage` / `stage-bo` | 7 / 7 | 43.7 / 7.7 | 2.6 / 2.6 | yes (`stage` only via `error.message`) |

Failed-stage location works: `wfapi/describe` flagged **14** stages `FAILED` (failure propagates to every later stage), but the stage describes show only 2 real failing steps (`Shell Script ./run_docker_unit_tests…`, `Shell Script ./run_testing_framework_local_sanity_test…`) plus a follow-on `Publish MSTest` step. `error.message` (`script returned exit code 1`, `hudson.AbortException`) is available without reading any log. Blue Ocean `nodes/` found the same steps with 4 failed nodes and ~6× fewer bytes (JSON is not gzipped on wfapi; Blue Ocean's is).

### Needle in the middle (a unique `Passed …VpgIamRolesExistsValidatorTests…` line at L57056 and an xUnit line at L97284; tails grown ×4 up to 64 MB)

| Needle | Strategy | Reqs | Body KB | Sec | Note |
|---|---|---|---|---|---|
| L57056 (53%) | baseline | 1 | 1941 | 2.2 | 5 matches |
| | `tail` / `blueocean` | 6 / 5 | 1091.7 / 1091.7 | 4.7 / 3.7 | window had to reach 16 MB |
| | `chunked` | 1 | 320 (rx 1557) | 1.7 | stopped at the *first* of 5 matches (L10866) |
| | `timestamps` | 31 | 14,831 | 41.5 | uncompressed, 7× the full download |
| L97284 (90%) | `tail` / `blueocean` | 5 / 4 | 298.5 / 298.5 | 4.6 / 2.0 | window 4 MB |
| | `chunked` | 1 | 1824 | 2.1 | match first occurs at 28 MB of 30 MB: no saving |
| | `timestamps` | 29 | 2,953 | 40.4 | |

### Server-side search (`probe`, run on PR-519 #4)

| Endpoint | Status | Query text honored? |
|---|---|---|
| `<build>/parsed_console/`, `parsed_console/api/json`, `logParser/`, `log-parser/` | 404 | n/a (Log Parser output not present; plugin not shown to be installed) |
| `<build>/consoleText?q=…`, `…/log/?q=…`, `progressiveText`/node log with `q,query,search,grep,filter,pattern,text` | 200 | **no**: body unchanged for a no-match token |
| `/search/?q=`, `/search/index?q=` | 404 | n/a |
| `/search/suggest?query=` | 200 JSON | yes, **item names only** (`PR-519` → job names) |
| `/blue/rest/search/?q=type:pipeline;…;pipeline:<name>` | 200 JSON | yes, **pipeline names only** (no log text) |
| `/blue/rest/organizations/jenkins/pipelines/?q=` | 200 | no (ignored; 573 KB either way) |
| `<build>/wfapi/log` (run level) | 404 | n/a |
| `/pluginManager/api/json` | 403 | n/a (plugin list unreadable) |

Page HTML names only `blueocean-rest-impl` under `/plugin/…`; no Log Parser, Pipeline Log Highlighter (a UI script, no REST), OpenTelemetry/Logstash marker was found. That is weak evidence of absence, not proof.

### Other observations

- **Gzip changes the economics.** `consoleText` and `progressiveText` are gzip-compressed on the wire at ~16:1, so the "wasteful" 31 MB log costs 1.9 MB and 2–3 s. Tail strategies cut that ~600× (3 KB), but the baseline is not catastrophic.
- **Early close does not stop the server pushing.** Closing `chunked` after 32–64 KB of body left 420–620 KB already in the kernel buffer (rx ≈ 10–16× body); a stop at 131 KB of body left 1.4 MB buffered. Early stop saves less than app-level counting suggests.
- **Timestamper is not gzipped** and its line numbers equal `consoleText` line numbers (1-based, `endLine` inclusive; the line count matched the baseline exactly: 108,089). `time=yyyy-MM-dd HH:mm:ss` or `elapsed=` add a prefix, so time-based bisection over line numbers is possible. Cost: ≈1–1.5 s per request (each seems to scan from the log start), 18–24 probes just to find the end (20–30 s). `startLine=99999999` (beyond the end) took **130 s** in a manual probe: never send a huge line number.
- Blue Ocean `thresholdInKB` and the `HEAD` size probe behaved as in the research note; `tail` costs one extra request (≈0.5–1.3 s) over `blueocean`.
- Wall times vary with server load (`stage` on PR-520 #13 took 2.8, 14.6 and 27 s for identical requests; the byte counts did not change).

## Conclusion

- **Hypothesis: confirmed for the common case**: the final verdict and a failure's trailing error are found with 2–4 KB (≈0.1–0.2 % of the 1.9 MB gzip body) in 0.35 s (`blueocean`) to 1.7 s (`tail`).
- **No server-side log search is available** on this server: no Log Parser pages, `q`/`grep`-style parameters are ignored everywhere, `/search` and Blue Ocean search cover item and pipeline names only. Plugin list is 403, so OpenTelemetry/Logstash backends are unverified.
- **Recommended client-side strategy** (cheapest first):
  1. `GET <blue run>/log/?thresholdInKB=64` (one request); grep for the pattern; if no match, grow ×4 (64 KB → 256 KB → 1 MB → 4 MB). Fall back to `HEAD`+`progressiveText?start=size-N` if Blue Ocean is not installed.
  2. To locate *which* step failed without reading logs, use Blue Ocean `nodes/` → `nodes/<id>/steps/` (≈8 KB for 6 requests) or `wfapi` stage describes, and read `error.message`; fetch that step's log (`steps/<id>/log/?thresholdInKB=`) only if more context is needed. `wfapi` marks all downstream stages `FAILED`: pick failed *steps*, not stages.
  3. Mid-log needles: windowed tail growth beats the full download only when the match is in the last ~4 MB of 30 MB (298 KB vs 1.9 MB); for a needle at ~50 % it matches the full download (1.1 MB over 5 requests) and is slower. Then download the full log once.
- **Not worth it:** `timestamps` (uncompressed, 20–40 s, can exceed the full download) and forward `chunked` early stop (only wins when the first match is early; the kernel buffer hides 10×+ of the savings; first match ≠ relevant match).

## Caveats

- Neither requested build failed; failed-step results come from PR-520 #13 and PR-519 #1 (same job, same pipeline shape). One server, one job family, one run per cell; times are noisy.
- "Found" means ≥1 matching line in the fetched window, not that it is the *relevant* error. `tail`/`blueocean` report the last match, `chunked` the first.
- `rx` is a lower bound on true wire bytes after an early stop (more can arrive after the 0.3 s sample until the connection resets) and excludes TLS and HTTP header bytes; `body KB` excludes them as well.
- `progressiveText` offsets are raw-file bytes while the returned text is annotation-stripped (`X-Text-Size` > body); windows are therefore approximate and the first line of a mid-file window is dropped.
- `wfapi` step logs are capped at 10 KiB (HTML) and `stage` first matched only via `error.message` for the exit-code pattern.
- Not tested: running builds under load beyond PR-518's earlier run, pipelines with `parallel` failure branches, logs with ≫100 MB, other servers.
