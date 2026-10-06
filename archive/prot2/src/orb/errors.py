from __future__ import annotations


class OrbError(Exception):
    """Base class for every error the library raises on purpose."""


class CommandError(OrbError):
    def __init__(self, command: str, returncode: int | None, output: str) -> None:
        super().__init__(f"command failed ({returncode}): {command}\n{output}".rstrip())
        self.command = command
        self.returncode = returncode
        self.output = output


class PromptError(OrbError):
    pass


class AgentError(OrbError):
    def __init__(self, name: str, output: str) -> None:
        super().__init__(f"agent '{name}' failed:\n{output}".rstrip())
        self.name = name
        self.output = output


class ExtractionError(OrbError):
    pass
