"""Jenkins boundary fixtures, head binding, durable capacity and single-shot recovery."""

from __future__ import annotations

import json
import ssl
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from workflows.software_factory.infrastructure import jenkins as boundary
from workflows.software_factory.infrastructure.jenkins import (
    Jenkins,
    JenkinsBusy,
    JenkinsError,
    JenkinsSettings,
    Response,
)

ORIGIN = "https://jenkins.example.com"
JOB = f"{ORIGIN}/job/CI/job/PR-7/"
HEAD = "a" * 40
OTHER = "b" * 40


class JenkinsHost:
    """A deterministic Jenkins HTTP boundary, including the accepted/lost-response window."""

    def __init__(self) -> None:
        self.builds = [self.build(1)]
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.queued = False
        self.first = 1
        self.lose_response = False
        self.post_status = 201
        self.location = f"{ORIGIN}/queue/item/42/"
        self.log = b"java.lang.AssertionError: expected 1 got 2\n"
        self.definitions = [
            {
                "name": "RUN_TESTS",
                "type": "BooleanParameterDefinition",
                "defaultParameterValue": {"value": True},
            },
            {
                "name": "IMAGE",
                "type": "ChoiceParameterDefinition",
                "defaultParameterValue": {"value": "test"},
                "choices": ["test", "production"],
            },
        ]

    @staticmethod
    def build(
        number: int,
        *,
        head: str = HEAD,
        result: str = "FAILURE",
        building: bool = False,
        queue: int = 0,
    ) -> dict:
        """A PR revision plus unrelated checkout metadata, deliberately ordered first."""
        return {
            "number": number,
            "result": result,
            "building": building,
            "queueId": queue,
            "actions": [
                {"lastBuiltRevision": {"SHA1": OTHER}},
                {"revision": {"pullHash": head}},
                {"remoteUrls": ["git@github.com:a/b.git"]},
                {
                    "parameters": [
                        {"name": "RUN_TESTS", "value": False},
                        {"name": "IMAGE", "value": "test"},
                    ]
                },
            ],
        }

    def __call__(
        self, method: str, url: str, data: bytes | None, maximum: int
    ) -> Response:
        self.calls.append((method, url, data))
        if method == "POST":
            if self.lose_response:
                raise JenkinsError("trigger response lost")
            return Response(self.post_status, {"location": self.location}, b"")
        if method == "HEAD":
            return Response(200, {"x-text-size": str(len(self.log))}, b"")
        if "progressiveText" in url:
            start = int(parse_qs(urlsplit(url).query)["start"][0])
            return Response(200, {}, self.log[start : start + maximum])
        tree = parse_qs(urlsplit(url).query).get("tree", [""])[0]
        if "parameterDefinitions" in tree:
            body = {"property": [{"parameterDefinitions": self.definitions}]}
        else:
            body = {
                "builds": deepcopy(self.builds),
                "firstBuild": {"number": self.first},
                "inQueue": self.queued,
            }
        return Response(200, {}, json.dumps(body).encode())

    def posts(self) -> list[tuple[str, str, bytes | None]]:
        """Every actual trigger request observed by this fake server."""
        return [call for call in self.calls if call[0] == "POST"]


def client(tmp_path: Path, host: JenkinsHost) -> Jenkins:
    """A client whose unused credential reference proves transport injection is offline."""
    settings = JenkinsSettings(ORIGIN, ("/job/CI/",), tmp_path / "nonexistent.user")
    return Jenkins(settings, tmp_path / "recovery", transport=host)


def failed(client: Jenkins, head: str = HEAD):
    """Observe the selected head's actionable failed build."""
    return client.inspect("a/b", 7, head, (JOB,))[0]


def test_count_initial_build_and_two_restarts_but_never_a_fourth(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    for number in (1, 2):
        failure = failed(jenkins)
        assert failure.attempts == number
        assert jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,)) == host.location
        host.builds.append(host.build(number + 1, queue=42))
    with pytest.raises(JenkinsError, match="exhausted 3 total"):
        failed(jenkins)
    assert len(host.posts()) == 2
    assert parse_qs(host.posts()[0][2].decode()) == {
        "RUN_TESTS": ["false"],
        "IMAGE": ["test"],
    }


def test_external_builds_also_consume_capacity(tmp_path: Path) -> None:
    host = JenkinsHost()
    host.builds = [host.build(n) for n in (1, 2, 3)]
    with pytest.raises(JenkinsError, match="exhausted 3 total"):
        failed(client(tmp_path, host))
    assert not host.posts()


def test_lost_response_and_reenabled_client_never_blindly_submit(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    failure = failed(jenkins)
    host.lose_response = True
    with pytest.raises(JenkinsError, match="lost"):
        jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,))
    restored = client(tmp_path, host)
    with pytest.raises(JenkinsError, match="unresolved"):
        failed(restored)
    with pytest.raises(JenkinsError, match="needs reconciliation"):
        restored.restart("a/b", 7, failure, lambda: HEAD, (JOB,))
    assert len(host.posts()) == 1


