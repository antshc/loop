"""Throwaway experiment: find error lines in a Jenkins build log without downloading all of it.

Usage: python log_search.py BUILD_URL --pattern REGEX [--strategy NAME ...]

Read-only (GET/HEAD). Credentials come from a JSON file (keys jenkinsUser, jenkinsApiToken),
located by $JENKINS_CREDENTIALS or the sibling jenkins.json.user. They are never printed.
Disposable experiment code, not production.
"""

from __future__ import annotations

import argparse
import codecs
import fcntl
import html
import json
import os
import re
import struct
import sys
import termios
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings()  # self-signed TLS on the target server

DEFAULT_CREDENTIALS = Path(__file__).resolve().parent.parent / "jenkins.json.user"
STRATEGIES = (
    "baseline",
    "stage",
    "stage-bo",
    "tail",
    "blueocean",
    "timestamps",
    "chunked",
    "probe",
)
KB = 1024


@dataclass
class Response:
    status: int
    headers: requests.structures.CaseInsensitiveDict
    body: bytes = b""
    wire: int = 0  # body bytes as received (gzip-compressed), no headers
    decoded: int = 0
    stopped_early: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> object:
        return json.loads(self.body)


@dataclass
class Meter:
    requests: int = 0
    wire: int = 0
    rx: int = 0  # body bytes read + bytes already buffered unread in the kernel at close (early stop)
    decoded: int = 0


class Jenkins:
    def __init__(self, credentials: Path) -> None:
        data = json.loads(credentials.read_text())
        self.session = requests.Session()
        self.session.auth = (data["jenkinsUser"], data["jenkinsApiToken"])
        self.meter = Meter()

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        on_chunk: Callable[[bytes], bool] | None = None,
        sink: Path | None = None,
    ) -> Response:
        """Stream a request; `on_chunk` returns True to stop reading and close the connection."""
        resp = self.session.request(
            method,
            url,
            params=params,
            headers=headers,
            stream=True,
            verify=False,
            timeout=600,
        )
        out = Response(resp.status_code, resp.headers)
        inflate = (
            zlib.decompressobj(47)
            if resp.headers.get("Content-Encoding") == "gzip"
            else None
        )
        kept: list[bytes] = []
        sock = resp.raw.connection.sock if resp.raw.connection is not None else None
        handle = sink.open("wb") if sink else None
        try:
            if method != "HEAD":
                stream = resp.raw.stream(
                    64 * KB, decode_content=False
                )  # keep ref: break must not close it
                for raw in stream:
                    out.wire += len(raw)
                    chunk = inflate.decompress(raw) if inflate else raw
                    out.decoded += len(chunk)
                    if handle:
                        handle.write(chunk)
                    if on_chunk is not None:
                        if on_chunk(chunk):
                            out.stopped_early = True
                            break
                    elif not handle:
                        kept.append(chunk)
            self._account(out, sock)
        finally:
            if handle:
                handle.close()
            resp.close()
        out.body = b"".join(kept)
        return out

    def _account(self, out: Response, sock) -> None:
        self.meter.requests += 1
        self.meter.wire += out.wire
        self.meter.decoded += out.decoded
        self.meter.rx += out.wire + (_unread(sock) if out.stopped_early and sock else 0)


def _unread(sock) -> int:
    """Encrypted bytes the server already delivered that the client never read (FIONREAD)."""
    time.sleep(0.3)  # let in-flight segments land
    return struct.unpack(
        "i", fcntl.ioctl(sock.fileno(), termios.FIONREAD, b"\0\0\0\0")
    )[0]


@dataclass
class BuildRef:
    root: str
    jobs: list[
        str
    ]  # URL-encoded job path segments, e.g. ['CI', 'ProductA', 'Official%20...', 'PR-519']
    number: int

    @property
    def url(self) -> str:
        return self.root + "".join(f"/job/{j}" for j in self.jobs) + f"/{self.number}"

    @property
    def blue(self) -> str:
        *folders, multibranch, branch = self.jobs
        pipes = "".join(f"/pipelines/{f}" for f in folders)
        return (
            f"{self.root}/blue/rest/organizations/jenkins{pipes}/pipelines/{multibranch}"
            f"/branches/{branch}/runs/{self.number}"
        )


