"""Keep one explicit pull request under maintenance until GitHub closes it."""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)/?")
LOGGER = logging.getLogger("babysit.skill")


def bundle_path() -> Path:
    """Find the generated module tree relative to this script, not the caller."""
    return Path(__file__).resolve().parent / "_bundled"


def load_factory() -> None:
    """Prefer the generated factory modules and require Loop's installed runtime."""
    bundled = str(bundle_path())
    if not bundle_path().is_dir():
        raise ValueError("Bundled factory code is missing; regenerate the skill")
    if bundled not in sys.path:
        sys.path.insert(0, bundled)
    try:
        import loop  # noqa: F401
    except ImportError as exception:
        raise ValueError(
            "Install a compatible Loop Python package first"
        ) from exception


def parse_pr(url: str) -> tuple[str, int]:
    """Return the GitHub repository and number for one exact PR URL."""
    match = PR_URL.fullmatch(url)
    if match is None:
        raise ValueError("Expected https://github.com/owner/repo/pull/number")
    return match[1], int(match[2])


def pr_state(url: str) -> str:
    """Read the actual GitHub state rather than infer closure from workflow exit codes."""
    result = subprocess.run(
        ["gh", "pr", "view", url, "--json", "state", "--jq", ".state"],
        capture_output=True,
        text=True,
        timeout=45,
        check=True,
    )
    state = result.stdout.strip()
    if state not in ("OPEN", "CLOSED", "MERGED"):
        raise ValueError(f"Unknown GitHub PR state: {state!r}")
    return state


def python_environment() -> dict[str, str]:
    """Put the generated factory first on subprocesses' import path."""
    result = os.environ.copy()
    existing = result.get("PYTHONPATH")
    result["PYTHONPATH"] = os.pathsep.join(
        (str(bundle_path()), existing) if existing else (str(bundle_path()),)
    )
    return result


def supervise(url: str, config: Path, ready: Path, stop: threading.Event) -> int:
    """Run the existing workflow, waiting through blocked work until PR closure."""
    load_factory()
    from workflows.software_factory.shared.infrastructure.ledger import Ledger
    from workflows.software_factory.shared.infrastructure.state import process_lock
    from workflows.software_factory.workflows.pr_babysit.definition import NAME
    from workflows.software_factory.workflows.pr_babysit.intake import work_of
    from workflows.software_factory.workflows.pr_babysit.settings import Settings

    slug, number = parse_pr(url)
    settings = Settings.load(config)
    settings.repository(slug)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    work = work_of(settings, slug, number)
    ledger = Ledger(settings.ledger_root)
    lock = settings.state_dir / "skill-runs" / slug / f"{number}.lock"
    with process_lock(lock):
        if pr_state(url) != "OPEN":
            LOGGER.info("PR already closed: %s", url)
            return 0
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text("ready\n", encoding="utf-8")
        LOGGER.info("Watching %s until closed or merged", url)
        while not stop.is_set():
            if pr_state(url) != "OPEN":
                LOGGER.info("PR closed: %s", url)
                return 0
            command = [
                sys.executable,
                "-m",
                "workflows.software_factory.workflows.pr_babysit",
                "--config",
                str(config),
                "--pr",
                url,
            ]
            child = subprocess.Popen(
                command, cwd=settings.state_dir, env=python_environment()
            )
            while child.poll() is None:
                if stop.wait(0.5):
                    child.terminate()
                    LOGGER.info("Waiting for active PR maintenance to stop")
                    child.wait()
                    return 130
            if stop.is_set():
                return 130
            if pr_state(url) != "OPEN":
                LOGGER.info("PR closed: %s", url)
                return 0
            status = ledger.status(NAME, work.id)
            if status is None or status.status not in ("handed_off", "held", "idle"):
                LOGGER.error(
                    "Babysit exited %s without a waiting PR state", child.returncode
                )
                return child.returncode or 1
            LOGGER.info(
                "PR remains open (%s); checking again in %ss",
                status.status,
                settings.interval,
            )
            stop.wait(settings.interval)
        return 130


def main(argv: list[str] | None = None) -> int:
    """Manage signals and report an unambiguous process exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        """Signal the supervisor to stop after its active workflow exits."""
        LOGGER.info("Received signal %s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        return supervise(args.pr, args.config.resolve(), args.ready.resolve(), stop)
    except (OSError, ValueError, subprocess.SubprocessError) as exception:
        LOGGER.error("Babysit supervisor stopped: %s", exception)
        return 1


if __name__ == "__main__":
    sys.exit(main())
