"""Naming rules for feature branches, task identifiers, and commit subjects."""

from __future__ import annotations

import re

_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


def feature_branch_name(base_branch: str, title: str) -> str:
    """`<version_with_underscores>_<title slug>` when base_branch carries a version, else `<title slug>`."""
    slug = slugify(title)
    match = _VERSION.search(base_branch)
    return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"


def commit_subject_prefix(identifier: str) -> str:
    """The required prefix of a Ticket's delivering commit subject."""
    return f"ccode({identifier}): "