def parse_build_url(url: str) -> BuildRef:
    parts = urlparse(url)
    match = re.match(r"((?:/job/[^/]+)+)/(\d+)", parts.path)
    if match is None:
        raise ValueError(f"not a build URL: {url}")
    jobs = [seg for seg in match.group(1).split("/job/") if seg]
    return BuildRef(f"{parts.scheme}://{parts.netloc}", jobs, int(match.group(2)))


class LineScanner:
    """Incrementally scans decoded chunks for regex matches, tracking line numbers."""

    def __init__(self, pattern: re.Pattern[str], first_only: bool = False) -> None:
        self.pattern = pattern
        self.first_only = first_only
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.carry = ""
        self.line_no = 0
        self.matches: list[tuple[int, str]] = []

    def feed(self, chunk: bytes) -> bool:
        text = self.carry + self.decoder.decode(chunk)
        *lines, self.carry = text.split("\n")
        for line in lines:
            self._check(line)
        return self.first_only and bool(self.matches)

    def finish(self) -> None:
        self.carry += self.decoder.decode(b"", final=True)
        if self.carry:
            self._check(self.carry)
            self.carry = ""

    def _check(self, line: str) -> None:
        self.line_no += 1
        if self.pattern.search(line):
            self.matches.append((self.line_no, line.strip()[:160]))


def scan_text(
    pattern: re.Pattern[str], text: str, skip_first: bool = False
) -> list[str]:
    lines = text.splitlines()
    if skip_first:
        lines = lines[1:]  # mid-line start
    return [ln.strip()[:160] for ln in lines if pattern.search(ln)]


@dataclass
class Result:
    strategy: str
    requests: int = 0
    wire: int = 0
    rx: int = 0
    decoded: int = 0
    seconds: float = 0.0
    found: bool | None = None
    detail: str = ""
    notes: list[str] = field(default_factory=list)


class Ctx:
    def __init__(
        self, jenkins: Jenkins, build: BuildRef, pattern: re.Pattern[str], args
    ) -> None:
        self.j = jenkins
        self.b = build
        self.pattern = pattern
        self.args = args

    @property
    def log_path(self) -> Path:
        return (
            Path(self.args.baseline_dir)
            / f"{'-'.join(self.b.jobs[-1:])}-{self.b.number}.log"
        )

    def size(self) -> int:
        r = self.j.fetch(
            self.b.url + "/logText/progressiveText", method="HEAD", params={"start": 0}
        )
        return int(r.headers["X-Text-Size"])


# --- strategies -------------------------------------------------------------------------------


def baseline(ctx: Ctx) -> tuple[bool, str]:
    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.j.fetch(ctx.b.url + "/consoleText", sink=ctx.log_path)
    scanner = LineScanner(ctx.pattern)
    with ctx.log_path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            scanner.feed(chunk)
    scanner.finish()
    m = scanner.matches
    if not m:
        return False, f"{scanner.line_no} lines, no match"
    return (
        True,
        f"{scanner.line_no} lines, {len(m)} matches; first L{m[0][0]}, last L{m[-1][0]}: {m[-1][1][:70]}",
    )


def _failed(items: list[dict], ok: tuple[str, ...]) -> list[dict]:
    return [
        i for i in items if str(i.get("status") or i.get("result")).upper() not in ok
    ]


def stage(ctx: Ctx) -> tuple[bool, str]:
    """wfapi: failed stage -> failed step -> last 10 KiB of that step's log."""
    run = ctx.j.fetch(ctx.b.url + "/wfapi/describe").json()
    done = ("SUCCESS", "NOT_EXECUTED", "IN_PROGRESS", "PAUSED_PENDING_INPUT")
    stages = _failed(run["stages"], done)
    if not stages:
        return False, f"run status {run['status']}: no failed stage"
    hits: list[str] = []
    located: list[str] = []
    for st in stages[: ctx.args.max_stages]:
        detail = ctx.j.fetch(
            f"{ctx.b.url}/execution/node/{st['id']}/wfapi/describe"
        ).json()
        for step in _failed(detail["stageFlowNodes"], done):
            located.append(f"{st['name'].strip()}/{step['name'][:40]}#{step['id']}")
            hits += scan_text(ctx.pattern, (step.get("error") or {}).get("message", ""))
            log = ctx.j.fetch(
                f"{ctx.b.url}/execution/node/{step['id']}/wfapi/log"
            ).json()
            text = html.unescape(re.sub(r"<[^>]+>", "", log.get("text", "")))
            hits += scan_text(ctx.pattern, text)
    err = hits[-1][:60] if hits else "-"
    return (
        bool(hits),
        f"{len(stages)} failed stages; steps {located[:3]}; {len(hits)} hits; last: {err}",
    )


