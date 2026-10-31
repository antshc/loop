"""Optional Jenkins recovery through the real babysit definition and publication gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loop import AgentRequest, FakeReply, RunContext
from workflows.software_factory.events.jenkins import JenkinsRestarted
from workflows.software_factory.events.pull_requests import ReadinessReported
from workflows.software_factory.infrastructure.jenkins import Jenkins, JenkinsSettings
from workflows.software_factory.workflows.pr_babysit.__main__ import (
    StopRequest,
    build_services,
)
from workflows.software_factory.workflows.pr_babysit.definition import pr_babysit
from workflows.software_factory.workflows.pr_babysit.settings import Settings
from workflows.tests.software_factory.infrastructure.github.fakes import commit_status
from workflows.tests.software_factory.infrastructure.test_jenkins import (
    JOB,
    ORIGIN,
    JenkinsHost,
)
from workflows.tests.software_factory.stages.pr_support import SLUG
from workflows.tests.software_factory.stages.watch_support import MaintainerAgent

from .harness import FAILS_UNTIL_FIXED, Babysit


class JenkinsMaintainer(MaintainerAgent):
    """Return a structured Jenkins diagnosis and reuse the real scripted fix behavior."""

    def __init__(self, category: str, classification: str = "transient") -> None:
        super().__init__(
            classification, "test_case failed; inspect assertion in cart.py"
        )
        self.category = category

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        payload = json.loads(request.prompt.rsplit("\n", 1)[-1])
        if payload["mode"] == "diagnose":
            self.requests.append(payload)
            assert payload["provider"] == "jenkins"
            return FakeReply(
                json.dumps(
                    {
                        "classification": self.classification,
                        "category": self.category,
                        "reason": self.reason,
                    }
                )
            )
        return super().__call__(request, context)


def enabled(
    tmp_path: Path,
    category: str = "timeout",
    classification: str = "transient",
    **options,
) -> tuple[Babysit, JenkinsHost, Jenkins]:
    """A watched PR, fake Jenkins boundary and enabled global configuration."""
    rig = Babysit(tmp_path, **options)
    value = json.loads(rig.config.read_text())
    value["jenkins"] = {
        "enabled": True,
        "url": ORIGIN,
        "job_roots": ["/job/CI/"],
        "credentials_file": "unused.user",
    }
    rig.config.write_text(json.dumps(value))
    rig.agent.maintainer = JenkinsMaintainer(category, classification)
    host = JenkinsHost()
    host.builds = [host.build(1, head=rig.head)]
    host.builds[0]["actions"][2]["remoteUrls"] = [f"https://github.com/{SLUG}.git"]
    rig.pull.statuses = lambda: [
        {
            **commit_status(1, "continuous-integration/jenkins/pr-head", state="error"),
            "target_url": f"{JOB}{len(host.builds)}/display/redirect",
        }
    ]
    settings = Settings.load(rig.config)
    assert settings.jenkins is not None
    jenkins = Jenkins(settings.jenkins, tmp_path / "jenkins", transport=host)
    rig.services = rig.services.bind(Jenkins, jenkins)
    return rig, host, jenkins


@pytest.mark.parametrize(
    "category", ["timeout", "flaky_test", "container_error", "likely_transient"]
)
def test_agent_classified_transient_failure_restarts_jenkins_not_actions(
    tmp_path: Path, category: str
) -> None:
    rig, host, _ = enabled(tmp_path, category)
    assert rig.babysit() == 0
    assert rig.agent.modes == ["diagnose"]
    assert len(host.posts()) == 1
    assert rig.host.retries == [] and not rig.pushed()
    assert rig.history(JenkinsRestarted)[0].queue == host.location
    assert "Untrusted Jenkins console" in rig.agent.maintainer.requests[0]["logs"]


def test_initial_build_and_two_restarts_then_handoff_on_third_failure(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path)

    def on_wait(seconds: float) -> None:
        queue = int(host.location.strip("/").split("/")[-1])
        build = host.build(len(host.builds) + 1, head=rig.head, queue=queue)
        build["actions"][2]["remoteUrls"] = [f"https://github.com/{SLUG}.git"]
        host.builds.append(build)
        host.location = f"{ORIGIN}/queue/item/{queue + 1}/"

    rig.on_wait = on_wait
    assert rig.babysit() == 1
    assert "exhausted 3 total" in rig.status().reason
    assert len(host.posts()) == 2
    assert rig.agent.modes == ["diagnose", "diagnose"]


def test_genuine_test_failure_is_verified_committed_and_published_without_restart(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path, "test_failure", "branch", build=FAILS_UNTIL_FIXED)
    assert rig.babysit() == 0
    assert rig.agent.modes == ["diagnose", "fix"]
    assert rig.commits() == ["babysit: fix CI (PR #7)"] and rig.pushed()
    assert not host.posts()
    assert rig.agent.maintainer.requests[1]["diagnosis"]["reason"].startswith(
        "Jenkins "
    )


def test_failed_fix_verification_retains_work_without_publication(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(
        tmp_path, "test_failure", "branch", build="raise SystemExit(1)"
    )
    assert rig.babysit() == 1
    assert not rig.pushed() and not host.posts()
    assert (rig.worktree() / "fix.txt").exists()


def test_published_fix_waits_for_automatic_new_head_build_before_readiness(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path, "test_failure", "branch")
    statuses = rig.pull.statuses
    rig.pull.statuses = lambda: [] if rig.pushed() else statuses()
    rig.close_after(2)
    assert rig.babysit() == 0
    assert rig.pushed() and rig.agent.modes == ["diagnose", "fix"]
    assert not rig.history(ReadinessReported)
    assert not host.posts()


def test_tracked_new_head_failure_is_recovered_even_before_github_status_arrives(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path, "test_failure", "branch")
    statuses = rig.pull.statuses
    rig.pull.statuses = lambda: [] if rig.pushed() else statuses()

    def on_wait(seconds: float) -> None:
        rig.waits.append(seconds)
        if len(rig.waits) == 1:
            build = host.build(2, head=rig.head_now())
            build["actions"][2]["remoteUrls"] = [f"https://github.com/{SLUG}.git"]
            host.builds.append(build)
            rig.agent.maintainer = JenkinsMaintainer("flaky_test")
        else:
            rig.pull.close()

    rig.on_wait = on_wait
    assert rig.babysit() == 0
    assert rig.agent.modes == ["diagnose", "fix", "diagnose"]
    assert len(host.posts()) == 1
    assert not rig.history(ReadinessReported)


def test_unknown_or_inconsistent_diagnosis_hands_off_without_trigger(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path, "unknown", "blocked")
    assert rig.babysit() == 1 and "unknown" in rig.status().reason
    assert not host.posts()


def test_aborted_pipeline_timeout_is_diagnosed_and_restarted(tmp_path: Path) -> None:
    rig, host, _ = enabled(tmp_path, "timeout")
    host.builds[0]["result"] = "ABORTED"
    host.log = b"Timeout has been exceeded; cancelling nested steps\n"
    assert rig.babysit() == 0
    assert rig.agent.maintainer.requests[0]["result"] == "ABORTED"
    assert len(host.posts()) == 1


def test_manual_abort_is_diagnosed_but_not_automatically_restarted(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path, "unknown", "blocked")
    host.builds[0]["result"] = "ABORTED"
    host.log = b"Aborted by operator\n"
    assert rig.babysit() == 1
    assert rig.agent.modes == ["diagnose"] and not host.posts()


@pytest.mark.parametrize(
    "reply",
    [
        {
            "classification": "transient",
            "category": "test_failure",
            "reason": "assertion",
        },
        {"classification": "transient", "category": [], "reason": "malformed"},
        {"classification": "transient", "category": "timeout", "reason": ""},
        {"classification": "transient", "category": "timeout", "reason": 42},
    ],
)
def test_invalid_jenkins_classifier_reply_cannot_authorize_restart(
    tmp_path: Path, reply: dict
) -> None:
    rig, host, _ = enabled(tmp_path)

    class InvalidMaintainer(JenkinsMaintainer):
        def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
            return FakeReply(json.dumps(reply))

    rig.agent.maintainer = InvalidMaintainer("timeout")
    assert rig.babysit() == 1
    assert not host.posts() and not rig.pushed()


def test_diagnosis_is_enforced_read_only_before_any_jenkins_trigger(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path)

    class EditingMaintainer(JenkinsMaintainer):
        def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
            (context.agent.cwd / "unapproved.txt").write_text("edited during diagnosis")
            return super().__call__(request, context)

    rig.agent.maintainer = EditingMaintainer("timeout")
    assert rig.babysit() == 1
    assert not host.posts() and not rig.pushed()


def test_noop_fix_does_not_publish_or_create_a_fresh_budget(tmp_path: Path) -> None:
    rig, host, _ = enabled(tmp_path, "test_failure", "branch")
    rig.agent.maintainer.fix = lambda _: {"verdict": "fixed", "reason": "no edits"}
    assert rig.babysit() == 1
    assert "no changes" in rig.status().reason
    assert not rig.pushed() and not host.posts()


def test_lost_trigger_response_hands_off_and_preserves_no_resend_on_retry(
    tmp_path: Path,
) -> None:
    rig, host, jenkins = enabled(tmp_path)
    host.lose_response = True
    assert rig.babysit() == 1
    assert len(host.posts()) == 1
    rig.ledger.release("pr_babysit", rig.pr.id, "explicit retry")
    rig.restart()
    rig.services = rig.services.bind(Jenkins, jenkins)
    assert rig.babysit() == 1
    assert len(host.posts()) == 1


def test_queued_and_running_jobs_wait_without_diagnosing_or_triggering(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path)
    host.queued = True
    assert rig.babysit() == 0
    assert not host.posts() and rig.agent.modes == []


def test_job_queued_during_diagnosis_does_not_cache_a_skipped_restart(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path)

    class QueuingMaintainer(JenkinsMaintainer):
        def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
            host.queued = True
            return super().__call__(request, context)

    rig.agent.maintainer = QueuingMaintainer("timeout")

    def on_wait(seconds: float) -> None:
        rig.waits.append(seconds)
        host.queued = False
        if len(rig.waits) == 2:
            rig.pull.close()

    rig.on_wait = on_wait
    assert rig.babysit() == 0
    assert rig.agent.modes == ["diagnose"]
    assert len(host.posts()) == 1


def test_stale_red_jenkins_does_not_prevent_actions_recovery(tmp_path: Path) -> None:
    rig, host, _ = enabled(tmp_path)
    host.builds[0]["result"] = "SUCCESS"
    rig.fail_ci()
    rig.agent.maintainer = MaintainerAgent("transient")
    assert rig.babysit() == 0
    assert rig.host.retries == [10] and not host.posts()


def test_disabled_configuration_needs_no_client_and_does_not_contact_jenkins(
    tmp_path: Path,
) -> None:
    rig, host, _ = enabled(tmp_path)
    value = json.loads(rig.config.read_text())
    value["jenkins"] = {"enabled": False, "credentials_file": "does-not-exist.user"}
    rig.config.write_text(json.dumps(value))
    rig.restart()
    rig.agent.maintainer = MaintainerAgent("blocked")
    assert rig.babysit() == 1
    assert not host.calls
    assert not rig.pushed()


def test_enabled_definition_is_valid_and_service_is_composed_only_when_enabled(
    tmp_path: Path,
) -> None:
    rig, _, _ = enabled(tmp_path)
    settings = Settings.load(rig.config)
    workflow = pr_babysit(settings, lambda _: None)
    workflow.check()
    assert Jenkins in workflow.services()
    assert Jenkins not in build_services(None, StopRequest()).types()
    assert isinstance(settings.jenkins, JenkinsSettings)
