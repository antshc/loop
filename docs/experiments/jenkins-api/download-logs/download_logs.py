"""Disposable experiment: download Jenkins build logs from a build URL.

Modes: full (/consoleText streamed), stream (/logText/progressiveText follow), tail (last N bytes).
Credentials come from a JSON file (keys jenkinsUser, jenkinsApiToken); they are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zlib
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CHUNK = 64 * 1024
DEFAULT_CREDS = Path(__file__).resolve().parent.parent / "jenkins.json.user"
META_TREE = (
    "number,displayName,result,building,duration,estimatedDuration,timestamp,url,"
    "actions[_class,parameters[name,value],causes[shortDescription]]"
)


class _Session(requests.Session):
    """Session whose verify=False wins over REQUESTS_CA_BUNDLE / SSL_CERT_FILE."""

    def merge_environment_settings(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        settings = super().merge_environment_settings(*args, **kwargs)
        settings["verify"] = False
        return settings


def make_session(creds_file: Path, gzip: bool = True) -> requests.Session:
    creds = json.loads(creds_file.read_text())
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session = _Session()
    session.auth = (creds["jenkinsUser"], creds["jenkinsApiToken"])
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    if not gzip:
        session.headers["Accept-Encoding"] = "identity"
    return session


def normalize_build_url(url: str) -> str:
    """Re-quote the path so literal spaces and %20 both end up as %20."""
    parts = urlsplit(url.strip())
    path = quote(unquote(parts.path), safe="/-_.~")
    return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}/"


def build_dir_names(build_url: str) -> tuple[str, str]:
    segments = [unquote(s) for s in urlsplit(build_url).path.strip("/").split("/")]
    folders = [segments[i + 1] for i, s in enumerate(segments[:-1]) if s == "job"]
    job = "__".join(re.sub(r"[^\w.-]+", "_", f) for f in folders)
    return job, re.sub(r"[^\w.-]+", "_", segments[-1])


@dataclass
class Stats:
    mode: str
    url: str
    file: str
    requests: int
    body_bytes: int
    wire_bytes: int
    file_bytes: int
    seconds: float
    sha256: str
    content_encoding: str | None
    x_text_size: int | None = None
    more_data: bool | None = None
    note: str = ""


class _Counter:
    wire = 0
    body = 0


def _iter_decoded(resp: requests.Response, counter: _Counter) -> Iterator[bytes]:
    """Yield decoded body chunks; count compressed bytes as received on the wire."""
    gzipped = resp.headers.get("Content-Encoding", "").lower() == "gzip"
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if gzipped else None
    for raw in resp.raw.stream(CHUNK, decode_content=False):
        counter.wire += len(raw)
        data = inflater.decompress(raw) if inflater else raw
        counter.body += len(data)
        if data:
            yield data


class _Sink:
    """Writes chunks to a file, hashing what is written; optionally converts CRLF to LF."""

    def __init__(self, fh: Any, lf: bool) -> None:
        self.fh, self.lf, self.carry = fh, lf, b""
        self.digest = hashlib.sha256()
        self.written = 0

    def write(self, data: bytes) -> None:
        if self.lf:
            data = (self.carry + data).replace(b"\r\n", b"\n")
            # keep a trailing CR: its LF may arrive in the next chunk
            self.carry = data[-1:] if data.endswith(b"\r") else b""
            data = data[: len(data) - len(self.carry)]
        self.fh.write(data)
        self.digest.update(data)
        self.written += len(data)

    def finish(self) -> None:
        if self.carry:
            self.fh.write(self.carry)
            self.digest.update(self.carry)
            self.written += len(self.carry)
            self.carry = b""


def fetch_metadata(s: requests.Session, build_url: str) -> dict[str, Any]:
    resp = s.get(build_url + "api/json", params={"tree": META_TREE}, timeout=60)
    resp.raise_for_status()
    raw = resp.json()
    params: dict[str, Any] = {}
    causes: list[str] = []
    for action in raw.get("actions", []):
        for p in action.get("parameters", []) or []:
            params[p["name"]] = p.get("value")
        for c in action.get("causes", []) or []:
            causes.append(c.get("shortDescription", ""))
    return {
        "number": raw["number"],
        "displayName": raw.get("displayName"),
        "result": raw.get("result"),
        "building": raw.get("building"),
        "durationMs": raw.get("duration"),
        "estimatedDurationMs": raw.get("estimatedDuration"),
        "timestampMs": raw.get("timestamp"),
        "url": raw.get("url"),
        "parameters": params,
        "causes": causes,
    }


def download_full(s: requests.Session, build_url: str, dest: Path) -> Stats:
    url = build_url + "consoleText"
    counter = _Counter()
    t0 = time.monotonic()
    with s.get(url, stream=True, timeout=(10, 120)) as resp, dest.open("wb") as fh:
        resp.raise_for_status()
        sink = _Sink(fh, lf=False)
        for chunk in _iter_decoded(resp, counter):
            sink.write(chunk)
        enc = resp.headers.get("Content-Encoding")
    return Stats(
        "full",
        url,
        str(dest),
        1,
        counter.body,
        counter.wire,
        sink.written,
        time.monotonic() - t0,
        sink.digest.hexdigest(),
        enc,
    )


def download_stream(
    s: requests.Session,
    build_url: str,
    dest: Path,
    poll_interval: float = 2.0,
    max_seconds: float | None = None,
    lf: bool = False,
) -> Stats:
    """Follow progressiveText: next start = X-Text-Size; stop when X-More-Data is absent."""
    url = build_url + "logText/progressiveText"
    t0 = time.monotonic()
    offset, requests_made = 0, 0
    counter = _Counter()
    more, enc, note = True, None, ""
    with dest.open("wb") as fh:
        sink = _Sink(fh, lf)
        while more:
            with s.get(
                url, params={"start": offset}, stream=True, timeout=(10, 120)
            ) as resp:
                resp.raise_for_status()
                for chunk in _iter_decoded(resp, counter):
                    sink.write(chunk)
                enc = resp.headers.get("Content-Encoding")
                size_hdr = resp.headers.get("X-Text-Size")
                more = resp.headers.get("X-More-Data", "").lower() == "true"
            fh.flush()
            requests_made += 1
            if size_hdr is None:
                raise RuntimeError("progressiveText response lacks X-Text-Size")
            offset = int(size_hdr)
            if more:
                if max_seconds is not None and time.monotonic() - t0 >= max_seconds:
                    note = "stopped by --max-seconds while build still running"
                    break
                time.sleep(poll_interval)
        sink.finish()
    return Stats(
        "stream",
        url,
        str(dest),
        requests_made,
        counter.body,
        counter.wire,
        sink.written,
        time.monotonic() - t0,
        sink.digest.hexdigest(),
        enc,
        offset,
        more,
        note,
    )


def download_tail(
    s: requests.Session, build_url: str, dest: Path, tail_bytes: int, lf: bool = False
) -> Stats:
    url = build_url + "logText/progressiveText"
    t0 = time.monotonic()
    head = s.head(url, params={"start": 0}, timeout=60)
    head.raise_for_status()
    size_hdr = head.headers.get("X-Text-Size")
    if size_hdr is None:
        raise RuntimeError("HEAD progressiveText lacks X-Text-Size")
    size = int(size_hdr)
    start = max(0, size - tail_bytes)
    counter = _Counter()
    with s.get(url, params={"start": start}, stream=True, timeout=(10, 120)) as resp:
        resp.raise_for_status()
        data = b"".join(_iter_decoded(resp, counter))
        enc = resp.headers.get("Content-Encoding")
        end = int(resp.headers.get("X-Text-Size", size))
        more = resp.headers.get("X-More-Data", "").lower() == "true"
    if start > 0:
        # start is a raw byte offset, so the first line is usually partial
        nl = data.find(b"\n")
        data = data[nl + 1 :] if nl >= 0 else data
    if lf:
        data = data.replace(b"\r\n", b"\n")
    dest.write_bytes(data)
    return Stats(
        "tail",
        url,
        str(dest),
        2,
        counter.body,
        counter.wire,
        len(data),
        time.monotonic() - t0,
        hashlib.sha256(data).hexdigest(),
        enc,
        end,
        more,
        f"start={start}, first partial line dropped" if start else "whole log",
    )


def blue_ocean_run_url(build_url: str) -> str:
    """.../job/A/job/B/job/PR-1/7/ -> .../blue/rest/.../pipelines/A/pipelines/B/branches/PR-1/runs/7/"""
    parts = urlsplit(build_url)
    segs = parts.path.strip("/").split("/")
    jobs = [segs[i + 1] for i, x in enumerate(segs[:-1]) if x == "job"]
    prefix = parts.path[: parts.path.index("/job/")]
    chain = "/".join(f"pipelines/{j}" for j in jobs[:-1]) + f"/branches/{jobs[-1]}"
    return f"{parts.scheme}://{parts.netloc}{prefix}/blue/rest/organizations/jenkins/{chain}/runs/{segs[-1]}/"


def download_stages(
    s: requests.Session, build_url: str, out: Path, max_steps: int
) -> list[dict]:
    """Per-stage logs via Blue Ocean nodes/steps; one file per stage (steps concatenated)."""
    run = blue_ocean_run_url(build_url)
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(build_url))
    stage_dir = out / "stages"
    stage_dir.mkdir(exist_ok=True)
    report: list[dict] = []
    steps_seen = 0
    nodes = s.get(run + "nodes/", timeout=60)
    nodes.raise_for_status()
    for node in nodes.json():
        name = re.sub(r"[^\w.-]+", "_", node["displayName"].strip())
        path = stage_dir / f"{int(node['id']):04d}-{name}.log"
        steps = s.get(f"{run}nodes/{node['id']}/steps/", timeout=60)
        steps.raise_for_status()
        written = 0
        with path.open("wb") as fh:
            for step in steps.json():
                if steps_seen >= max_steps:
                    break
                log_link = next(
                    (
                        a["_links"]["self"]["href"]
                        for a in step.get("actions", [])
                        if a.get("urlName") == "log"
                    ),
                    None,
                )
                if not log_link:
                    continue
                steps_seen += 1
                header = (
                    f"--- step {step['id']}: {step['displayName']} ({step['result']})\n"
                )
                fh.write(header.encode())
                with s.get(
                    origin + quote(log_link, safe="/"),
                    params={"start": 0},
                    stream=True,
                    timeout=(10, 120),
                ) as r:
                    if r.status_code == 200:
                        for chunk in r.iter_content(chunk_size=CHUNK):
                            fh.write(chunk)
                            written += len(chunk)
        if written == 0:
            path.unlink()
        report.append(
            {
                "id": node["id"],
                "stage": node["displayName"].strip(),
                "result": node["result"],
                "durationMs": node["durationInMillis"],
                "bytes": written,
                "file": path.name if written else None,
            }
        )
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("build_url")
    ap.add_argument("--out", type=Path, default=Path("tmp/jenkins-logs"))
    ap.add_argument("--mode", choices=("full", "stream", "tail"), default="full")
    ap.add_argument("--tail-bytes", type=int, default=65536)
    ap.add_argument(
        "--stages", action="store_true", help="also save per-stage logs (Blue Ocean)"
    )
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--poll-interval", type=float, default=2.0)
    ap.add_argument(
        "--max-seconds", type=float, default=None, help="stream: stop following"
    )
    ap.add_argument("--lf", action="store_true", help="stream/tail: convert CRLF to LF")
    ap.add_argument(
        "--no-gzip", action="store_true", help="send Accept-Encoding: identity"
    )
    ap.add_argument(
        "--creds",
        type=Path,
        default=Path(os.environ.get("JENKINS_CREDS_FILE", DEFAULT_CREDS)),
    )
    args = ap.parse_args(argv)

    s = make_session(args.creds, gzip=not args.no_gzip)
    build_url = normalize_build_url(args.build_url)
    job, build = build_dir_names(build_url)
    out = args.out / job / build
    out.mkdir(parents=True, exist_ok=True)

    meta = fetch_metadata(s, build_url)
    if args.mode == "full":
        stats = download_full(s, build_url, out / "console-full.log")
    elif args.mode == "stream":
        stats = download_stream(
            s,
            build_url,
            out / "console-stream.log",
            args.poll_interval,
            args.max_seconds,
            args.lf,
        )
    else:
        stats = download_tail(
            s, build_url, out / "console-tail.log", args.tail_bytes, args.lf
        )
    meta_path = out / "build.json"
    previous = (
        json.loads(meta_path.read_text()).get("downloads", {})
        if meta_path.exists()
        else {}
    )
    previous[stats.mode] = asdict(stats)
    meta["downloads"] = previous
    if args.stages:
        meta["stages"] = download_stages(s, build_url, out, args.max_steps)
    meta_path.write_text(json.dumps(meta, indent=2))
    print(json.dumps({k: v for k, v in asdict(stats).items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