def test_queue_reservation_waits_then_reconciles_to_build(tmp_path: Path) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    jenkins.restart("a/b", 7, failed(jenkins), lambda: HEAD, (JOB,))
    host.queued = True
    with pytest.raises(JenkinsBusy):
        failed(jenkins)
    host.queued = False
    host.builds.append(host.build(2, queue=42))
    assert failed(client(tmp_path, host)).attempts == 2
    assert len(host.posts()) == 1


def test_new_head_gets_capacity_but_returning_to_old_head_keeps_history(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    host.builds = [host.build(n) for n in (1, 2, 3)]
    jenkins = client(tmp_path, host)
    with pytest.raises(JenkinsError, match="exhausted"):
        failed(jenkins)
    host.builds.append(host.build(4, head=OTHER))
    assert failed(jenkins, OTHER).attempts == 1
    host.builds.append(host.build(5))
    with pytest.raises(JenkinsError, match="exhausted"):
        failed(jenkins)


@pytest.mark.parametrize("change", ["head", "new-build", "running", "queued"])
def test_refresh_before_restart_prevents_stale_or_duplicate_submission(
    tmp_path: Path, change: str
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    failure = failed(jenkins)
    if change == "head":
        with pytest.raises(JenkinsError, match="head changed"):
            jenkins.restart("a/b", 7, failure, lambda: OTHER, (JOB,))
    else:
        if change == "new-build":
            host.builds.append(host.build(2))
        elif change == "running":
            host.builds[0]["building"] = True
        else:
            host.queued = True
        if change == "queued":
            with pytest.raises(JenkinsBusy):
                jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,))
        else:
            assert jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,)) is None
    assert not host.posts()


@pytest.mark.parametrize(
    "change, message",
    [
        ("retained", "incomplete"),
        ("gap", "incomplete"),
        ("no-head", "revision"),
        ("wrong-repo", "repository"),
        ("ambiguous-head", "revision"),
        ("stale", "another PR head"),
        ("not-built", "manual inspection"),
    ],
)
def test_uncertain_or_unrelated_history_never_mints_capacity(
    tmp_path: Path, change: str, message: str
) -> None:
    host = JenkinsHost()
    if change == "retained":
        host.first = 2
    elif change == "gap":
        host.builds.append(host.build(3))
    elif change == "no-head":
        host.builds[0]["actions"] = []
    elif change == "wrong-repo":
        host.builds[0]["actions"][2]["remoteUrls"] = ["https://github.com/x/y.git"]
    elif change == "ambiguous-head":
        host.builds[0]["actions"].append({"revision": {"pullHash": OTHER}})
    elif change == "stale":
        host.builds[0] = host.build(1, head=OTHER)
    else:
        host.builds[0]["result"] = "NOT_BUILT"
    with pytest.raises(JenkinsError, match=message):
        failed(client(tmp_path, host))
    assert not host.posts()


def test_console_tail_is_byte_bounded_and_labels_untrusted_evidence(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    host.log = b"x" * 5000 + b"\nFAIL: test_real_defect\n"
    jenkins = client(tmp_path, host)
    jenkins.settings = replace(jenkins.settings, log_bytes=1024)
    failure = failed(jenkins)
    assert "Untrusted Jenkins console" in failure.logs
    assert "FAIL: test_real_defect" in failure.logs
    assert len(failure.logs) < 1200
    assert any(f"start={len(host.log) - 1024}" in url for _, url, _ in host.calls)


@pytest.mark.parametrize("kind", ["choice", "boolean", "secret"])
def test_invalid_or_sensitive_parameter_definitions_prevent_submission(
    tmp_path: Path, kind: str
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    failure = failed(jenkins)
    if kind == "choice":
        host.definitions[1]["choices"] = ["not-selected"]
    elif kind == "boolean":
        host.builds[0]["actions"][3]["parameters"][0]["value"] = "false"
    else:
        host.definitions[0]["type"] = "PasswordParameterDefinition"
    with pytest.raises(JenkinsError, match="parameter"):
        jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,))
    assert not host.posts()


def test_invalid_queue_response_retains_reservation(tmp_path: Path) -> None:
    host = JenkinsHost()
    host.location = "https://evil.example/queue/item/42/"
    jenkins = client(tmp_path, host)
    with pytest.raises(JenkinsError, match="Untrusted"):
        jenkins.restart("a/b", 7, failed(jenkins), lambda: HEAD, (JOB,))
    with pytest.raises(JenkinsError, match="unresolved"):
        failed(client(tmp_path, host))
    assert len(host.posts()) == 1


