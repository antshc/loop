---
name: babysit
description: Launch a background PR babysitter for a GitHub pull request URL, continuously monitor review feedback and CI, recover merge conflicts, and stop when the PR closes or merges.
---

# Babysit a pull request

1. Take one GitHub PR URL (`https://github.com/owner/repo/pull/number`). Do not substitute a repository scan.
2. Require Python 3.14+ and install dependencies with `python3 -m pip install -r <skill-directory>/requirements.txt` in the Python environment used to launch the skill.
3. Locate the local babysit JSON config: `--config PATH`, `LOOP_BABYSIT_CONFIG`, or `.loop/pr-babysit.json` in the caller's repository. If absent, request configuration; do not guess build or test commands.
4. Run `python3 <skill-directory>/scripts/start.py PR_URL [--config ABSOLUTE_PATH]` while preserving the caller's repository as the working directory. The script resolves the generated `software_factory` package relative to its own file and launches one detached supervisor.
5. Return the process ID and log path **only after startup is confirmed**. Do not wait in the agent session.
6. The supervisor uses the generated `pr_babysit` workflow, the existing ledger and recovery semantics. A blocked PR remains monitored, but unchanged blocked work must not be repeated.
7. A merged or closed PR ends the process. SIGTERM requests graceful shutdown. A configuration or unrecoverable error stops with a log; never claim continued monitoring after an error.

## Gotchas

- Install the `loop[otel]` Python package from `requirements.txt`; the skill does not vendor the Loop library.
- The repository checkout, authenticated `gh`, agent CLI, build/test commands and optional Jenkins credentials are configured by the existing workflow settings.
- All Python files under `scripts/` are generated. Change canonical files in the Loop repository and regenerate; never edit bundled scripts directly.
- A second invocation targets a separate process; per-PR locking prevents simultaneous modifications to the same PR.
