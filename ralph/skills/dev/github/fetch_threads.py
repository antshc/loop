#!/usr/bin/env python3
"""Entry point for fetching actionable review threads for a PR.

Fetches unresolved actionable threads for a given PR URL and prints them
as a JSON array to stdout. Intended as a drop-in replacement for
fetch-threads.sh used by the Copilot fix skill.

Usage:
    python3 fetch_threads.py <pr-url>

Arguments:
    pr-url  Full GitHub PR URL, e.g. https://github.com/owner/repo/pull/123.

Output:
    JSON array of actionable thread objects printed to stdout:
        [
          {
            "thread_id": "...",
            "prefix": "fix!",
            "path": "src/foo.py",
            "lines": "10-15",
            "actionable_comment": "fix!: broken null check",
            "comments": [{"author": "reviewer", "body": "fix!: broken null check"}]
          },
          ...
        ]

Exit codes:
    0 - success (may output an empty array)
    1 - usage / argument error
"""

import json
import pathlib
import re
import sys

# Add the parent of the github/ folder to sys.path so "github" is importable
# regardless of where this file lives (tools/src/modules/ or any plugin copy).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from github.features.fetch_threads.handler import fetch_threads

_USAGE = "Usage: fetch_threads.py <pr-url>"
_PR_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pull/\d+$")


def main() -> None:
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        sys.exit(1)

    pr_url = sys.argv[1]

    if not _PR_URL_RE.match(pr_url):
        print(
            f"Error: Invalid PR URL. Expected https://github.com/<owner>/<repo>/pull/<number>, got: {pr_url}",
            file=sys.stderr,
        )
        sys.exit(1)

    threads = fetch_threads(pr_url)
    print(json.dumps(threads, indent=2))


if __name__ == "__main__":
    main()
