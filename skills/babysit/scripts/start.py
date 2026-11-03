"""Launch one detached PR babysitter from the generated skill bundle."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from supervise import load_factory, parse_pr, pr_state, python_environment


def config_path(explicit: Path | None) -> Path:
    """Use a supplied config, an environment override or the caller's local config."""
    chosen = explicit or (
        Path(os.environ["LOOP_BABYSIT_CONFIG"])
        if "LOOP_BABYSIT_CONFIG" in os.environ
        else Path.cwd() / ".loop" / "pr-babysit.json"
    )
    path = chosen.resolve()
    if not path.is_file():
        raise ValueError(f"Missing babysit config: {path}; pass --config PATH")
    return path


def launch(url: str, config: Path) -> tuple[int, Path]:
    """Start a new process and confirm that it holds the PR's monitor lock."""
    load_factory()
    from software_factory.workflows.pr_babysit.settings import Settings

    slug, number = parse_pr(url)
    settings = Settings.load(config)
    repository = settings.repository(slug)
    if not repository.path.is_dir():
        raise ValueError(
            f"Configured repository checkout is missing: {repository.path}"
        )
    if pr_state(url) != "OPEN":
        raise ValueError(f"PR is already closed or merged: {url}")
    root = settings.state_dir / "skill-runs" / slug / str(number) / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    log = root / "babysit.log"
    ready = root / "ready"
    command = [
        sys.executable,
        str(Path(__file__).resolve().with_name("supervise.py")),
        "--pr",
        url,
        "--config",
        str(config),
        "--ready",
        str(ready),
    ]
    with log.open("ab") as output:
        process = subprocess.Popen(
            command,
            cwd=settings.state_dir,
            env=python_environment(),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (root / "pid").write_text(f"{process.pid}\n", encoding="utf-8")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError(f"Babysit exited during startup; read {log}")
        if ready.exists():
            return process.pid, log
        time.sleep(0.1)
    if process.poll() is not None:
        raise ValueError(f"Babysit exited during startup; read {log}")
    raise ValueError(f"Babysit startup not confirmed; process {process.pid}, log {log}")


def main(argv: list[str] | None = None) -> int:
    """Validate the PR and start its background monitor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", help="https://github.com/owner/repo/pull/123")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    try:
        pid, log = launch(args.pr, config_path(args.config))
    except (OSError, ValueError, subprocess.SubprocessError) as exception:
        print(f"Could not start babysit: {exception}", file=sys.stderr)
        return 1
    print(f"Babysit started: PID {pid}\nLog: {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