def stage_bo(ctx: Ctx) -> tuple[bool, str]:
    """Blue Ocean: failed node -> failed step -> step log tail (thresholdInKB)."""
    nodes = ctx.j.fetch(ctx.b.blue + "/nodes/").json()
    failed = _failed(nodes, ("SUCCESS", "NOT_BUILT", "UNKNOWN", None, "null"))
    failed = [
        n for n in failed if n.get("result") in ("FAILURE", "UNSTABLE", "ABORTED")
    ]
    if not failed:
        return False, "no failed node"
    hits: list[str] = []
    located: list[str] = []
    for node in failed[: ctx.args.max_stages]:
        steps = ctx.j.fetch(f"{ctx.b.blue}/nodes/{node['id']}/steps/").json()
        for step in [s for s in steps if s.get("result") == "FAILURE"]:
            located.append(
                f"{node['displayName'].strip()}/{step['displayName'][:40]}#{step['id']}"
            )
            log = ctx.j.fetch(
                f"{ctx.b.blue}/steps/{step['id']}/log/",
                params={"thresholdInKB": ctx.args.step_kb},
            )
            hits += scan_text(ctx.pattern, log.text)
    err = hits[-1][:60] if hits else "-"
    return bool(
        hits
    ), f"{len(failed)} failed nodes; steps {located[:3]}; {len(hits)} hits; last: {err}"


def _grow(start_kb: int, max_kb: int):
    kb = start_kb
    while True:
        yield min(kb, max_kb)
        if kb >= max_kb:
            return
        kb *= 4


def tail(ctx: Ctx) -> tuple[bool, str]:
    """HEAD for size, then GET progressiveText?start=size-N, growing N x4 until a match."""
    size = ctx.size()
    for kb in _grow(ctx.args.tail_kb, ctx.args.max_kb):
        start = max(0, size - kb * KB)
        r = ctx.j.fetch(ctx.b.url + "/logText/progressiveText", params={"start": start})
        hits = scan_text(ctx.pattern, r.text, skip_first=start > 0)
        if hits:
            return (
                True,
                f"window {kb} KB (size {size // KB} KB); {len(hits)} hits; last: {hits[-1][:70]}",
            )
    return False, f"no match within last {ctx.args.max_kb} KB (size {size // KB} KB)"


def blueocean(ctx: Ctx) -> tuple[bool, str]:
    """Blue Ocean run log with thresholdInKB, growing x4 until a match (no size probe needed)."""
    for kb in _grow(ctx.args.tail_kb, ctx.args.max_kb):
        r = ctx.j.fetch(ctx.b.blue + "/log/", params={"thresholdInKB": kb})
        hits = scan_text(ctx.pattern, r.text, skip_first=True)
        if hits:
            return True, f"threshold {kb} KB; {len(hits)} hits; last: {hits[-1][:70]}"
    return False, f"no match within last {ctx.args.max_kb} KB"


def _ts_lines(ctx: Ctx, first: int, last: int) -> str:
    r = ctx.j.fetch(
        ctx.b.url + "/timestamps/",
        params={"appendLog": "", "startLine": first, "endLine": last},
    )
    return r.text


