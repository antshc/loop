"""Settings as code; --harness-root, --log-dir, and --log-level are the only CLI overrides."""

from __future__ import annotations

from pathlib import Path

from loop import Hook
from workflows.platforms.work_tracking import RepositoryConfig

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
HOOKS: tuple[Hook, ...] = ()
_HARNESS_PATH = Path(__file__).resolve().parents[2]
# Single repo: this checkout is both the harness and the only target repository.
REPOSITORIES: tuple[RepositoryConfig, ...] = (
    RepositoryConfig(
        path=_HARNESS_PATH,
        owner_repo="antshc/loop",
        is_harness=True,
        worktree_root=_HARNESS_PATH / "workspace" / f"{_HARNESS_PATH.name}.worktrees",
    ),
)
MAX_TICKET_FAILURES = 2
PROMPT = Path(__file__).parent / "prompts" / "dev.md"
