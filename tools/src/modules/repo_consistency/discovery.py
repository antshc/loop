"""Discovers agent/skill documents and skill names under the repo's plugin/skill trees."""

from pathlib import Path

# Scanned roots only — excludes _backup/, _in-progress/, docs/kbs and other non-plugin folders.
SCAN_ROOTS = ("plugins", "skills")


def find_skill_and_agent_files(repo_root: Path) -> list[Path]:
    """Every SKILL.md and *.agent.md file under the repo's plugin/skill trees."""
    files: list[Path] = []
    for root_name in SCAN_ROOTS:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        files.extend(root.rglob("SKILL.md"))
        files.extend(root.rglob("*.agent.md"))
    return sorted(files)


def find_skill_names(repo_root: Path) -> set[str]:
    """Every skill name backed by a `SKILL.md` folder under `plugins/`/`skills/` (any nesting depth)."""
    names: set[str] = set()
    for root_name in SCAN_ROOTS:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for skill_md in root.rglob("SKILL.md"):
            names.add(skill_md.parent.name)
    return names