def snapshot(url: str = f"{JOB}1/display/redirect") -> dict:
    """A failed current-head Jenkins status without an Actions run."""
    return {
        "statuses": [
            {"id": 1, "context": "jenkins/pr-head", "state": "error", "target_url": url}
        ],
        "checks": [],
        "runs": [],
    }


def test_classic_and_blue_ocean_links_resolve_only_the_watched_pr(
    tmp_path: Path,
) -> None:
    jenkins = client(tmp_path, JenkinsHost())
    assert jenkins.failed_jobs(snapshot(), 7) == (JOB,)
    blue = f"{ORIGIN}/blue/organizations/jenkins/CI/detail/PR-7/1/pipeline"
    assert jenkins.failed_jobs(snapshot(blue), 7) == (JOB,)
    with pytest.raises(JenkinsError, match="this PR job"):
        jenkins.failed_jobs(snapshot(f"{ORIGIN}/job/CI/job/PR-8/1/"), 7)
    with pytest.raises(JenkinsError, match="Untrusted"):
        jenkins.failed_jobs(snapshot(f"{ORIGIN}/job/OTHER/job/PR-7/1/"), 7)


def test_latest_status_wins_and_external_origin_is_not_contacted(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    value = snapshot()
    value["statuses"].append({**value["statuses"][0], "id": 2, "state": "success"})
    assert jenkins.failed_jobs(value, 7) == ()
    assert (
        jenkins.failed_jobs(snapshot("https://evil.example/job/CI/job/PR-7/1/"), 7)
        == ()
    )
    assert not host.calls


def test_queued_job_identity_is_retained_when_github_status_disappears(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    host.queued = True
    jenkins = client(tmp_path, host)
    assert jenkins.readiness("a/b", 7, HEAD, snapshot()) == "waiting"
    empty = {"statuses": [], "checks": [], "runs": []}
    restored = client(tmp_path, host)
    assert restored.jobs_for("a/b", 7, empty) == (JOB,)
    host.queued = False
    assert restored.readiness("a/b", 7, HEAD, empty) == "failed"


def test_settings_are_disabled_without_credential_resolution(tmp_path: Path) -> None:
    assert JenkinsSettings.load(None, tmp_path) is None
    assert (
        JenkinsSettings.load({"enabled": False, "credentials_file": 42}, tmp_path)
        is None
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": "false"},
        {"url": "http://jenkins.example.com"},
        {"url": "https://user:token@jenkins.example.com"},
        {"url": 42},
        {"job_roots": ["/job/CI/../"]},
        {"job_roots": []},
        {"insecure_tls": "false"},
        {"timeout": True},
        {"timeout": float("inf")},
        {"log_bytes": 1},
        {"log_bytes": 1024.5},
        {"credentials_file": ""},
        {"insecure_tls": True, "ca_bundle": "ca.pem"},
    ],
)
def test_jenkins_configuration_rejects_unsafe_coercions(
    tmp_path: Path, overrides: dict
) -> None:
    value = {
        "enabled": True,
        "url": ORIGIN,
        "job_roots": ["/job/CI/"],
        "credentials_file": "jenkins.json.user",
    }
    with pytest.raises(ValueError):
        JenkinsSettings.load({**value, **overrides}, tmp_path)


def test_configured_ca_paths_and_explicit_insecure_warning(
    tmp_path: Path, caplog
) -> None:
    value = {
        "enabled": True,
        "url": ORIGIN,
        "job_roots": ["/job/CI/"],
        "credentials_file": "jenkins.json.user",
        "ca_bundle": "ca.pem",
    }
    settings = JenkinsSettings.load(value, tmp_path)
    assert settings is not None
    assert settings.ca_bundle == tmp_path / "ca.pem"
    assert not settings.insecure_tls
    Jenkins(replace(settings, ca_bundle=None, insecure_tls=True), tmp_path)
    assert "explicitly disabled" in caplog.text


def test_workers_sharing_state_admit_only_one_submission(tmp_path: Path) -> None:
    host = JenkinsHost()
    first, second = client(tmp_path, host), client(tmp_path, host)
    failure = failed(first)
    barrier = Barrier(2)

    def submit(jenkins: Jenkins) -> str:
        barrier.wait()
        try:
            return jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,)) or "wait"
        except JenkinsError:
            return "held"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (first, second)))
    assert sorted(results) == sorted([host.location, "held"])
    assert len(host.posts()) == 1


def test_interrupted_reservation_before_post_does_not_allow_a_later_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    failure = failed(jenkins)
    request = jenkins._request

    def crash(method, url, data, maximum):
        if method == "POST":
            raise RuntimeError("interrupted before dispatch")
        return request(method, url, data, maximum)

    monkeypatch.setattr(jenkins, "_request", crash)
    with pytest.raises(RuntimeError, match="interrupted"):
        jenkins.restart("a/b", 7, failure, lambda: HEAD, (JOB,))
    with pytest.raises(JenkinsError, match="unresolved"):
        failed(client(tmp_path, host))
    assert not host.posts()


