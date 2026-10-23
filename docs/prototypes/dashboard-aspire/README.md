# Loop run telemetry in Aspire

The [SQLite dashboard prototype](../dashboard/README.md) reduced to OpenTelemetry only. The fake plan → develop → review run exports spans and logs over OTLP to a standalone [Aspire dashboard](https://aspire.dev/dashboard/standalone/), and Aspire's own UI is the only viewer: sessions are traces, phases are spans. There is no custom server, UI or database.

```
run_fake.py ──OTLP/HTTP :4318──▶ Aspire dashboard (UI/API :18888)
```

## Run

```
python -m pip install -e ".[dev]"                      # from the reporoot
cd docs/prototypes/dashboard-aspire
python -m pip install -r requirements.txt
docker compose up -d                                   # Aspire 13.2: UI :18888, OTLP :4317/:4318
python run_fake.py                                     # then open http://localhost:18888
```

In Aspire open **Traces** for the phase tree (each workflow is one trace; filter on the span name `dev` or `pr_babysitter`) and **Structured logs** for the start markers. Flags: `--workflow {dev,pr_babysitter}`, `--speed N`, `--no-rework`, `--fail-at PHASE` and `--request TEXT`. `OTEL_EXPORTER_OTLP_ENDPOINT` overrides the default `http://localhost:4318`.

## Workflows

- `dev` (default): request → plan (a spec of 2 tickets) → one collapsible `#N title` span per ticket, run one after another. Each ticket holds develop → commit → review, and ticket 1 also goes through the failing gate, fix → commit → review. Phase numbers (`develop_1`, `review_2`) restart inside each ticket. `--fail-ticket 2` makes `--fail-at` hit the second ticket.
- `pr_babysitter`: fetch the PR, fix its unresolved review comments, commit and push, then poll with `wait_N` phases until the PR is merged. Polls that find new comments run another `fix_comments_N` → commit → `push_N` round (the script finds 2 and 1 new comments, then the merge). `--fail-at` accepts the phase names, e.g. `wait_3`.

## Telemetry contract

The session span, logs and metrics use `service.name=workflow`. Each phase exports its span and its children as the resource of its role (`loop.phase.role`), because Aspire colors spans by resource (instance id = name, so Aspire shows no suffix): `engineer` (the user who starts the run), `code` (commit, push, GitHub reads), `developer`, `reviewer` and, in the dev workflow, `planner` (agents). One trace is one session, and the trace id is the session id.

| Span | Parent | Attributes |
| --- | --- | --- |
| `{workflow}` (`dev`, `pr_babysitter`) | — | `loop.session.adw_name`, `.engineer`, `.request` |
| `#{n} {ticket title}` (dev only) | session | `loop.ticket.id`, `.title`; status ERROR if one of its phases fails |
| `{phase name}` | session, or its ticket | `loop.phase.seq`, `.name`, `.kind`, `.owner`, `.role`, `.description`, `.retries`, `.attempt`, and `loop.ticket.id` inside a ticket; status ERROR with message on fail |
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
| `loop.agent.cost` | counter `USD` | as the token metric, minus `gen_ai.token.type` |
| `loop.gate.results` | counter | `loop.gate.name`, `loop.gate.passed` |
| `loop.phase.duration` | histogram `s` | `loop.phase.name`, `loop.phase.status` |

Each run is a separate resource instance, so pick the instance or use the resource's aggregate view.

## Limits

- Aspire keeps telemetry in memory, so sessions disappear when the container restarts.
- An envelope appears only once its agent span ends, not after each attempt.
- A phase that is still running appears in Traces only after it ends; until then only its start log is visible.
- `docker-compose.yml` uses `network_mode: bridge` so it does not need a fresh compose subnet.
