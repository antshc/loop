# Run one fresh agent per Ticket from Python and let the agent commit it

The `dev` Workflow must deliver a Spec's Tickets with orchestration that stays deterministic and recoverable outside the model. Python selects the next actionable Ticket, starts one fresh agent run for it with no session resume, and validates the result against Git before advancing; the agent implements and verifies that Ticket and makes exactly one task commit with the subject `ccode(<initiative-id>|<ticket-number>): <message>`. The Initiative's `ccode(<initiative-id>|` commits are the per-iteration state handed to each run. Python never commits; it pushes, opens the draft pull request, and closes or labels Tickets from the agent's machine-readable report, so those state changes stay deterministic and testable against fakes.

## Considered Options

- **The agent loops over the Spec's Tickets in one session and commits each with a bare `ccode:` subject** — rejected: Ticket selection rests on model judgement, a long session accumulates context, and Python cannot validate each Ticket's commit before the next one starts.
- **Python commits once after the run from the report** — rejected: one commit mixes every Ticket the agent worked on, so a later run cannot see which Ticket a commit delivered.
- **The agent also pushes and closes Tickets through the prompt (the Sandcastle and full `/ralph:dev` shape)** — rejected: state changes then depend on model judgement, so they are not deterministic and cannot be tested against fakes.
- **Skill-driven orchestration inside the agent session (the former `/ralph:dev` skill)** — rejected: Python only starts the session, so commit, push, pull request, and Ticket outcomes are invisible to it and untestable.

## Consequences

- The `ccode(<initiative-id>|<ticket-number>): ` subject is a contract: it ties each commit to its Ticket and is how later runs and reruns read delivered work.
- Python treats a run as failed when Git does not prove the commit — HEAD unchanged, a subject not matching the Ticket, a dirty working tree, or a reported SHA other than HEAD — regardless of what the agent reports.
- The agent's report is a contract: Python can only close or label what the report states, and failures of `git`, `gh`, and the report parse are Python errors handled by workflow code.
- Work the agent leaves uncommitted is not saved.
- The agent prompt must forbid push, pull requests, Ticket changes, and selecting another Ticket; agent permissions can deny them.