def test_queued_build_with_changed_checkout_is_not_attributed_to_old_head(
    tmp_path: Path,
) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    jenkins.restart("a/b", 7, failed(jenkins), lambda: HEAD, (JOB,))
    host.builds.append(host.build(2, head=OTHER, queue=42))
    with pytest.raises(JenkinsError, match="another head"):
        failed(client(tmp_path, host))
    assert len(host.posts()) == 1


def test_multiple_jobs_share_the_same_head_budget(tmp_path: Path) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    second_job = f"{ORIGIN}/job/CI/job/extra/job/PR-7/"
    failures = jenkins.inspect("a/b", 7, HEAD, (JOB, second_job))
    assert len(failures) == 2 and {f.attempts for f in failures} == {2}
    host.builds.append(host.build(2))
    with pytest.raises(JenkinsError, match="exhausted"):
        jenkins.inspect("a/b", 7, HEAD, (JOB, second_job))
    assert not host.posts()


@pytest.mark.parametrize(
    "url",
    [
        f"{ORIGIN}/job/CI/../job/PR-7/",
        f"{ORIGIN}/job/CI/%252e%252e/job/PR-7/",
        f"{ORIGIN}/job/CI/job/PR-7/1/?token=unexpected",
        "http://jenkins.example.com/job/CI/job/PR-7/1/",
    ],
)
def test_untrusted_check_urls_are_rejected_before_credentials_or_network(
    tmp_path: Path, url: str
) -> None:
    host = JenkinsHost()
    with pytest.raises(JenkinsError, match="Untrusted"):
        client(tmp_path, host).failed_jobs(snapshot(url), 7)
    assert not host.calls


@pytest.mark.parametrize("kind", ["json", "actions", "history", "state"])
def test_malformed_external_data_holds_explicitly(tmp_path: Path, kind: str) -> None:
    host = JenkinsHost()
    jenkins = client(tmp_path, host)
    if kind == "json":
        jenkins._transport = lambda *args: Response(200, {}, b"{bad")
    elif kind == "actions":
        host.builds[0]["actions"] = [None]
    elif kind == "history":
        host.builds = [None]
    else:
        jenkins.state_dir.mkdir()
        jenkins._path("a/b", 7).write_text('{"' + HEAD + '":{"builds":false}}')
    with pytest.raises(JenkinsError):
        failed(jenkins)
    assert not host.posts()


@pytest.mark.parametrize("insecure", [False, True])
def test_real_transport_tls_is_scoped_and_redirects_are_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, insecure: bool
) -> None:
    credentials = tmp_path / "test.user"
    credentials.write_text(
        json.dumps({"jenkinsUser": "test", "jenkinsApiToken": "fake-token"})
    )
    settings = JenkinsSettings(
        ORIGIN, ("/job/CI/",), credentials, insecure_tls=insecure
    )
    contexts = []

    class HttpResponse(BytesIO):
        status = 200
        headers = {}

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Authorization").startswith("Basic ")
            assert timeout == settings.timeout
            return HttpResponse(b'{"ok":true}')

    def opener(*handlers):
        redirects, https = handlers
        assert (
            redirects.redirect_request(None, None, 302, "", {}, "https://evil.example")
            is None
        )
        contexts.append(https._context)
        return Opener()

    monkeypatch.setattr(boundary, "build_opener", opener)
    result = Jenkins(settings, tmp_path / "state")._json(f"{JOB}api/json", "ok")
    assert result == {"ok": True}
    assert contexts[0].verify_mode == (ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED)
    assert contexts[0].check_hostname is not insecure


@pytest.mark.parametrize("kind", ["http", "transport", "oversize"])
def test_real_transport_sanitizes_errors_and_enforces_response_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    credentials = tmp_path / "test.user"
    credentials.write_text(
        json.dumps({"jenkinsUser": "test", "jenkinsApiToken": "fake-token"})
    )
    settings = JenkinsSettings(ORIGIN, ("/job/CI/",), credentials)

    class HttpResponse(BytesIO):
        status = 200
        headers = {}

    class Opener:
        def open(self, request, timeout):
            if kind == "http":
                raise HTTPError(request.full_url, 403, "sensitive response", {}, None)
            if kind == "transport":
                raise URLError("sensitive exception")
            return HttpResponse(b"x" * 20)

    monkeypatch.setattr(boundary, "build_opener", lambda *args: Opener())
    with pytest.raises(JenkinsError) as caught:
        Jenkins(settings, tmp_path / "state")._request(
            "GET", f"{JOB}api/json", None, 10
        )
    assert "sensitive" not in str(caught.value)
    assert "fake-token" not in str(caught.value)
