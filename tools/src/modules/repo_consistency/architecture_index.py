"""Cross-checks ARCHITECTURE.md's ADR/Concept index rows against the records they link to."""

import re
from pathlib import Path

from modules.repo_consistency.frontmatter import find_first_heading, parse_frontmatter
from modules.repo_consistency.violation import Violation

_ROW = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|\s*([^|]+?)\s*\|")
_INDEX_SECTIONS = ("Architecture Decision Records", "Crosscutting Concepts")


def _section_lines(lines: list[str], heading: str) -> list[str]:
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return lines[start:end]


def find_architecture_index_violations(repo_root: Path) -> list[Violation]:
    """Every ADR/Concept index row whose id or title doesn't match the record it links to."""
    architecture_md = repo_root / "ARCHITECTURE.md"
    rel = "ARCHITECTURE.md"
    violations: list[Violation] = []
    if not architecture_md.is_file():
        return violations
    lines = architecture_md.read_text(encoding="utf-8").splitlines()
    for heading in _INDEX_SECTIONS:
        for line in _section_lines(lines, heading):
            match = _ROW.match(line)
            if not match:
                continue
            row_id, link, row_title = match.group(1), match.group(2), match.group(3).strip()
            record_path = (repo_root / link).resolve()
            if not record_path.is_file():
                violations.append(Violation(rel, f"index row [{row_id}] links to missing file {link}"))
                continue

            filename_id = record_path.stem.split("-", 1)[0]
            if filename_id != row_id:
                violations.append(
                    Violation(rel, f"index row id [{row_id}] doesn't match filename id [{filename_id}] in {link}")
                )

            record_text = record_path.read_text(encoding="utf-8")
            heading_title = find_first_heading(record_text)
            if heading_title and heading_title != row_title:
                violations.append(
                    Violation(rel, f"index row title '{row_title}' doesn't match {link}'s heading '{heading_title}'")
                )

            frontmatter = parse_frontmatter(record_text)
            if frontmatter:
                fm_id = frontmatter.get("id")
                if fm_id and fm_id != row_id:
                    violations.append(
                        Violation(rel, f"index row id [{row_id}] doesn't match {link}'s frontmatter id \"{fm_id}\"")
                    )
                fm_title = frontmatter.get("title")
                if fm_title and fm_title != row_title:
                    violations.append(
                        Violation(
                            rel, f"index row title '{row_title}' doesn't match {link}'s frontmatter title '{fm_title}'"
                        )
                    )
    return violations
