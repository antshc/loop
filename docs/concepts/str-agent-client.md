# Agent Client
**Type:** Architecture Pattern

## Purpose

Provide a stable application-facing client for running headless AI agents through provider CLIs while isolating orchestration code from provider-specific commands, session mechanics, flags, and output handling.

The client accepts an agent prompt, the arguments that render it, and run options, optionally associates the run with a logical session, and returns the captured CLI execution output plus the agent's response as extracted by the agent kind's output parser, so Loop, Ralph, and Crew depend on one stable API instead of invoking Copilot CLI directly.

## Concept

An **AgentClient** wraps a provider-specific CLI adapter, uses a **Prompt Preprocessor** to render the prompt, and uses a **SessionStore** for resumable agent sessions. The workflow passes the AgentClient to each worktree run, and the **worktree runner** supplies the executor the CLI runs through ([ADR 0008](../adr/0008-run-agents-on-the-host-through-a-worktree-runner-and-pass-the-agent-to-each-run.md)).

```text
Loop / Ralph / Crew
          |
          v
   WorktreeRunner
          |
          v
      AgentClient
     /     |      \
    v      v       v
Prompt  SessionStore  Provider CLI Adapter
Preprocessor              |
                          v
                    Headless AI Agent
                    e.g. Copilot CLI
```

`AgentClient` exposes a provider-neutral `run(prompt, prompt_args, options)` operation. The Prompt Preprocessor first replaces `{{KEY}}` placeholders in the prompt with `prompt_args` and expands `!`cmd`` commands the template author wrote. A run without `options.session_key` starts a fresh provider invocation. A run with a session key resolves the logical session through `SessionStore`; the provider adapter then creates or resumes the corresponding provider session.

The worktree runner runs the provider CLI, and the template commands, directly on the host in the harness-root workspace. The client never builds the process itself; it runs commands through the executor its runner gave it, which tests replace.

`SessionStore` owns Loop's logical-session metadata, not the provider conversation history. It maps a logical session key to the provider-facing session reference needed for later continuation. The provider CLI remains authoritative for the actual transcript and session state.

Provider-facing session names are derived from the logical session key. The `session_name_prefix` run option, when configured, is prepended to every provider-facing session name. This lets Loop-owned sessions remain identifiable in provider session pickers without exposing provider naming rules to workflows.

The CLI adapter owns command construction, process execution, and provider-specific create/resume semantics. For Copilot CLI, a new named session is started with the provider's naming option and subsequent runs resume that same name.

`AgentClient` is different from a Headless AI Agent: the Headless AI Agent is the executable worker; `AgentClient` is the orchestration-facing boundary used to invoke it, continue it when requested, and observe its execution result.

## Rules

- MUST make orchestration code depend on `AgentClient` rather than a concrete provider CLI.
- MUST expose prompt execution through a stable `run(prompt, prompt_args, options)` operation, with the session key carried in `options`.
- MUST render the prompt through the Prompt Preprocessor before invoking the provider CLI.
- MUST substitute only `{{KEY}}` placeholders; a placeholder without a matching argument MUST fail the run, and an argument no placeholder uses SHOULD log a warning.
- MUST execute only `!`cmd`` commands written in the prompt template; text arriving through `prompt_args` MUST NOT be executed.
- MUST run the provider CLI and template commands through the executor of the owning worktree runner.
- MUST pass path-scoped permissions (`--allow-all-tools` with `--add-dir` for Copilot CLI), never the provider's allow-all flag; the adapter owns these flags ([ADR 0005](../adr/0005-run-copilot-cli-agents-from-the-harness-root-with-harness-and-workspace-isolation.md)).
- MUST treat an omitted session key as a fresh provider invocation.
- MUST resolve a supplied logical session key through `SessionStore`.
- MUST persist a new session only after its first provider invocation succeeds.
- MUST keep provider conversation history owned by the provider CLI; `SessionStore` MUST NOT duplicate or interpret the transcript.
- MUST persist enough session metadata to resume a previously recorded logical session across later AgentClient invocations.
- MUST derive provider-facing session names from the logical session key rather than requiring workflows to supply provider-native names.
- MUST prepend the configured `session_name_prefix` when creating provider-facing session names.
- MUST keep provider executable names, arguments, session creation/resume flags, and naming syntax inside the provider CLI adapter.
- MUST execute the provider CLI in the harness-root workspace of the owning worktree runner.
- MUST capture stdout, stderr, and exit code for every invocation.
- MUST stream provider output line by line through the runner's executor for live logging while the process runs, and parse it only after the process exits, through the `AgentOutputParser` of the agent kind ([ADR 0007](../adr/0007-stream-agent-output-live-and-parse-it-after-exit-with-a-per-agent-kind-output-parser.md)).
- MUST let the output parser fill `AgentResult`'s response string from the generic envelope (`identifier`, `status`, `result`) and decide success or error from `status`; the parser MUST NOT check workflow-specific fields or the expected `identifier`.
- MUST keep `AgentClient` and its output parsers agnostic of what a prompt asks for: they run a prompt and return its response; Tickets, Specs, and identifier meaning belong to the workflow.
- MUST NOT terminate the provider process early; it runs until it exits or times out.
- MUST keep test doubles in `loop.testing`; there is no dry-run agent client.
- MUST return failed CLI execution output to orchestration instead of losing stderr.
- MUST NOT make Ralph, Crew, or other orchestration workflows construct provider CLI commands or provider session identifiers directly.
- SHOULD keep the raw provider output available for diagnostics and execution logs.
- SHOULD normalize provider-specific process results into one application-facing result type.

