"""Bounded Jenkins reads and crash-conservative whole-job recovery for the factory."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import ssl
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

logger = logging.getLogger(__name__)
TOTAL_BUILDS = 3
_SHA = re.compile(r"[0-9a-f]{40,64}")
_FAILED = {"FAILURE", "UNSTABLE", "ABORTED"}


class JenkinsError(ValueError):
    """An actionable Jenkins observation or recovery failure, without response secrets."""


class JenkinsBusy(JenkinsError):
    """Jenkins already has queued work; observation should wait rather than submit."""


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise JenkinsError(f"Jenkins {label} must be an array of objects")
    return value


@dataclass(frozen=True)
class JenkinsSettings:
    """One explicitly enabled, trusted Jenkins server shared by watched repositories."""

    url: str
    job_roots: tuple[str, ...]
    credentials_file: Path
    ca_bundle: Path | None = None
    insecure_tls: bool = False
    timeout: float = 30
    log_bytes: int = 262144

    @classmethod
    def load(cls, value: Any, base: Path) -> JenkinsSettings | None:
        """Validate opt-in configuration without reading credentials or contacting Jenkins."""
        if value is None:
            return None
        if not isinstance(value, dict) or type(value.get("enabled", False)) is not bool:
            raise ValueError("jenkins.enabled must be a boolean")
        if not value.get("enabled", False):
            return None
        url = value.get("url", "")
        if not isinstance(url, str):
            raise ValueError("jenkins.url must be an HTTPS origin")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("jenkins.url must be an HTTPS origin")
        roots = value.get("job_roots")
        if (
            not isinstance(roots, list)
            or not roots
            or any(
                not isinstance(root, str)
                or not root.startswith("/job/")
                or not root.endswith("/")
                or any(part in unquote(root) for part in ("..", "?", "#", "\\"))
                for root in roots
            )
        ):
            raise ValueError("jenkins.job_roots must contain trusted /job/.../ paths")
        credentials = value.get("credentials_file")
        ca = value.get("ca_bundle")
        if not isinstance(credentials, str) or not credentials:
            raise ValueError("jenkins.credentials_file is required when enabled")
        if ca is not None and (not isinstance(ca, str) or not ca):
            raise ValueError("jenkins.ca_bundle must be a path")
        insecure = value.get("insecure_tls", False)
        timeout = value.get("timeout", 30)
        size = value.get("log_bytes", 262144)
        if (
            type(insecure) is not bool
            or type(timeout) not in (int, float)
            or not 0 < timeout <= 3600
            or not math.isfinite(timeout)
            or type(size) is not int
            or not 1024 <= size <= 1048576
            or (insecure and ca is not None)
        ):
            raise ValueError("invalid Jenkins TLS, timeout, or log_bytes configuration")
        return cls(
            url.rstrip("/"),
            tuple(roots),
            (base / credentials).resolve(),
            (base / ca).resolve() if ca else None,
            insecure,
            float(timeout),
            size,
        )


@dataclass(frozen=True)
class JenkinsFailure:
    """A terminal failed build tied to a watched PR head and its remaining capacity."""

    job: str
    number: int
    head: str
    result: str
    attempts: int
    logs: str


@dataclass(frozen=True)
class Response:
    """A bounded HTTP result used by the injectable Jenkins transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        """Read a header independently of the server's capitalization."""
        return next(
            (
                value
                for key, value in self.headers.items()
                if key.lower() == name.lower()
            ),
            None,
        )