def timestamps(ctx: Ctx) -> tuple[bool, str]:
    """Timestamper line windows: find the line count by doubling+bisection, then scan backwards."""
    probes = 0

    def exists(line: int) -> bool:
        nonlocal probes
        probes += 1
        return bool(_ts_lines(ctx, line, line))

    high = 1024
    while exists(high):
        high *= 2
    low = high // 2 if high > 1024 else 0
    while high - low > 1:
        mid = (low + high) // 2
        low, high = (mid, high) if exists(mid) else (low, mid)
    total = low
    end, width = total, ctx.args.window_lines
    while end > 0 and total - end < ctx.args.max_lines:
        first = max(1, end - width + 1)
        hits = scan_text(ctx.pattern, _ts_lines(ctx, first, end))
        if hits:
            return (
                True,
                f"{total} lines ({probes} probes); lines {first}-{end}; last: {hits[-1][:70]}",
            )
        end, width = first - 1, width * 2
    return (
        False,
        f"{total} lines ({probes} probes); no match in last {total - end} lines",
    )


def chunked(ctx: Ctx) -> tuple[bool, str]:
    """Stream progressiveText from byte 0, close the connection at the first matching line."""
    scanner = LineScanner(ctx.pattern, first_only=True)
    r = ctx.j.fetch(
        ctx.b.url + "/logText/progressiveText",
        params={"start": 0},
        on_chunk=scanner.feed,
    )
    scanner.finish()
    if scanner.matches:
        line_no, line = scanner.matches[0]
        return (
            True,
            f"first match L{line_no} after {r.decoded // KB} KB decoded: {line[:70]}",
        )
    return False, f"no match; read whole log ({r.decoded // KB} KB)"


PROBE_TOKEN = "ZZNOMATCHZZ"
FILTER_PARAMS = ("q", "query", "search", "grep", "filter", "pattern", "text")
HINT = re.compile(
    r"log|search|highlight|elastic|logstash|opentelemetry|loki|splunk|pars", re.I
)


def probe(ctx: Ctx) -> tuple[bool, str]:
    """Read-only discovery of server-side search/filter endpoints; prints a status table."""
    b, root, blue = ctx.b.url, ctx.b.root, ctx.b.blue
    term = ctx.b.jobs[-1]
    # (name, url with a real term, url with a no-match term or None, body too big to compare)
    endpoints: list[tuple[str, str, str | None, bool]] = [
        ("build parsed_console/ (Log Parser)", f"{b}/parsed_console/", None, False),
        ("build parsed_console/api/json", f"{b}/parsed_console/api/json", None, False),
        ("build logParser/", f"{b}/logParser/", None, False),
        ("build log-parser/", f"{b}/log-parser/", None, False),
        ("build console (HTML view)", f"{b}/console", None, True),
        (
            "build consoleText?q=",
            f"{b}/consoleText?q=Finished",
            f"{b}/consoleText?q={PROBE_TOKEN}",
            True,
        ),
        ("global /search/?q=", f"{root}/search/?q={term}", None, False),
        ("global /search/index?q=", f"{root}/search/index?q={term}", None, False),
        (
            "global /search/suggest?query=",
            f"{root}/search/suggest?query={term}",
            f"{root}/search/suggest?query={PROBE_TOKEN}",
            False,
        ),
        (
            "blueocean /blue/rest/search/?q=pipeline:",
            f"{root}/blue/rest/search/?q=type:pipeline;organization:jenkins;pipeline:{ctx.b.jobs[0]}",
            f"{root}/blue/rest/search/?q=type:pipeline;organization:jenkins;pipeline:{PROBE_TOKEN}",
            False,
        ),
        (
            "blueocean pipelines/?q=",
            f"{root}/blue/rest/organizations/jenkins/pipelines/?q={term}",
            f"{root}/blue/rest/organizations/jenkins/pipelines/?q={PROBE_TOKEN}",
            False,
        ),
        (
            "blueocean run log/?q=",
            f"{blue}/log/?q=Finished&thresholdInKB=1",
            f"{blue}/log/?q={PROBE_TOKEN}&thresholdInKB=1",
            False,
        ),
        ("wfapi run-level log", f"{b}/wfapi/log", None, False),
        ("pluginManager api", f"{root}/pluginManager/api/json?depth=1", None, False),
    ]
    print("\nprobe: endpoint | status | content-type | bytes | query text honored?")
    for name, hit_url, miss_url, big in endpoints:
        first = ctx.j.fetch(hit_url, on_chunk=(lambda _c: True) if big else None)
        ctype = first.headers.get("Content-Type", "-").split(";")[0]
        honored = "n/a"
        if miss_url is not None:
            miss = ctx.j.fetch(miss_url, on_chunk=(lambda _c: True) if big else None)
            if big:
                honored = (
                    "no (non-empty body for a no-match term)" if miss.decoded else "yes"
                )
            else:
                honored = "yes" if miss.body != first.body else "no (same body)"
        size = f"{first.decoded}+" if big else str(first.decoded)
        print(f"  {name:40} {first.status:>4}  {ctype:17} {size:>9}  {honored}")
    node = ctx.j.fetch(blue + "/nodes/").json()[0]["id"]
    small = f"{blue}/nodes/{node}/log/"
    base = ctx.j.fetch(small).body
    changed = [
        p
        for p in FILTER_PARAMS
        if ctx.j.fetch(small, params={p: PROBE_TOKEN}).body != base
    ]
    print(
        f"  small-log filter-param test ({len(base)} B): params changing body: {changed or 'none'}"
    )
    page = ctx.j.fetch(b + "/consoleFull").text + ctx.j.fetch(root + "/").text
    plugins = sorted(set(re.findall(r"/plugin/([\w.-]+)/", page)))
    print(f"  plugin names in build/root page HTML ({len(plugins)}): {plugins}")
    print(f"  search-like hints: {[p for p in plugins if HINT.search(p)] or 'none'}")
    return False, "see table above"


