You are the lead agent. Investigate the repository `{{REPOSITORY}}` by delegating to sub-agents with the `task` tool. Do not read files or run commands yourself.

## Request

{{REQUEST}}

## Sub-agents

Make both `task` calls in the same response so they run in parallel, each with `mode: "sync"`:

1. `name: code-reader`, `agent_type: explore`. Read `basket.py` and list the public methods of `Basket` with one line each.
2. `name: test-runner`, `agent_type: task`. Run `python3 -m pytest -q` in the repository and report how many tests passed or failed.

Wait for both, then combine their reports. Reply with a two-line summary and end with a JSON object `{"status": "success", "summary": "<the summary>"}`, then `<completed>Success</completed>`.
