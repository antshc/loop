"""Checks that every agent/skill file carries its required frontmatter fields."""

from pathlib import Path

from modules.repo_consistency.discovery import find_skill_and_agent_files
from modules.repo_consistency.frontmatter import parse_frontmatter
from modules.repo_consistency.violation import Violation

# Per .github/instructions/agent-skills.instructions.md's Frontmatter table.
REQUIRED_FIELDS = ("name", "description")


def find_frontmatter_violations(repo_root: Path) -> list[Violation]:
    """Every SKILL.md/*.agent.md missing a frontmatter block or a required field's value."""
    violations: list[Violation] = []
    for doc in find_skill_and_agent_files(repo_root):
        text = doc.read_text(encoding="utf-8")
        rel = str(doc.relative_to(repo_root))
        fields = parse_frontmatter(text)
        if fields is None:
            violations.append(Violation(rel, "missing frontmatter block"))
            continue
        for field in REQUIRED_FIELDS:
            if not fields.get(field):
                violations.append(Violation(rel, f"missing required frontmatter field `{field}`"))
    return violations
