# Loop run telemetry in Aspire

The [SQLite dashboard prototype](../dashboard/README.md) reduced to OpenTelemetry only. The plan → develop → review run (real Copilot CLI agents, or scripted fakes with `--dry`) exports spans and logs over OTLP to a standalone [Aspire dashboard](https://aspire.dev/dashboard/standalone/), and Aspire's own UI is the only viewer: sessions are traces, phases are spans. There is no custom server, UI or database.

```
run_fake.py ──OTLP/HTTP :4318──▶ Aspire dashboard (UI/API :18888)
```

## Run

> The library now offers this as an optional extension: `with_observer(otlp_observer(...))`, see [Run Observation](../../concepts/str-run-observation.md). This prototype stays a standalone demo of the full dashboard, including Workflow-owned session, phase and gate spans.

```
python -m pip install -e ".[dev]"                      # from the reporoot
cd docs/prototypes/dashboard-aspire
python -m pip install -r requirements.txt
docker compose up -d                                   # Aspire 13.2: UI :18888, OTLP :4317/:4318
python run_fake.py --dry                               # scripted fake agents, no CLI needed
python run_fake.py                                     # real Copilot agents; then open http://localhost:18888
```

Real mode (the default) needs the `copilot` CLI on `PATH`, a logged-in account and network access. It spawns one `copilot -p` per agent phase, in a throwaway git repo under `/tmp` seeded with a tiny Python basket project, and bills about five premium requests per run (one planner, two developer and two reviewer calls; model `claude-haiku-4.5` unless `--model`). Agents may write files and run `git`, `python` and `pytest`; `git push` is denied. Only the `dev` workflow is real.

In Aspire open **Traces** for the phase tree (each workflow is one trace; filter on the span name `dev` or `pr_babysitter`; a `dev` name continues with the spec title) and **Structured logs** for the start markers. Flags: `--dry`, `--workflow {dev,pr_babysitter}`, `--speed N`, `--no-rework`, `--fail-at PHASE`, `--request TEXT`, and for real mode `--model NAME`, `--agent-timeout SECONDS` and `--keep-repo`. `OTEL_EXPORTER_OTLP_ENDPOINT` overrides the default `http://localhost:4318`.

## Workflows

- `dev` (default): request → plan (a spec of 2 tickets) → one collapsible `#N title` span per ticket, run one after another. Each ticket holds develop → commit → review, and ticket 1 also goes through the failing gate, fix → commit → review. Phase numbers (`develop_1`, `review_2`) restart inside each ticket. `--fail-ticket 2` makes `--fail-at` hit the second ticket.
  In real mode the two tickets are small changes to the scratch repo, the gates are scripted to pass (no failing gate or fix), and the `commit_*` phases stay scripted fake commits (the agent is still told to commit in the scratch repo, but nothing reads it).
- `pr_babysitter`: fetch the PR, fix its unresolved review comments, commit and push, then poll with `wait_N` phases until the PR is merged. Polls that find new comments run another `fix_comments_N` → commit → `push_N` round (the script finds 2 and 1 new comments, then the merge). `--fail-at` accepts the phase names, e.g. `wait_3`.

## Telemetry contract

The session span, logs and metrics use `service.name=workflow`. Each phase exports its span and its children as the resource of its role (`loop.phase.role`), because Aspire colors spans by resource (instance id = name, so Aspire shows no suffix): `engineer` (the user who starts the run), `code` (commit, push, GitHub reads), `developer`, `reviewer` and, in the dev workflow, `planner` (agents). One trace is one session, and the trace id is the session id.

| Span | Parent | Attributes |
| --- | --- | --- |
| `{workflow}` (`dev: {spec title}`, `pr_babysitter`) | — | `loop.session.adw_name`, `.engineer`, `.request`, and in `dev` `loop.spec.title` |
| `#{n} {ticket title}` (dev only) | session | `loop.ticket.id`, `.title`, `loop.spec.title`; status ERROR if one of its phases fails |
| `{phase name}` | session, or its ticket | `loop.phase.seq`, `.name`, `.kind`, `.owner`, `.role`, `.description`, `.retries`, `.attempt`, `loop.spec.title` in `dev`, and `loop.ticket.id`, `.title` inside a ticket; status ERROR with message on fail |
| `invoke_agent {agent}` | phase | `gen_ai.operation.name`, `.provider.name`, `.agent.name`, `.request.model`, `.response.model`, `.conversation.id`, `.system_instructions`, `.input.messages`, `.output.messages`, `.usage.*`, `loop.usage.*`, `loop.agent.*` |
| `execute_tool {tool}` | agent | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `.arguments`, `.result`, `loop.tool.ok` |
| `gate {gate}` | phase | `loop.gate.name`, `.attempt`, `.passed`, `.checks` (JSON), `.violations` (JSON) |

Span events: `log` on a phase, carrying the payload as attributes, and `envelope` on an agent (`loop.envelope.*`).

The `gen_ai.*` names follow the OpenTelemetry [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (status: Development); `loop.*` attributes hold what they don't cover. `gen_ai.output.messages` is the agent's envelope.

Spans are exported only when they end. To make open work visible, the run also emits log records `loop.session.start`, `loop.phase.start` and `loop.agent.start`, each with the opening span's attributes plus `loop.span_id` and `loop.parent_span_id`.

## Metrics

Exported every 2 s and on exit, under resource `workflow`; open **Metrics** in Aspire and pick the resource.

| Metric | Type | Attributes |
| --- | --- | --- |
| `gen_ai.client.token.usage` | histogram `{token}` | `gen_ai.token.type` (`input`/`output`), `gen_ai.request.model`, `gen_ai.agent.name`, `gen_ai.provider.name`, `gen_ai.operation.name` |
| `gen_ai.client.operation.duration` | histogram `s` | as above, plus `error.type` when the gate failed |
| `loop.agent.cost` | counter `USD` | as the token metric, minus `gen_ai.token.type`; `--dry` only (the CLI reports AIU, not dollars) |
| `loop.agent.aiu` | counter `{AIU}` | as the cost counter; real mode only |
| `loop.agent.premium_requests` | counter `{request}` | as the cost counter; real mode only |
| `loop.gate.results` | counter | `loop.gate.name`, `loop.gate.passed` |
| `loop.phase.duration` | histogram `s` | `loop.phase.name`, `loop.phase.status` |

Each run is a separate resource instance, so pick the instance or use the resource's aggregate view.

## Real data

In real mode `fakeloop/copilot_agent.py` runs the CLI and reads its session log (`$COPILOT_HOME` or `~/.copilot`, `session-state/<id>/events.jsonl`) into the agent span: model, tokens (`gen_ai.usage.input_tokens` counts cache reads and writes, `loop.usage.input_tokens` does not), AIU per token type (`loop.usage.*_aiu`, per-call logs from CLI 1.0.95), premium requests, context window, and one `execute_tool` span per tool call with its real start and end. `python -m fakeloop.copilot_session <events.jsonl>` prints what it reads from any session log. How each field maps is in [copilot-cli-telemetry-extraction.md](../../experiments/copilot-cli-telemetry-extraction.md).

## Limits

- Real mode's tool spans, usage and envelope appear only after the agent's CLI process exits; the CLI reports no tool duration, so it is the gap between its start and complete events.
- Aspire keeps telemetry in memory, so sessions disappear when the container restarts.
- An envelope appears only once its agent span ends, not after each attempt.
- A phase that is still running appears in Traces only after it ends; until then only its start log is visible.
- `docker-compose.yml` uses `network_mode: bridge` so it does not need a fresh compose subnet.

## Tests

```
python -m pytest docs/prototypes/dashboard-aspire/tests     # from the reporoot; no network or Copilot needed
```
