"""Regex-based YAML frontmatter and heading extraction (no PyYAML dependency)."""

import re

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_KEY_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$")
_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Return top-level frontmatter key/value pairs, or None if the file has no frontmatter block."""
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0] in (" ", "\t", "#"):
            continue
        kv = _KEY_VALUE.match(line)
        if kv:
            fields[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
    return fields


def find_first_heading(text: str) -> str | None:
    """Return the text of the first `# ` heading, or None if the document has none."""
    match = _H1.search(text)
    return match.group(1) if match else None