type Transport = Callable[[str, str, bytes | None, int], Response]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Jenkins:
    """Read trusted PR jobs and reserve durable capacity before single-shot submissions."""

    def __init__(
        self,
        settings: JenkinsSettings,
        state_dir: Path,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.settings = settings
        self.state_dir = state_dir
        self._transport = transport or self._http
        if settings.insecure_tls:
            logger.warning(
                "Jenkins TLS certificate verification explicitly disabled for %s",
                settings.url,
            )

    def failed_jobs(
        self, snapshot: dict[str, Any], number: int, *, failed_only: bool = True
    ) -> tuple[str, ...]:
        """Resolve latest failed Jenkins statuses/checks into validated whole-job URLs."""
        selected: dict[str, dict[str, Any]] = {}
        for status in sorted(snapshot["statuses"], key=lambda s: s["id"], reverse=True):
            selected.setdefault(f"status:{status['context']}", status)
        for check in sorted(snapshot["checks"], key=lambda c: c["id"], reverse=True):
            selected.setdefault(
                f"check:{check['name']}:{(check.get('app') or {}).get('id')}", check
            )
        jobs = set()
        for item in selected.values():
            failed = item.get("state") in {"error", "failure"} or item.get(
                "conclusion"
            ) in {"failure", "timed_out", "startup_failure"}
            url = item.get("target_url") or item.get("details_url") or ""
            if (failed_only and not failed) or urlsplit(url).netloc != urlsplit(
                self.settings.url
            ).netloc:
                continue
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise JenkinsError("Untrusted Jenkins check URL")
            path = urlsplit(url).path
            blue = re.fullmatch(
                r"/blue/organizations/jenkins/([^/]+)/detail/([^/]+)/\d+/pipeline/?",
                path,
            )
            if blue:
                parts = [*unquote(blue[1]).split("/"), unquote(blue[2])]
                path = (
                    "/"
                    + "/".join(f"job/{quote(part, safe='')}" for part in parts)
                    + "/"
                )
            path = re.sub(r"/(?:\d+/)?display/redirect/?$", "/", path)
            path = re.sub(r"/\d+/?$", "/", path)
            job = f"{self.settings.url}{path.rstrip('/')}/"
            self._validate(job)
            if not unquote(urlsplit(job).path).endswith(f"/job/PR-{number}/"):
                raise JenkinsError("Jenkins check does not identify this PR job")
            jobs.add(job)
        return tuple(sorted(jobs))

    def inspect(
        self, slug: str, number: int, head: str, jobs: tuple[str, ...]
    ) -> tuple[JenkinsFailure, ...]:
        """Reconcile history and return actionable failures, or wait for in-flight work."""
        with self._state(slug, number) as state:
            budget = self._budget(state, head)
            budget["jobs"] = sorted(set(budget["jobs"]) | set(jobs))
            self._save(slug, number, state)
            builds = self._history(jobs, slug)
            self._reconcile(budget, builds, head)
            self._save(slug, number, state)
            if budget["pending"]:
                raise JenkinsError(
                    "Jenkins submission is unresolved; inspect its queue/build before retrying"
                )
            attempts = len(budget["builds"])
            if any(build["building"] for _, build in builds):
                return ()
            failures = []
            for job in jobs:
                latest = max(
                    (build for build_job, build in builds if build_job == job),
                    key=lambda build: build["number"],
                )
                if self._head(latest) != head:
                    raise JenkinsError(
                        "Latest Jenkins build belongs to another PR head"
                    )
                if latest["result"] == "SUCCESS":
                    continue
                if latest["result"] not in _FAILED:
                    raise JenkinsError(
                        f"Jenkins build #{latest['number']} is {latest['result']}; manual inspection required"
                    )
                if attempts >= TOTAL_BUILDS:
                    raise JenkinsError(
                        f"Jenkins exhausted {TOTAL_BUILDS} total builds for head {head}; "
                        f"last failure: {job}{latest['number']}/"
                    )
                failures.append(
                    JenkinsFailure(
                        job,
                        latest["number"],
                        head,
                        latest["result"],
                        attempts,
                        self._logs(f"{job}{latest['number']}/"),
                    )
                )
            return tuple(failures)

    def jobs_for(
        self, slug: str, number: int, snapshot: dict[str, Any]
    ) -> tuple[str, ...]:
        """Retain observed job identities across head changes and absent GitHub statuses."""
        jobs = set(self.failed_jobs(snapshot, number, failed_only=False))
        if self._path(slug, number).exists():
            with self._state(slug, number) as state:
                for head in state:
                    budget = self._budget(state, head)
                    jobs.update(budget["jobs"])
                    for build in budget["builds"]:
                        jobs.add(re.sub(r"/\d+/$", "/", build))
                    if budget["pending"]:
                        jobs.add(budget["pending"]["job"])
        for job in jobs:
            self._validate(job)
        return tuple(sorted(jobs))

    def readiness(
        self, slug: str, number: int, head: str, snapshot: dict[str, Any]
    ) -> str:
        """Wait for a tracked job on a new head rather than assuming missing CI is green."""
        jobs = self.jobs_for(slug, number, snapshot)
        if not jobs:
            return "green"
        with self._state(slug, number) as state:
            budget = self._budget(state, head)
            budget["jobs"] = sorted(set(budget["jobs"]) | set(jobs))
            self._save(slug, number, state)
            try:
                builds = self._history(jobs, slug)
            except JenkinsBusy:
                return "waiting"
            self._reconcile(budget, builds, head)
            self._save(slug, number, state)
            if budget["pending"]:
                raise JenkinsError("Unresolved Jenkins submission blocks readiness")
            if any(build["building"] for _, build in builds):
                return "waiting"
            results = []
            for job in jobs:
                latest = max(
                    (build for build_job, build in builds if build_job == job),
                    key=lambda build: build["number"],
                )
                if self._head(latest) != head:
                    return "waiting"
                results.append(latest["result"])
            if any(result in _FAILED for result in results):
                return "failed"
            if any(result != "SUCCESS" for result in results):
                raise JenkinsError("Jenkins terminal result needs manual inspection")
            return "green"

    def restart(
        self,
        slug: str,
        number: int,
        failure: JenkinsFailure,
        current_head: Callable[[], str],
        jobs: tuple[str, ...],
    ) -> str | None:
        """Reserve one slot then POST once; never resend an uncertain submission."""
        with self._state(slug, number) as state:
            builds = self._history(jobs, slug)
            budget = self._budget(state, failure.head)
            self._reconcile(budget, builds, failure.head)
            self._save(slug, number, state)
            if budget["pending"]:
                raise JenkinsError("Previous Jenkins submission needs reconciliation")
            if any(build["building"] for _, build in builds):
                return None
            latest = max(
                (b for job, b in builds if job == failure.job),
                key=lambda b: b["number"],
            )
            if (
                self._head(latest) != failure.head
                or latest["number"] != failure.number
                or latest["result"] not in _FAILED
            ):
                return None
            if len(budget["builds"]) >= TOTAL_BUILDS:
                raise JenkinsError("Jenkins three-total-build budget exhausted")
            payload = self._payload(failure.job, latest)
            if current_head() != failure.head:
                raise JenkinsError("PR head changed before Jenkins restart")
            budget["pending"] = {
                "job": failure.job,
                "before": latest["number"],
                "queue": None,
            }
            self._save(slug, number, state)
            response = self._request(
                "POST",
                f"{failure.job}buildWithParameters",
                urlencode(payload).encode(),
                4096,
            )
            location = response.header("Location")
            if response.status != 201 or not location:
                raise JenkinsError(
                    "Jenkins trigger outcome uncertain; reservation retained, do not resubmit"
                )
            queue = urljoin(self.settings.url + "/", location)
            self._validate(queue, queue=True)
            if not re.fullmatch(r"/queue/item/\d+/", urlsplit(queue).path):
                raise JenkinsError("Jenkins returned an invalid queue identity")
            budget["pending"]["queue"] = queue
            self._save(slug, number, state)
            return queue

    def _history(
        self, jobs: tuple[str, ...], slug: str
    ) -> list[tuple[str, dict[str, Any]]]:
        builds = []
        for job in jobs:
            self._validate(job)
            info = self._json(
                f"{job}api/json",
                "inQueue,firstBuild[number],builds[number,building,result,queueId,"
                "actions[remoteUrls,revision[pullHash],"
                "parameters[name,value],lastBuiltRevision[SHA1]]]",
            )
            if type(info.get("inQueue")) is not bool:
                raise JenkinsError("Jenkins queue state is malformed")
            if info.get("inQueue"):
                raise JenkinsBusy(
                    "Jenkins job is queued; wait for its build before recovery"
                )
            history = _objects(info.get("builds"), "build history")
            if not history:
                raise JenkinsError("Jenkins build history unavailable")
            first_build = info.get("firstBuild")
            first = first_build.get("number") if isinstance(first_build, dict) else None
            numbers = [build.get("number") for build in history]
            if (
                type(first) is not int
                or first != 1
                or any(type(n) is not int for n in numbers)
                or sorted(numbers) != list(range(1, len(numbers) + 1))
            ):
                raise JenkinsError(
                    "Jenkins history incomplete; cannot safely count attempts"
                )
            for build in history:
                if type(build.get("building")) is not bool:
                    raise JenkinsError("Jenkins build state is malformed")
                if not build["building"] and not isinstance(build.get("result"), str):
                    raise JenkinsError("Jenkins terminal result is malformed")
                actions = _objects(build.get("actions"), "build actions")
                if any(
                    not isinstance(action.get("remoteUrls", []), list)
                    or any(
                        not isinstance(remote, str)
                        for remote in action.get("remoteUrls", [])
                    )
                    for action in actions
                ):
                    raise JenkinsError("Jenkins checkout identities are malformed")
                if not build["building"]:
                    if not self._head(build):
                        raise JenkinsError(
                            "Jenkins build lacks an unambiguous PR revision"
                        )
                    remotes = {
                        self._repository(remote)
                        for action in actions
                        for remote in action.get("remoteUrls", [])
                    }
                    if slug.lower() not in remotes:
                        raise JenkinsError(
                            "Jenkins checkout does not match the watched repository"
                        )
                builds.append((job, build))
        return builds

    @staticmethod
    def _head(build: dict[str, Any]) -> str | None:
        actions = _objects(build.get("actions"), "build actions")
        heads = {
            action["revision"]["pullHash"]
            for action in actions
            if isinstance(action.get("revision"), dict)
            and isinstance(action["revision"].get("pullHash"), str)
            and _SHA.fullmatch(action["revision"]["pullHash"])
        }
        return next(iter(heads)) if len(heads) == 1 else None

    @staticmethod
    def _repository(remote: str) -> str:
        remote = re.sub(r"^git@github\.com:", "https://github.com/", remote)
        parsed = urlsplit(remote)
        return (
            parsed.path.strip("/").removesuffix(".git").lower()
            if parsed.hostname == "github.com"
            else ""
        )

    def _logs(self, build: str) -> str:
        endpoint = f"{build}logText/progressiveText"
        size_response = self._request("HEAD", f"{endpoint}?start=0", None, 0)
        try:
            size = int(size_response.header("X-Text-Size") or "")
        except ValueError as exception:
            raise JenkinsError("Jenkins console size unavailable") from exception
        if size < 0:
            raise JenkinsError("Jenkins console size is invalid")
        start = max(0, size - self.settings.log_bytes)
        response = self._request(
            "GET", f"{endpoint}?start={start}", None, self.settings.log_bytes
        )
        text = response.body.decode("utf-8", errors="replace")
        if not text.strip():
            raise JenkinsError("Jenkins failed-build console is empty")
        return f"Untrusted Jenkins console {build} (byte offset {start}):\n{text}"

    def _payload(self, job: str, build: dict[str, Any]) -> dict[str, str]:
        info = self._json(
            f"{job}api/json",
            "property[parameterDefinitions[name,type,defaultParameterValue[value],choices]]",
        )
        definitions = [
            definition
            for prop in _objects(info.get("property", []), "job properties")
            for definition in _objects(
                prop.get("parameterDefinitions", []), "parameter definitions"
            )
        ]
        if not definitions:
            raise JenkinsError("Jenkins job has no supported parameter definitions")
        previous = {
            p["name"]: p.get("value")
            for action in _objects(build.get("actions"), "build actions")
            for p in _objects(action.get("parameters", []), "recorded parameters")
            if isinstance(p.get("name"), str)
        }
        result = {}
        for definition in definitions:
            name, kind = definition.get("name"), definition.get("type")
            if (
                not isinstance(name, str)
                or not name
                or name in result
                or not isinstance(kind, str)
                or kind
                not in {
                    "StringParameterDefinition",
                    "BooleanParameterDefinition",
                    "ChoiceParameterDefinition",
                }
            ):
                raise JenkinsError(
                    "Unsupported Jenkins parameter; configure the job manually"
                )
            default = definition.get("defaultParameterValue")
            if default is not None and not isinstance(default, dict):
                raise JenkinsError("Jenkins parameter default is malformed")
            value = previous.get(name, (default or {}).get("value"))
            if kind == "BooleanParameterDefinition":
                if type(value) is not bool:
                    raise JenkinsError("Jenkins boolean parameter is invalid")
                result[name] = str(value).lower()
            else:
                choices = definition.get("choices")
                if kind == "ChoiceParameterDefinition" and (
                    not isinstance(choices, list)
                    or any(not isinstance(choice, str) for choice in choices)
                ):
                    raise JenkinsError(
                        "Jenkins choice parameter definition is malformed"
                    )
                if not isinstance(value, str) or (
                    kind == "ChoiceParameterDefinition" and value not in choices
                ):
                    raise JenkinsError("Jenkins string/choice parameter is invalid")
                result[name] = value
        return result

    def _reconcile(
        self,
        budget: dict[str, Any],
        builds: list[tuple[str, dict[str, Any]]],
        head: str,
    ) -> None:
        seen = set(budget["builds"])
        for job, build in builds:
            if self._head(build) == head:
                seen.add(f"{job}{build['number']}/")
        budget["builds"] = sorted(seen)
        pending = budget["pending"]
        if pending and pending.get("queue"):
            queue_id = int(urlsplit(pending["queue"]).path.strip("/").split("/")[-1])
            matching = [
                build
                for job, build in builds
                if job == pending["job"]
                and build["number"] > pending["before"]
                and build.get("queueId") == queue_id
            ]
            if matching:
                actual = self._head(matching[0])
                if actual is None and matching[0]["building"]:
                    raise JenkinsBusy(
                        "Queued Jenkins build has not checked out its revision yet"
                    )
                if actual != head:
                    raise JenkinsError(
                        "Queued Jenkins build checked out another head; inspect manually"
                    )
                budget["pending"] = None

    @staticmethod
    def _budget(state: dict[str, Any], head: str) -> dict[str, Any]:
        budget = state.setdefault(head, {"builds": [], "pending": None})
        if (
            not isinstance(budget, dict)
            or not isinstance(budget.get("builds"), list)
            or any(not isinstance(build, str) for build in budget["builds"])
            or "pending" not in budget
        ):
            raise JenkinsError("Jenkins recovery budget is malformed")
        known_jobs = budget.setdefault("jobs", [])
        if not isinstance(known_jobs, list) or any(
            not isinstance(job, str) for job in known_jobs
        ):
            raise JenkinsError("Jenkins recovery job identities are malformed")
        pending = budget["pending"]
        if pending is not None and (
            not isinstance(pending, dict)
            or not isinstance(pending.get("job"), str)
            or type(pending.get("before")) is not int
            or ("queue" not in pending)
            or (pending["queue"] is not None and not isinstance(pending["queue"], str))
        ):
            raise JenkinsError("Jenkins recovery reservation is malformed")
        return budget

    def _path(self, slug: str, number: int) -> Path:
        key = hashlib.sha256(f"{slug}#{number}".encode()).hexdigest()
        return self.state_dir / f"{key}.json"

    @contextmanager
    def _state(self, slug: str, number: int) -> Iterator[dict[str, Any]]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(slug, number)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(path.read_text()) if path.exists() else {}
            except ValueError as exception:
                raise JenkinsError("Jenkins recovery state is malformed") from exception
            if not isinstance(state, dict):
                raise JenkinsError("Jenkins recovery state is malformed")
            yield state

    def _save(self, slug: str, number: int, state: dict[str, Any]) -> None:
        path = self._path(slug, number)
        encoded = json.dumps(state, sort_keys=True)
        if path.exists() and path.read_text(encoding="utf-8") == encoded:
            return
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _validate(self, url: str, *, queue: bool = False) -> None:
        parsed, origin = urlsplit(url), urlsplit(self.settings.url)
        if (
            parsed.scheme != origin.scheme
            or parsed.netloc != origin.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(part in unquote(parsed.path) for part in ("..", "\\", "//"))
            or unquote(unquote(parsed.path)) != unquote(parsed.path)
            or not (
                parsed.path.startswith("/queue/item/")
                if queue
                else any(
                    parsed.path.startswith(root) for root in self.settings.job_roots
                )
            )
        ):
            raise JenkinsError("Untrusted Jenkins URL; request rejected")

    def _json(self, url: str, tree: str) -> dict[str, Any]:
        response = self._request(
            "GET", f"{url}?{urlencode({'tree': tree})}", None, 4194304
        )
        try:
            value = json.loads(response.body)
        except (ValueError, UnicodeDecodeError) as exception:
            raise JenkinsError("Jenkins returned invalid JSON") from exception
        if not isinstance(value, dict):
            raise JenkinsError("Jenkins JSON must be an object")
        return value

    def _request(
        self, method: str, url: str, data: bytes | None, maximum: int
    ) -> Response:
        self._validate(url)
        response = self._transport(method, url, data, maximum)
        if not 200 <= response.status < 300:
            raise JenkinsError(f"Jenkins {method} returned HTTP {response.status}")
        return response

    def _http(
        self, method: str, url: str, data: bytes | None, maximum: int
    ) -> Response:
        try:
            credentials = json.loads(self.settings.credentials_file.read_text())
            user, token = credentials["jenkinsUser"], credentials["jenkinsApiToken"]
        except (KeyError, ValueError) as exception:
            raise JenkinsError("Invalid Jenkins credential file") from exception
        if (
            not isinstance(user, str)
            or not isinstance(token, str)
            or not user
            or not token
        ):
            raise JenkinsError(
                "Jenkins credentials must contain nonempty user and API token"
            )
        context = ssl.create_default_context(cafile=self.settings.ca_bundle)
        if self.settings.insecure_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        authorization = base64.b64encode(f"{user}:{token}".encode()).decode()
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
        try:
            with opener.open(request, timeout=self.settings.timeout) as response:
                body = response.read(maximum + 1)
                if len(body) > maximum:
                    raise JenkinsError("Jenkins response exceeded its byte limit")
                return Response(response.status, dict(response.headers), body)
        except HTTPError as exception:
            exception.close()
            raise JenkinsError(
                f"Jenkins {method} returned HTTP {exception.code}"
            ) from None
        except (URLError, TimeoutError, OSError) as exception:
            raise JenkinsError(
                f"Jenkins {method} transport failed ({type(exception).__name__}); "
                "submission reservations are retained"
            ) from None
