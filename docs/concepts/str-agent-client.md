# Agent Client
**Type:** Architecture Pattern

## Purpose

Provide a stable application-facing client for AI-agent runtimes while isolating orchestration code from provider-specific client APIs such as the Copilot SDK.

The client owns provider connection lifecycle, configuration translation, recovery, and session creation so callers depend on one Shipyard-level API instead of the provider SDK directly.

## Concept

An **AgentClient** wraps a provider client and exposes the lifecycle operations required by orchestration.

```text
Shipyard / Ralph / Crew
          |
          v
      AgentClient
          |
          v
   Provider Client
   e.g. CopilotClient
```

The provider client remains an infrastructure detail. `AgentClient` translates application configuration into provider configuration, starts and stops the provider runtime connection, creates or resumes agent sessions, and converts provider failures into stable client behavior.

The wrapper may add behavior that the provider client does not provide as an application contract, such as explicit connection state, automatic start, bounded reconnection, telemetry, or provider-specific configuration normalization.

`AgentClient` is different from a Headless AI Agent: the Headless AI Agent is the executable worker; `AgentClient` is the orchestration-facing lifecycle boundary used to connect to and invoke the underlying agent runtime.

## Rules

- MUST make orchestration code depend on `AgentClient` rather than the concrete provider client.
- MUST keep provider-client construction inside `AgentClient` or its infrastructure factory.
- MUST own provider connection startup and shutdown behind stable client operations.
- MUST translate application-facing client configuration into provider-specific connection configuration inside the wrapper.
- MUST keep provider-specific connection transports, option names, and lifecycle calls inside the wrapper.
- MUST expose session creation through the application-facing client boundary.
- MUST convert transient provider connection failures into bounded retry or reconnection behavior when recovery is enabled.
- MUST keep reconnection attempts bounded.
- MUST NOT expose the concrete provider client to orchestration code.
- MUST NOT make Ralph, Crew, or other orchestration workflows call provider lifecycle APIs directly.
- SHOULD expose explicit connection state when orchestration needs to reason about availability.
- SHOULD centralize telemetry and usage wiring that applies to every created session.

## Example

A Python `AgentClient` wrapping a provider-specific `CopilotClient`:

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class AgentSession(Protocol):
    @property
    def id(self) -> str: ...

    def send(self, prompt: str) -> str: ...

    def close(self) -> None: ...


class ProviderSession(Protocol):
    @property
    def session_id(self) -> str: ...

    def send_and_wait(self, prompt: str) -> str: ...

    def destroy(self) -> None: ...


class ProviderClient(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def create_session(self, config: dict) -> ProviderSession: ...


@dataclass(frozen=True)
class AgentClientOptions:
    working_directory: str
    auto_start: bool = True
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 3


class CopilotSessionAdapter:
    def __init__(self, inner: ProviderSession) -> None:
        self._inner = inner

    @property
    def id(self) -> str:
        return self._inner.session_id

    def send(self, prompt: str) -> str:
        return self._inner.send_and_wait(prompt)

    def close(self) -> None:
        self._inner.destroy()


class AgentClient:
    def __init__(
        self,
        provider: ProviderClient,
        options: AgentClientOptions,
    ) -> None:
        self._provider = provider
        self._options = options
        self._state = ConnectionState.DISCONNECTED

    @property
    def state(self) -> ConnectionState:
        return self._state

    def connect(self) -> None:
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING
        try:
            self._provider.start()
            self._state = ConnectionState.CONNECTED
        except Exception:
            self._state = ConnectionState.ERROR
            raise

    def disconnect(self) -> None:
        self._provider.stop()
        self._state = ConnectionState.DISCONNECTED

    def create_session(self, *, model: str | None = None) -> AgentSession:
        if self._state != ConnectionState.CONNECTED:
            if not self._options.auto_start:
                raise RuntimeError("Agent client is not connected")
            self.connect()

        config = {
            "working_directory": self._options.working_directory,
            "model": model,
        }

        try:
            session = self._provider.create_session(config)
        except Exception:
            if not self._options.auto_reconnect:
                raise

            self._reconnect()
            session = self._provider.create_session(config)

        return CopilotSessionAdapter(session)

    def _reconnect(self) -> None:
        self._state = ConnectionState.RECONNECTING

        for _ in range(self._options.max_reconnect_attempts):
            try:
                self._provider.stop()
                self._provider.start()
                self._state = ConnectionState.CONNECTED
                return
            except Exception:
                continue

        self._state = ConnectionState.ERROR
        raise RuntimeError("Agent client reconnection failed")
```

Orchestration uses only the stable wrapper:

```python
client.connect()

session = client.create_session(model="claude-sonnet")
result = session.send("Implement issue #42 and verify the result.")

session.close()
client.disconnect()
```

The underlying provider may change without changing the Ralph or Crew workflow as long as the `AgentClient` contract remains stable.

## Benefits and Trade-offs

**Benefits**

- Isolates provider SDK/API changes from orchestration code.
- Centralizes connection, retry, configuration translation, and telemetry behavior.
- Gives Ralph and Crew one stable entry point for creating agent sessions.
- Makes provider clients replaceable or testable behind a fake.
- Prevents provider-specific lifecycle rules from spreading through workflows.

**Trade-offs**

- Adds a wrapper layer over an existing provider SDK.
- The wrapper must evolve when provider capabilities change.
- Provider-specific features require deliberate exposure through the stable application contract.

## Validation

A conforming implementation demonstrates that orchestration can connect, create an agent session, execute work, and disconnect without importing or calling the concrete provider client. Provider lifecycle calls, connection configuration, and retry behavior remain inside the `AgentClient` boundary.

## References

- [SquadClient adapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/adapter/client.ts)
- [Squad high-level client](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/client/index.ts)