RUNNERS = {
    "baseline": baseline,
    "stage": stage,
    "stage-bo": stage_bo,
    "tail": tail,
    "blueocean": blueocean,
    "timestamps": timestamps,
    "chunked": chunked,
    "probe": probe,
}


def run(ctx: Ctx, name: str) -> Result:
    ctx.j.meter = Meter()
    began = time.perf_counter()
    try:
        found, detail = RUNNERS[name](ctx)
    except (requests.RequestException, KeyError, ValueError) as exc:
        found, detail = None, f"ERROR {type(exc).__name__}: {exc}"
    m = ctx.j.meter
    return Result(
        name,
        m.requests,
        m.wire,
        m.rx,
        m.decoded,
        time.perf_counter() - began,
        found,
        detail,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("build_url")
    ap.add_argument("--pattern", default=r"error|fail|exception|FAILURE|Finished:")
    ap.add_argument("--case-sensitive", action="store_true")
    ap.add_argument(
        "--strategy", nargs="+", default=["all"], choices=("all", *STRATEGIES)
    )
    ap.add_argument("--tail-kb", type=int, default=64)
    ap.add_argument("--max-kb", type=int, default=4096)
    ap.add_argument("--step-kb", type=int, default=16, help="Blue Ocean step log tail")
    ap.add_argument("--max-stages", type=int, default=3)
    ap.add_argument("--window-lines", type=int, default=500)
    ap.add_argument("--max-lines", type=int, default=100_000)
    ap.add_argument("--baseline-dir", default="tmp")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    creds = Path(os.environ.get("JENKINS_CREDENTIALS", DEFAULT_CREDENTIALS))
    flags = 0 if args.case_sensitive else re.IGNORECASE
    ctx = Ctx(
        Jenkins(creds),
        parse_build_url(args.build_url),
        re.compile(args.pattern, flags),
        args,
    )
    names = (
        [n for n in STRATEGIES if n != "probe"]
        if "all" in args.strategy
        else args.strategy
    )
    results = [run(ctx, n) for n in names]

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=1))
        return 0
    print(f"\n{ctx.b.jobs[-1]} #{ctx.b.number} pattern={args.pattern!r}")
    print(
        f"{'strategy':11} {'reqs':>4} {'body KB':>9} {'rx KB':>9} {'text KB':>9} {'sec':>7}  found  detail"
    )
    for r in results:
        print(
            f"{r.strategy:11} {r.requests:>4} {r.wire / KB:>9.1f} {r.rx / KB:>9.1f} "
            f"{r.decoded / KB:>9.1f} {r.seconds:>7.2f}  {str(r.found):5}  {r.detail}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
