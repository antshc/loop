from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunContext:
    repo: Path
    remote_url: str
    log_dir: Path
    dry_run: bool
    logger: logging.Logger
