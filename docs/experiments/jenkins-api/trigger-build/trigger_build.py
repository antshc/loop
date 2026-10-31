"""Disposable experiment: trigger a parameterized multibranch PR build and follow it briefly.

Usage: trigger_build.py JOB_OR_PR_URL [--param K=V ...] [--dry-run]

Credentials come from the JSON file named by $JENKINS_JSON (keys jenkinsUrl, jenkinsUser,
jenkinsApiToken). The token is only passed to requests as Basic auth and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

DEFAULT_CREDS = "/home/pet/_projects/afk/loop/docs/prototypes/jenkins-api/jenkins.json.user"
DEFINITIONS_TREE = (
    "property[parameterDefinitions[name,type,defaultParameterValue[value],choices,description]]"
)
QUEUE_POLL_SECONDS = 3
BUILD_POLL_SECONDS = 10
MAX_WAIT_SECONDS = 180

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def derive_job_url(url: str) -> str:
    """Strip a trailing `/<n>/display/redirect` (or `/<n>`) and any query from a check URL."""
    path = urlparse(url).path
    path = re.sub(r"/\d+/display/redirect/?$", "", path)
    path = re.sub(r"/\d+/?$", "", path)
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}"


def make_session() -> requests.Session:
    creds = json.loads(Path(os.environ.get("JENKINS_JSON", DEFAULT_CREDS)).read_text())
    session = requests.Session()
    session.auth = (creds["jenkinsUser"], creds["jenkinsApiToken"])
    # verify=False is passed per request: REQUESTS_CA_BUNDLE overrides session.verify.
    return session


def get_definitions(session: requests.Session, job_url: str) -> list[dict[str, Any]]:
    response = session.get(
        f"{job_url}/api/json", params={"tree": DEFINITIONS_TREE}, timeout=30, verify=False
    )
    response.raise_for_status()
    definitions: list[dict[str, Any]] = []
    for prop in response.json().get("property", []):
        definitions.extend(prop.get("parameterDefinitions", []))
    return definitions


def build_payload(definitions: list[dict[str, Any]], overrides: dict[str, str]) -> dict[str, str]:
    names = {d["name"] for d in definitions}
    unknown = set(overrides) - names
    if unknown:
        raise SystemExit(f"unknown parameters {sorted(unknown)}; job defines {sorted(names)}")
    payload: dict[str, str] = {}
    for d in definitions:
        value = overrides.get(d["name"], (d.get("defaultParameterValue") or {}).get("value"))
        if value is not None:
            payload[d["name"]] = str(value).lower() if isinstance(value, bool) else str(value)
    return payload


def get_json(session: requests.Session, url: str) -> dict[str, Any]:
    response = session.get(url, timeout=30, verify=False)
    response.raise_for_status()
    return response.json()


def follow_queue(session: requests.Session, queue_url: str, deadline: float) -> str | None:
    """Poll the queue item; return the build URL once it has an executable."""
    while time.monotonic() < deadline:
        item = get_json(session, f"{queue_url.rstrip('/')}/api/json")
        executable = item.get("executable")
        print(
            f"queue: cancelled={item.get('cancelled')} why={item.get('why')!r} "
            f"executable={executable}"
        )
        if item.get("cancelled"):
            return None
        if executable:
            return str(executable["url"])
        time.sleep(QUEUE_POLL_SECONDS)
    return None


def follow_build(session: requests.Session, build_url: str, deadline: float) -> None:
    """Report building/result a few times; stop at the deadline or when finished."""
    while True:
        build = get_json(
            session, f"{build_url.rstrip('/')}/api/json?tree=number,building,result,timestamp"
        )
        print(
            f"build #{build['number']}: building={build['building']} result={build['result']}"
        )
        if not build["building"] or time.monotonic() >= deadline:
            return
        time.sleep(BUILD_POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--param", action="append", default=[], metavar="K=V")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    overrides = dict(p.split("=", 1) for p in args.param)
    job_url = derive_job_url(args.url)
    session = make_session()
    definitions = get_definitions(session, job_url)
    payload = build_payload(definitions, overrides)

    print(f"job url: {job_url}")
    for d in definitions:
        default = (d.get("defaultParameterValue") or {}).get("value")
        print(
            f"definition: {d['name']} [{d['type']}] default={default!r} choices={d.get('choices')}"
        )
    print(f"POST {job_url}/buildWithParameters payload={json.dumps(payload)}")
    if args.dry_run:
        print("dry-run: nothing posted")
        return 0

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    response = session.post(
        f"{job_url}/buildWithParameters", data=payload, timeout=30, verify=False
    )
    location = response.headers.get("Location")
    print(f"trigger: status={response.status_code} location={location}")
    if response.status_code != 201 or not location:
        print(f"unexpected trigger response (not retrying): {response.text[:300]!r}")
        return 1

    build_url = follow_queue(session, location, deadline)
    if build_url is None:
        print("no build started within the wait cap (or queue item cancelled)")
        return 2
    print(f"build url: {build_url}")
    follow_build(session, build_url, deadline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
