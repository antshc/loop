# Agent Client
**Type:** Architecture Pattern

## Purpose

Provide a stable application-facing client for running headless AI agents through provider CLIs while isolating orchestration code from provider-specific commands, session mechanics, flags, and output handling.

The client accepts an `AgentRequest` (the prompt), optionally continues a logical session, and returns the CLI's raw output with the exit code and the session it ran under, so Workflows depend on one stable API instead of invoking Copilot CLI or Codex directly.

## Concept

A **CliAgentClient** is bound to one `AgentProfile` (CLI, model, reasoning effort, context, extra args) and one `CliRunner`. `Agent()` returns an `AgentBuilder` that builds it, and optional wrappers (`GitAgent` for a worktree, `DockerAgent` for a container) decorate it behind the same `AgentClient` interface ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).

```text
Workflow
    |
    v
AgentBuilder.create() / Worktree.agent(profile)
    |
    v
[GitAgent] -> [DockerAgent] -> CliAgentClient
                                  |        \
                                  v         v
                             CliRunner   SessionStore
                                  |
                                  v
                       Headless AI Agent (Copilot CLI, Codex)
```

`AgentClient.run(request, context=None)` takes one `AgentRequest(prompt)` and returns an `AgentResult(output, session, exit_code)`. The model and reasoning effort belong to the profile, so Runs that need different ones use different clients; the prompt is sent as given, and rendering any template is the Workflow's job.

Sessions are off by default: without `with_session()` every run starts a fresh provider session under a generated name. With `with_session()`, a `SessionName` identifies a logical session; the client asks the `SessionStore` for the `NativeHandle` of that name and CLI and resumes it, or starts a new session, and saves the handle returned by the CLI. The provider CLI stays authoritative for the transcript; the store maps only the logical name to the CLI's native handle. `MemorySessionStore` keeps handles for the process lifetime. A handle stored for a different CLI raises `SessionCliMismatch`.

The CLI adapter (`CopilotCli`, `CodexCli`) owns command construction, session create/resume flags, and permission flags. A `ProcessCliRunner` starts the process on the host in the agent context's working directory (the worktree when a worktree is open), returns the raw stdout, and raises `CalledProcessError` on a non-zero exit. Extra directories (`--add-dir`) are passed only when the run's `AgentContext` lists them.

`AgentClient` is different from a Headless AI Agent: the Headless AI Agent is the executable worker; `AgentClient` is the orchestration-facing boundary used to invoke it and continue it when requested.

## Rules

- MUST make orchestration code depend on `AgentClient` rather than a concrete provider CLI.
- MUST expose prompt execution through a stable `run(request, context=None)` operation returning an `AgentResult`.
- MUST carry the CLI, model, reasoning effort, context, and extra args in the `AgentProfile`, and map them to provider flags inside the CLI adapter (`--model`, `--reasoning-effort` for Copilot CLI), omitting each flag when it is not given and leaving validation of the values to the provider.
- MUST send the prompt of the `AgentRequest` as given; the library MUST NOT substitute placeholders or run commands found in it.
- MUST keep provider executable names, arguments, session create/resume flags, and naming syntax inside the CLI adapter.
- MUST treat a client without `with_session()` as stateless: each run starts a fresh provider session.
- MUST resume a logical session by `SessionName` through `SessionStore` when sessions are enabled, and save the CLI's handle after each run.
- MUST keep provider conversation history owned by the provider CLI; `SessionStore` MUST NOT duplicate or interpret the transcript.
- MUST run the provider CLI on the host in the working directory of the agent context.
- MUST return the CLI's raw stdout as `AgentResult.output` and raise `CalledProcessError` on a non-zero exit; interpreting the output (such as a response envelope) belongs to the Workflow.
- MUST keep `AgentClient` agnostic of what a prompt asks for: Tickets, Specs, and identifier meaning belong to the Workflow.
- MUST make `CliRunner` the replaceable boundary for tests; there is no test double inside the public `loop` API.
- MUST NOT make Workflows construct provider CLI commands or provider session identifiers directly.

## Example

Sketch — not the implementation.

```python
builder = Agent().with_git(GitOptions(strategy=BranchStrategy("feature"))).with_session()
with builder.open() as worktree:
    planner = worktree.agent(AgentProfile(copilot, "gpt-5", "high"))
    result = planner.run(AgentRequest("Plan the change"))
    print(result.output, result.session, result.exit_code)
```

For Copilot CLI, the adapter translates the session state conceptually as:

```text
new session      -> copilot -p <prompt> --name <session-name> ...
existing session -> copilot -p <prompt> --resume=<native-handle> ...
```

## Benefits and Trade-offs

**Benefits**

- Keeps provider CLI syntax and session mechanics out of orchestration Workflows.
- One request and result contract across Copilot CLI and Codex.
- Lets Workflows address sessions with stable logical names instead of provider identifiers.
- Keeps the provider authoritative for conversation history.

**Trade-offs**

- Resuming across processes needs a durable `SessionStore`; only an in-memory one ships.
- Provider-specific session capabilities still differ and require adapter-specific handling.
- Raw stdout means every Workflow that wants structured output extracts it itself.
- No live streaming, timeout, or cancellation of the CLI process.

## Validation

A conforming implementation demonstrates both fresh and resumable execution: a Workflow can run without sessions, enable `with_session()`, resume a session by the same `SessionName`, and inspect output and exit code without constructing provider commands or native session identifiers directly.

## References

- [GitHub Copilot CLI session reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
- [SquadClient adapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/adapter/client.ts)
