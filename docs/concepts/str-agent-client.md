# Agent Client
**Type:** Architecture Pattern

## Purpose

Provide a stable application-facing client for running headless AI agents through provider CLIs while isolating orchestration code from provider-specific commands, flags, and output handling.

The client accepts an agent prompt and returns captured CLI execution output so Shipyard, Ralph, and Crew depend on one stable API instead of invoking Copilot CLI directly.

## Concept

An **AgentClient** wraps a provider-specific CLI adapter.

```text
Shipyard / Ralph / Crew
          |
          v
      AgentClient
          |
          v
   Provider CLI Adapter
          |
          v
     Headless AI Agent
     e.g. Copilot CLI
```

The CLI adapter owns command construction and process execution. `AgentClient` exposes a provider-neutral `run(prompt)` operation and returns the captured stdout, stderr, and exit code.

There is no application-level agent session. Each invocation is a CLI process execution. Any provider-specific session behavior remains internal to the provider CLI and is not part of the `AgentClient` contract.

`AgentClient` is different from a Headless AI Agent: the Headless AI Agent is the executable worker; `AgentClient` is the orchestration-facing boundary used to invoke it and observe its execution result.

## Rules

- MUST make orchestration code depend on `AgentClient` rather than a concrete provider CLI.
- MUST expose prompt execution through a stable `run(prompt)` operation.
- MUST keep provider executable names, arguments, and flags inside the provider CLI adapter.
- MUST execute the provider CLI in the configured working directory.
- MUST capture stdout, stderr, and exit code for every invocation.
- MUST return failed CLI execution output to orchestration instead of losing stderr.
- MUST NOT expose provider session creation or resume operations through the application-facing contract.
- MUST NOT make Ralph, Crew, or other orchestration workflows construct provider CLI commands directly.
- SHOULD keep the raw provider output available for diagnostics and execution logs.
- SHOULD normalize provider-specific process results into one application-facing result type.

## Example

A Python `AgentClient` using a Copilot CLI adapter:

```python
from dataclasses import dataclass
import subprocess
from typing import Protocol


@dataclass(frozen=True)
class AgentRunResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class AgentCli(Protocol):
    def run(self, prompt: str) -> AgentRunResult: ...


@dataclass(frozen=True)
class CopilotCliOptions:
    working_directory: str
    executable: str = "copilot"
    agent: str | None = None
    model: str | None = None


class CopilotCliAdapter:
    def __init__(self, options: CopilotCliOptions) -> None:
        self._options = options

    def run(self, prompt: str) -> AgentRunResult:
        command = [
            self._options.executable,
            "-p",
            prompt,
            "--no-ask-user",
        ]

        if self._options.agent:
            command += ["--agent", self._options.agent]

        if self._options.model:
            command += ["--model", self._options.model]

        result = subprocess.run(
            command,
            cwd=self._options.working_directory,
            capture_output=True,
            text=True,
            check=False,
        )

        return AgentRunResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )


class AgentClient:
    def __init__(self, cli: AgentCli) -> None:
        self._cli = cli

    def run(self, prompt: str) -> AgentRunResult:
        return self._cli.run(prompt)
```

Orchestration uses only the stable client:

```python
client = AgentClient(
    CopilotCliAdapter(
        CopilotCliOptions(
            working_directory="/workspace/repo",
            agent="codey",
        )
    )
)

result = client.run("Implement issue #42 and verify the result.")

if not result.succeeded:
    raise RuntimeError(result.stderr)

print(result.stdout)
```

The provider adapter effectively runs:

```bash
copilot -p "Implement issue #42 and verify the result." \
  --no-ask-user \
  --agent codey
```

The output-capture pattern follows the repository's existing CLI wrappers: `subprocess.run(..., capture_output=True, text=True)`, then consume `stdout`. `AgentClient` additionally preserves stderr and the exit code because orchestration needs both successful output and failure evidence.

## Benefits and Trade-offs

**Benefits**

- Keeps provider CLI syntax out of orchestration workflows.
- Gives Ralph and Crew one stable prompt execution contract.
- Captures execution output for diagnostics, logging, and control flow.
- Makes provider CLIs replaceable behind the same interface.
- Avoids leaking provider-specific session concepts into Shipyard.

**Trade-offs**

- Each invocation is an independent CLI process unless the provider CLI preserves state internally.
- Provider-specific capabilities require deliberate exposure through the adapter.
- Captured output may require provider-specific parsing if structured results are needed.

## Validation

A conforming implementation demonstrates that orchestration can execute an agent prompt and inspect stdout, stderr, and exit code without constructing or invoking the concrete provider command directly.

## References

- [Repository GhCli output capture](https://github.com/antshc/ralphv2/blob/main/tools/src/modules/github/infrastructure/gh_cli.py)
- [SquadClient adapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/adapter/client.ts)
