"""The snapshot projection moved to the shared GitHub package; the legacy slice keeps importing it from here until the slice is removed."""

from __future__ import annotations

from software_factory.shared.github.pull_requests.babysit import babysit_snapshot

__all__ = ["babysit_snapshot"]
