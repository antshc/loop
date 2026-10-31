"""Disposable verification helpers for the download-logs experiment.

verify.py compare DIR          sha256 + suffix checks over saved files in DIR
verify.py offsets BUILD_URL FULL_LF_FILE   raw start= offset vs position in text
verify.py memory pyjenkins|full|stream JOB_PATH BUILD_NUMBER   peak RSS of one method
"""

from __future__ import annotations

import hashlib
import mmap
import os
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from download_logs import (  # noqa: E402
    DEFAULT_CREDS,
    _Counter,
    _iter_decoded,
    make_session,
    normalize_build_url,
)

CREDS = Path(os.environ.get("JENKINS_CREDS_FILE", DEFAULT_CREDS))


def sha_stripped(path: Path, drop: bytes) -> str:
    """sha256 of the file with `drop` bytes removed (handles chunk edges: drop is 1 byte)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk.replace(drop, b""))
    return digest.hexdigest()


def compare(directory: Path) -> None:
    full, stream, tail = (
        directory / n
        for n in ("console-full.log", "console-stream.log", "console-tail.log")
    )
    print("full (CR stripped) sha256 :", sha_stripped(full, b"\r"))
    print(
        "stream --lf sha256        :", hashlib.sha256(stream.read_bytes()).hexdigest()
    )
    print("full raw sha256           :", hashlib.sha256(full.read_bytes()).hexdigest())
    print(
        "sizes full/stream/tail    :",
        full.stat().st_size,
        stream.stat().st_size,
        tail.stat().st_size,
    )
    text = stream.read_bytes()
    print("tail is suffix of stream  :", text.endswith(tail.read_bytes()))


def offsets(build_url: str, full_lf: Path) -> None:
    s = make_session(CREDS)
    url = normalize_build_url(build_url) + "logText/progressiveText"
    with (
        full_lf.open("rb") as fh,
        mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as text,
    ):
        total = len(text)
        for k in (0, 1_000_000, 10_000_000, 20_000_000, 30_000_000):
            with s.get(url, params={"start": k}, stream=True, timeout=60) as resp:
                counter = _Counter()
                data = b""
                for chunk in _iter_decoded(resp, counter):
                    data += chunk
                    if len(data) >= 4096:
                        break
                size = resp.headers.get("X-Text-Size")
            data = data.replace(b"\r\n", b"\n")
            if k:
                data = data[data.find(b"\n") + 1 :]
            probe = data[:1024]
            pos = text.find(probe)
            print(
                f"start={k:>9} -> first-line-aligned text pos={pos:>9}  "
                f"(text bytes before it: {pos - k:+d}), text len={total}, size_hdr={size}"
            )


def memory(method: str, job_path: str, number: int) -> None:
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        os.environ.pop(var, None)
    base_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    t0 = time.monotonic()
    if method == "pyjenkins":
        import json

        import jenkins

        creds = json.loads(CREDS.read_text())
        server = jenkins.Jenkins(
            creds["jenkinsUrl"], creds["jenkinsUser"], creds["jenkinsApiToken"]
        )
        server._session.verify = False  # self-signed
        import urllib3

        urllib3.disable_warnings()
        text = server.get_build_console_output(job_path, number)
        size = len(text)
    else:
        import download_logs as dl

        s = make_session(CREDS)
        build_url = normalize_build_url(
            f"{json_url()}/job/" + "/job/".join(job_path.split("/")) + f"/{number}"
        )
        dest = Path("tmp/mem-probe.log")
        stats = (dl.download_full if method == "full" else dl.download_stream)(
            s, build_url, dest
        )
        size = stats.file_bytes
        dest.unlink()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(
        f"{method}: bytes={size} seconds={time.monotonic() - t0:.2f} "
        f"peakRSS={peak:.0f} MiB (at start {base_mb:.0f} MiB)"
    )


def json_url() -> str:
    import json

    return json.loads(CREDS.read_text())["jenkinsUrl"].rstrip("/")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "compare":
        compare(Path(sys.argv[2]))
    elif cmd == "offsets":
        offsets(sys.argv[2], Path(sys.argv[3]))
    elif cmd == "memory":
        memory(sys.argv[2], sys.argv[3], int(sys.argv[4]))