## Example

Sketch — not the implementation.

```python
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AgentRunResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class AgentSession:
    key: str
    name: str


class SessionStore(Protocol):
    def get(self, key: str) -> AgentSession | None: ...
    def save(self, session: AgentSession) -> None: ...


@dataclass(frozen=True)
class AgentOptions:
    session_key: str | None = None
    session_name_prefix: str = ""
    model: str | None = None


class PromptPreprocessor(Protocol):
    def process(self, prompt: str, prompt_args: Mapping[str, str]) -> str: ...


class AgentCli(Protocol):
    def run(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None = None,
        resume: bool = False,
    ) -> AgentRunResult: ...


class AgentClient:
    def __init__(
        self,
        cli: AgentCli,
        preprocessor: PromptPreprocessor,
        sessions: SessionStore,
    ) -> None:
        self._cli = cli
        self._preprocessor = preprocessor
        self._sessions = sessions

    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str],
        options: AgentOptions,
    ) -> AgentRunResult:
        text = self._preprocessor.process(prompt, prompt_args)

        if options.session_key is None:
            return self._cli.run(text, options)

        session = self._sessions.get(options.session_key)

        if session is not None:
            return self._cli.run(text, options, session=session, resume=True)

        session = AgentSession(
            key=options.session_key,
            name=f"{options.session_name_prefix}{options.session_key}",
        )

        result = self._cli.run(text, options, session=session)
        if result.success:
            self._sessions.save(session)
        return result
```

With:

```python
AgentOptions(session_key="issue-42", session_name_prefix="loop-")
```

the logical session key `issue-42` becomes provider-facing session name `loop-issue-42`. The workflow continues to use only `issue-42`; the provider adapter owns how that name is created and resumed.

For Copilot CLI, the adapter translates the session state conceptually as:

```text
new session     -> copilot --name <provider-session-name> ...
existing session -> copilot --resume=<provider-session-name> ...
```

## Benefits and Trade-offs

**Benefits**

- Keeps provider CLI syntax and session mechanics out of orchestration workflows.
- Gives Ralph and Crew one stable prompt execution and continuation contract.
- Lets workflows address sessions with stable logical keys instead of provider identifiers.
- Keeps Loop-owned sessions recognizable through an optional naming prefix.
- Captures execution output for diagnostics, logging, and control flow.
- Keeps the provider authoritative for conversation history.

**Trade-offs**

- Resumable execution requires durable logical-session metadata.
- Provider-specific session capabilities still differ and require adapter-specific handling.
- A provider session may outlive Loop's local session metadata and require reconciliation outside the core contract.
- Captured output may require provider-specific parsing if structured results are needed.

## Validation

A conforming implementation demonstrates both fresh and resumable execution: orchestration can run without a session key, create a logical session with a configured name prefix, resume it by the same logical key, and inspect stdout, stderr, and exit code without constructing provider commands or provider session identifiers directly.

## References

- [GitHub Copilot CLI session reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
- [SquadClient adapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/adapter/client.ts)
