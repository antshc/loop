# Experiment: download Jenkins build logs from a PR job URL

Run 2026-10-31 against `https://jenkins.example.com` (Jenkins 2.452.2). Builds on [jenkins-log-search-without-downloading.md](./jenkins-log-search-without-downloading.md). Code: [`docs/prototypes/jenkins-api/download-logs/`](./download-logs/) (`download_logs.py`, `verify.py`). Disposable.

## Bottom line

- **Logs download reliably from a build address, with low and fixed memory.** A 31 MB log took about 2 seconds and about 2 MB on the wire, because Jenkins compresses logs about 15 to 1. Use the whole-log download for finished builds.
- **Following a running build works and loses no data.** The followed copy matched the final log after line endings were aligned. Use the offset-following mode for builds that still run.
- **Reading only the end of a log is cheap.** About 3 KB on the wire gave the last 25 KB of text. Use it for quick checks of the result.
- **The python-jenkins console call needs about 3.5 times the log size in memory.** It is fine for small logs. Use the streaming download for logs of tens of MB.
- **Per-stage logs are slow and duplicate parallel output.** Fetch the log of one needed step only. Saving the offset to resume after an interruption is **Not confirmed** and is not built.

## Question

Can we reliably download and save Jenkins build logs from a build URL, and which download method should be used when: `consoleText` streamed (`full`), `logText/progressiveText` followed with `X-Text-Size` / `X-More-Data` (`stream`), or only the last N bytes (`tail`)? How does that compare with python-jenkins `get_build_console_output`?

## Setup

- Targets (read-only, no build triggered): PR-519 #4 (finished, SUCCESS, 31 MB log) and PR-518 #1 (**running when the experiment started**, finished during it, SUCCESS, 27.5 MB log) under `CI/ProductA/Official builds and PR checks`.
- Python 3.12 venv with `requests` + `python-jenkins`. Basic auth from the credentials JSON; no crumb needed. Nothing secret is written to output, metadata files or docs.
- Output goes to the git-ignored `tmp/jenkins-logs/<folder__job>/<build>/`: `console-full.log`, `console-stream.log`, `console-tail.log`, `build.json`, optional `stages/`.

## Method

`download_logs.py BUILD_URL [--out DIR] [--mode full|stream|tail] [--tail-bytes N] [--stages] [--lf] [--no-gzip] [--poll-interval S] [--max-seconds S]`

- `full`: `GET …/consoleText` with `stream=True`, 64 KiB chunks straight to the file; sha256 computed while writing.
- `stream`: loop `GET …/logText/progressiveText?start=<offset>`; next offset = `X-Text-Size`; stop when `X-More-Data` is not `true`; sleep between polls; file flushed after every poll.
- `tail`: `HEAD …?start=0` → `X-Text-Size`; `GET …?start=size-N`; drop the first (partial) line.
- Wire bytes are counted by reading the raw (compressed) stream with `decode_content=False` and inflating by hand (`resp.raw.tell()` returned 0 — unusable).
- `build.json`: number, displayName, result, building, duration, timestamp, url, parameters, causes, plus per-mode download stats. Metadata comes from `api/json?tree=…`.
- `--stages`: Blue Ocean `…/branches/PR-n/runs/N/nodes/` → `nodes/<id>/steps/` → each step's `log/?start=0`; one file per stage.
- `verify.py`: sha256 comparison, suffix check, offset mapping, peak RSS (`ru_maxrss`) per method in separate processes.
- Spaces / `%20`: the URL path is unquoted and re-quoted, so literal spaces and `%20` both work (tested both). Retries: `urllib3.Retry` (5×, backoff, 429/500/502/503/504, GET/HEAD); tested against a local server returning 503 twice → 3 hits, success.

## Observed result

### Finished build, PR-519 #4

| Method | Requests | Body bytes | On the wire | File | Time | sha256 (first 16) |
|---|---|---|---|---|---|---|
| `full` (gzip) | 1 | 30,886,896 | 1,987,682 (6.4 %) | 30,886,896 | 1.8 s | `bef69a6f25528eef` |
| `full --no-gzip` | 1 | 30,886,896 | 30,886,896 | 30,886,896 | 3.1 s | `bef69a6f25528eef` |
| `stream` (raw) | 1 | 30,994,979 | 1,987,170 | 30,994,979 | 2.0 s | `d0164c1c917b0f51` |
| `stream --lf` | 1 | 30,994,979 | 1,987,170 | 30,886,890 | 1.7 s | `38b9167f4ba79e87` |
| `tail` 64 KiB | 2 (HEAD+GET) | 25,117 | 3,383 | 25,015 | 2.1 s | – |

