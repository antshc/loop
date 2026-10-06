"""Finds backticked `/skill-name` invocations that don't resolve to an existing skill."""

import re
from pathlib import Path

from modules.repo_consistency.discovery import find_skill_and_agent_files, find_skill_names
from modules.repo_consistency.violation import Violation

# A skill invocation uses the repository's documented ``Run `/name` skill`` or
# ``Follow `/name` skill`` form. Requiring the verb prevents filesystem paths
# such as `` `/usr` `` and `` `/wiki` `` from being treated as skill names.
_INVOCATION = re.compile(r"\b(?:Run|Follow)\s+`/([a-zA-Z][a-zA-Z0-9-]*)`")


def find_dangling_skill_invocations(repo_root: Path) -> list[Violation]:
    """Every skill invocation naming a skill that doesn't exist anywhere under `plugins/`/`skills/`."""
    known_skills = find_skill_names(repo_root)
    violations: list[Violation] = []
    for doc in find_skill_and_agent_files(repo_root):
        text = doc.read_text(encoding="utf-8")
        rel = str(doc.relative_to(repo_root))
        reported: set[str] = set()
        for match in _INVOCATION.finditer(text):
            name = match.group(1)
            if name in known_skills or name in reported:
                continue
            reported.add(name)
            violations.append(Violation(rel, f"unresolved skill invocation `/{name}`"))
    return violations
