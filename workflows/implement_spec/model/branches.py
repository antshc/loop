"""implement_spec's naming of the branches a Spec is delivered on."""

from __future__ import annotations

import re

from workflows.shared.github.issues.graph import SpecGraphSnapshot


_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


def slugify(text: str) -> str:
    """Lower-case words joined by hyphens; `spec` when nothing is left."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


def feature_branch(spec: SpecGraphSnapshot) -> str | None:
    """`<version_with_underscores>_<title slug>` when the base branch carries a version, else `<title slug>`; None without a base branch."""
    base_branch = spec.base_branch
    if base_branch is None:
        return None
    slug = slugify(spec.bare_title)
    match = _VERSION.search(base_branch)
    return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"