- `X-Text-Size` = 30,965,742 (raw log file size), `X-More-Data` absent on the finished build.
- **`stream` ≠ `full` byte-for-byte:** `progressiveText` emits `\r\n` for every line (108,089 lines → +108,089 bytes − 6 stray `\r` removed vs `consoleText`). After normalising, **`stream --lf` sha256 `38b9167f…cb8e5` == `consoleText` with `\r` stripped** (identical hash). The raw `consoleText` has 6 lone `\r` that `progressiveText` drops.
- Tail is a byte-exact suffix of the LF stream file (checked).
- Gzip: the server compresses both endpoints (`Content-Encoding: gzip`, chunked, no `Content-Length`). Log text compresses ~15:1 (31 MB → 2.0 MB); without gzip the download was ~1.3 s slower.
- Tail: `start` is a raw-file offset but `X-Text-Size` counts annotation bytes the text does not contain, so a 65,536-byte raw window returned only 25,117 text bytes (annotation density at the end of this log). Do not read `tail-bytes` as text bytes.
- Raw offset → text position (`verify.py offsets`): `start=1,000,000` lands at text position 966,099; `10 M` → 9,965,943; `20 M` → 19,965,915; `30 M` → 29,961,938. The raw file is ~34–38 KB larger than the text before that point and 78,846 bytes larger overall.
- Total raw-to-text overhead is at most ~0.3 %.

### Running build, PR-518 #1

- `stream --lf --poll-interval 5 --max-seconds 300` ran for 286.7 s in **54 requests**, following the build until it finished (`X-More-Data` went absent → loop ended on its own), 27,546,081 body bytes, 1,953,498 on the wire, file 27,453,971 bytes, final `X-Text-Size` 27,532,827.
- Afterwards `full` downloaded 27,453,977 bytes (1.7 s, 1,938,247 on the wire). **sha256 of `full` with `\r` stripped == sha256 of the followed `stream --lf` file** (`c17caea71183e31815e0c5d5ca9669cedd55ecf586479744c9bbc03426e2a669`). So following a live log with `X-Text-Size` offsets lost and duplicated nothing across 54 polls.
- `build.json` for #1: `result SUCCESS`, `durationMs 1,483,091`, `parameters {}` (branch-indexing run, no `ParametersAction`). PR-519 #4 has the four parameters (`IMAGE_TO_BUILD`, `RunFrontEndBuild`, `GUI_BRANCH`, `BRANCH_TO_BUILD`).

### python-jenkins `get_build_console_output` vs streaming (PR-519 #4, separate processes)

| Method | Peak RSS | Delta over idle (28 MiB) | Time |
|---|---|---|---|
| python-jenkins `get_build_console_output` | 137 MiB | +109 MiB (≈3.5× the log) | 2.7 s |
| `full` (this script) | 37 MiB | +9 MiB | 2.0 s |
| `stream` (this script) | 37 MiB | +9 MiB | 2.1 s |

python-jenkins returns one `str` (30,881,762 chars), uses `/consoleText`, and works with the self-signed cert after `server._session.verify = False` and unsetting `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`.

### Per-stage logs (`--stages`, Blue Ocean), PR-519 #4

- 24 nodes, 10 with non-empty logs, **74 s** wall time (one request per node plus one per step, sequential), 60 MB written for a 31 MB build log.
- Parent stages contain their children's output: `Unit / Sanity Tests` 30,677,096 bytes = `Unit Tests` 28,981,414 + `Framework AWS sanity tests` 1,697,118 (±header lines); `Frontend and Build` = `Full Build` = 307,471 ≈ `Build` 307,426. Skipped stages (`NOT_BUILT`) have no log.
- Step hrefs from Blue Ocean contain raw spaces and must be re-quoted.

## Conclusion

Hypothesis **confirmed**: logs can be downloaded and saved reliably from a PR build URL, with fixed memory, on both finished and running builds.

- **Finished build, whole log:** `full` (`consoleText`, gzip on). One request, ~2 s for 31 MB, ~2 MB on the wire, LF line endings, 9 MiB memory.
- **Running build / live follow:** `stream`. It is the only mode that resumes from an offset and detects completion (`X-More-Data`). Use `--lf` to get the same bytes as `consoleText` (minus 6 lone `\r` in this log); without it every line ends in `\r\n`.
- **Quick look at the end:** `tail` (HEAD + one GET). It transfers ~3 KB for the last ~25 KB of text. Treat N as raw-file bytes and drop the partial first line.
- **python-jenkins:** fine for small logs or when already using it for other calls; for logs of tens of MB it needs ~3.5× the log size in memory because the API is whole-string only. Prefer the streaming script for large logs.
- **Per-stage logs:** feasible through Blue Ocean but slow (74 s) and duplicative for parallel parents; use it only for the specific stage needed (the research doc's `steps/<id>/log/` for one step is cheaper).

## Caveats

- Fixed offset-bounded chunks are not possible: `progressiveText` has no end parameter and raw offsets do not equal text offsets (see table above). "Chunking" on a live log works only through the `X-Text-Size` chain. An interrupted `stream` cannot resume from the byte count of the file written; it must persist the last `X-Text-Size`, which this prototype does not do.
- Retries cover connection errors and 429/5xx before the body starts; a failure mid-body is not resumed (a re-run restarts the file).
- Only one running build was followed, for ~5 minutes with 5 s polls; it finished during the run, so "stopped by `--max-seconds`" was not exercised on a live build.
- The `\r`/`\r\n` findings are for this server's output; the 6 lone `\r` are in PR-519 #4 and were not examined further. Timings are from a single run each, on one network path; the first-run order may favour caching.
- `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` in this environment override `session.verify = False`; the script overrides `merge_environment_settings` to force it. Without that every call fails with `CERTIFICATE_VERIFY_FAILED`.
- Not checked: other Jenkins versions, logs with non-UTF-8 bytes, and auth failures (401/403 are not retried and are raised).
