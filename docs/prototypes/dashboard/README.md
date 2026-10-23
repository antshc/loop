# Dashboard prototype

Runs a fake plan → develop → review workflow and shows it in the SSSF visualizer copied from eShop (`.github/skills/sssf/apps/visualizer`). No Copilot call, no real git: agents replay scripted tool calls, commits are trace events.

Only `loop.Prompt` is used from the library. The develop phase renders the live [workflows/dev/prompts/implement.md](../../../workflows/dev/prompts/implement.md); `plan.md` and `review.md` are local.

The link between the two halves is the `sssf.db` SQLite schema in `fakeloop/tracer.py`. The visualizer polls it read-only.

## Run

```
python -m pip install -e ".[dev]"      # from the reporoot
cd docs/prototypes/dashboard
python run_fake.py --speed 0           # creates data/sssf.db first; the server needs it
cd visualizer && bun install && bun run dev:all
```

Open http://localhost:4601. To watch a run live, start the dashboard first, then run `python run_fake.py` (default speed 1) in another terminal.

| Option | Effect |
| --- | --- |
| `--speed N` | Delay multiplier, `0` is instant |
| `--no-rework` | Skip the failing gate and the review fix loop |
| `--fail-at PHASE` | Raise inside `request`, `plan`, `develop_1`, `review_1`, `fix_1`, or `review_2` |
| `--request TEXT` | Engineer request shown on the session |
| `--data-dir DIR` | Where `sssf.db`, `events.jsonl`, and `sessions/` are written (the dashboard reads `--db` / `SSSF_DB`) |

## Changes from the eShop visualizer

- `server/db.ts`: default db path is `../../data/sssf.db`.
- Nothing else.
