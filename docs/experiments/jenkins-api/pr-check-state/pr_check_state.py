"""Disposable experiment: derive the Jenkins PR job from a GitHub PR and read its state.

Read-only. Never triggers a build. Credentials come from JENKINS_CREDENTIALS_FILE
(JSON with jenkinsUser, jenkinsApiToken) and are never printed.

Usage: pr_check_state.py PR_NUMBER [--repo OWNER/REPO] [--wait] [--timeout S] [--interval S]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_REPO = "example-org/example-repo"
JENKINS_CONTEXT = "continuous-integration/jenkins/pr-head"
CREDENTIALS_ENV = "JENKINS_CREDENTIALS_FILE"

# Jenkins result -> GitHub status states the Branch Source plugin is expected to publish.
EXPECTED_GITHUB_STATES: dict[str | None, set[str]] = {
    None: {"pending"},
    "SUCCESS": {"success"},
    "UNSTABLE": {"failure", "success"},
    "FAILURE": {"failure", "error"},
    "ABORTED": {"failure", "error"},
    "NOT_BUILT": {"error", "failure"},
}

BUILD_TREE = (
    "number,url,building,result,timestamp,duration,estimatedDuration,queueId,displayName,"
    "actions[parameters[name,value],lastBuiltRevision[SHA1],causes[shortDescription]]"
)
JOB_TREE = (
    "name,url,buildable,inQueue,queueItem[id,why,blocked,buildable,stuck,inQueueSince],"
    "lastBuild[number,url],lastCompletedBuild[number,url,result],"
    "lastSuccessfulBuild[number],lastFailedBuild[number]"
)


@dataclass(frozen=True)
class JenkinsRefs:
    base_url: str
    job_url: str
    build_number: int | None
    build_url: str | None


@dataclass(frozen=True)
class Pull:
    repo: str
    head_sha: str
    merge_sha: str | None

    def accepts(self, built: str) -> bool:
        """True if `built` is the head, GitHub's current merge commit, or a merge of the head.

        The PR job builds head merged into the target branch. GitHub's merge_commit_sha
        moves when the target moves, so an older built merge commit is checked by parent.
        """
        if built in {self.head_sha, self.merge_sha}:
            return True
        parents = gh_json(f"repos/{self.repo}/commits/{built}")["parents"]
        return self.head_sha in {p["sha"] for p in parents}


def gh_json(path: str) -> Any:
    out = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def find_jenkins_entry(repo: str, sha: str) -> dict[str, Any]:
    """Look in commit statuses and check-runs; report where the Jenkins entry lives."""
    statuses = gh_json(f"repos/{repo}/commits/{sha}/statuses?per_page=100")
    check_runs = gh_json(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")[
        "check_runs"
    ]
    jenkins_runs = [r["name"] for r in check_runs if "jenkins" in json.dumps(r).lower()]
    # /statuses lists newest first, so the first match per context is the current one.
    entry = next((s for s in statuses if s["context"] == JENKINS_CONTEXT), None)
    if entry is None:
        raise SystemExit(f"no {JENKINS_CONTEXT} status on {sha}")
    return {
        "entry": entry,
        "in_commit_status": True,
        "in_check_runs": bool(jenkins_runs),
        "status_contexts": sorted({s["context"] for s in statuses}),
        "check_run_names": [r["name"] for r in check_runs],
    }


def parse_target_url(target_url: str) -> JenkinsRefs:
    """`<base>/job/A/job/B/job/PR-n/<build>/display/redirect?x` -> job and build URLs."""
    parts = urlsplit(target_url)
    path = parts.path  # query and fragment are dropped by only keeping the path
    path = re.sub(r"/display/redirect/?$", "/", path)
    match = re.fullmatch(r"(?P<job>(?:/job/[^/]+)+)(?:/(?P<build>\d+))?/?", path)
    if match is None:
        raise ValueError(f"unrecognised Jenkins URL path: {path}")
    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    job_url = f"{base}{match['job']}/"
    number = int(match["build"]) if match["build"] else None
    build_url = f"{job_url}{number}/" if number is not None else None
    return JenkinsRefs(base, job_url, number, build_url)


def load_session() -> requests.Session:
    path = os.environ.get(CREDENTIALS_ENV)
    if not path:
        raise SystemExit(f"set {CREDENTIALS_ENV} to the credentials JSON file")
    with open(path, encoding="utf-8") as handle:
        creds = json.load(handle)
    session = requests.Session()
    session.auth = (creds["jenkinsUser"], creds["jenkinsApiToken"])
    return session


# verify=False per request: REQUESTS_CA_BUNDLE in the env overrides session.verify.
def jenkins_get(
    session: requests.Session, url: str, tree: str
) -> dict[str, Any] | None:
    resp = session.get(
        f"{url}api/json", params={"tree": tree}, timeout=30, verify=False
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def summarise_build(build: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    sha = None
    causes: list[str] = []
    for action in build.get("actions", []):
        for p in action.get("parameters", []) or []:
            params[p["name"]] = p.get("value")
        if "lastBuiltRevision" in action:
            sha = action["lastBuiltRevision"].get("SHA1")
        causes += [c["shortDescription"] for c in action.get("causes", []) or []]
    started = build["timestamp"] / 1000
    elapsed = time.time() - started
    return {
        "number": build["number"],
        "url": build["url"],
        "building": build["building"],
        "result": build["result"],
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_ms": build["duration"],
        "elapsed_s": round(elapsed) if build["building"] else None,
        "estimated_duration_ms": build["estimatedDuration"],
        "queue_id": build.get("queueId"),
        "parameters": params,
        "built_sha": sha,
        "causes": causes,
    }


def read_state(session: requests.Session, refs: JenkinsRefs) -> dict[str, Any]:
    job = jenkins_get(session, refs.job_url, JOB_TREE)
    if job is None:
        raise SystemExit(f"job not found: {refs.job_url}")
    build = jenkins_get(session, refs.build_url, BUILD_TREE) if refs.build_url else None
    last_url = (job.get("lastBuild") or {}).get("url")
    last = None
    if build is None and last_url:
        raw = jenkins_get(session, last_url, BUILD_TREE)
        last = summarise_build(raw) if raw else None
    return {
        "job": {
            "in_queue": job["inQueue"],
            "queue_item": job.get("queueItem"),
            "last_build": job.get("lastBuild"),
            "last_completed_build": job.get("lastCompletedBuild"),
            "last_successful_build": job.get("lastSuccessfulBuild"),
            "last_failed_build": job.get("lastFailedBuild"),
        },
        "build": summarise_build(build) if build else None,
        "last_build_detail": last,
    }


def finished(state: dict[str, Any]) -> bool:
    build = state["build"]
    return build is not None and not build["building"] and build["result"] is not None


def resolve(repo: str, pr: int) -> tuple[Pull, dict[str, Any], JenkinsRefs]:
    data = gh_json(f"repos/{repo}/pulls/{pr}")
    pull = Pull(repo, data["head"]["sha"], data.get("merge_commit_sha"))
    found = find_jenkins_entry(repo, pull.head_sha)
    return pull, found, parse_target_url(found["entry"]["target_url"])


def wait_until_done(
    session: requests.Session, repo: str, pr: int, timeout: float, interval: float
) -> tuple[tuple[Pull, dict[str, Any], JenkinsRefs], dict[str, Any], dict[str, Any]]:
    """Poll until the status' build exists and has building=false and a result.

    The status is re-read on every poll: while the build is only queued, the target_url
    has no build number yet.
    """
    deadline = time.monotonic() + timeout
    polls = 0
    while True:
        resolved = resolve(repo, pr)
        state = read_state(session, resolved[2])
        polls += 1
        done = finished(state)
        if done or time.monotonic() >= deadline:
            return resolved, state, {"finished": done, "polls": polls}
        time.sleep(interval)


def compare(
    github_state: str, pull: Pull, refs: JenkinsRefs, state: dict[str, Any]
) -> list[str]:
    notes: list[str] = []
    build = state["build"]
    if build is None:
        last = state["last_build_detail"]
        note = "status target_url has no build number: the build is still queued"
        if last:
            note += (
                f"; job lastBuild #{last['number']} is {last['result'] or 'running'}"
            )
            if last["built_sha"] and not pull.accepts(last["built_sha"]):
                note += f" and built {last['built_sha'][:10]}, not this PR head/merge commit"
        return [note]
    result = build["result"]
    expected = EXPECTED_GITHUB_STATES.get(result, set())
    label = "building" if build["building"] else result
    if build["building"] and github_state != "pending":
        notes.append(
            f"build running but GitHub state is {github_state!r}, expected 'pending'"
        )
    elif not build["building"] and github_state not in expected:
        notes.append(
            f"Jenkins {label} but GitHub state is {github_state!r}, expected {expected}"
        )
    if build["built_sha"] and not pull.accepts(build["built_sha"]):
        notes.append(f"build built {build['built_sha'][:10]}, not PR head/merge commit")
    last = (state["job"]["last_build"] or {}).get("number")
    if last is not None and last != build["number"]:
        notes.append(
            f"status points at #{build['number']} but job lastBuild is #{last}"
        )
    return notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pr", type=int)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--wait", action="store_true", help="poll until building=false")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--interval", type=float, default=10)
    args = ap.parse_args()

    session = load_session()
    waited: dict[str, Any] | None = None
    if args.wait:
        (pull, found, refs), state, waited = wait_until_done(
            session, args.repo, args.pr, args.timeout, args.interval
        )
    else:
        pull, found, refs = resolve(args.repo, args.pr)
        state = read_state(session, refs)
    entry = found["entry"]

    report = {
        "pr": args.pr,
        "head_sha": pull.head_sha,
        "merge_commit_sha": pull.merge_sha,
        "github": {
            "jenkins_in_commit_status": found["in_commit_status"],
            "jenkins_in_check_runs": found["in_check_runs"],
            "status_contexts": found["status_contexts"],
            "check_run_names": found["check_run_names"],
            "state": entry["state"],
            "description": entry["description"],
            "target_url": entry["target_url"],
        },
        "derived": {
            "job_url": refs.job_url,
            "build_number": refs.build_number,
            "build_url": refs.build_url,
        },
        "jenkins": state,
        "mismatches": compare(entry["state"], pull, refs, state),
    }
    if waited:
        report["wait"] = waited
    print(json.dumps(report, indent=2))
    return 0 if waited is None or waited["finished"] else 2


if __name__ == "__main__":
    sys.exit(main())
