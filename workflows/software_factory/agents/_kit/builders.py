"""Message encoding and the two agent builders every role is made with."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from loop import AgentProfile, Permissions, copilot

from loop import Json, Message, TypedAgent
from .prompts import PromptError, placeholders, render


# Native denials reinforce prompts for roles whose Python caller owns publication; agents still run with host-user permissions.
PUBLICATION_DENIED = Permissions(deny_tools=("shell(git commit*)", "shell(git push*)", "shell(gh*)"))


def resource_text(package: str, name: str) -> str:
    """The UTF-8 text of the resource `name` that travels with `package`."""
    return resources.files(package).joinpath(name).read_text(encoding="utf-8")


def to_json(value: Any) -> Json:
    """A message as JSON values: nested messages become objects without their absent fields, tuples lists, paths strings."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json(getattr(value, field.name)) for field in fields(value) if getattr(value, field.name) is not None}
    if isinstance(value, (tuple, list)):
        return [to_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: to_json(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def template_agent[Q: Message, R](
    role: str,
    *,
    package: str,
    profile: AgentProfile,
    args: Mapping[str, Callable[[Q], str]],
    decode: Callable[[Q, str], R],
) -> TypedAgent[Q, R]:
    """An agent whose prompt is the `prompt.md` of `package` with one argument per placeholder; a mismatch fails at import."""
    template = resource_text(package, "prompt.md")
    mismatch = placeholders(template) ^ set(args)
    if mismatch:
        raise PromptError(f"{role} prompt placeholders and arguments differ: {', '.join(sorted(mismatch))}")

    def encode(request: Q) -> str:
        return render(template, {key: value(request) for key, value in args.items()})

    return TypedAgent(role, profile, encode, decode)


def mode_agent[Q: Message, R](
    role: str,
    mode: str,
    *,
    profile: AgentProfile,
    decode: Callable[[Q, str], R],
) -> TypedAgent[Q, R]:
    """An agent whose prompt is the JSON object `{"mode": mode, **request fields}` and whose instructions live in its profile."""

    def encode(request: Q) -> str:
        return json.dumps({"mode": mode, **to_json(request)})

    return TypedAgent(role, profile, encode, decode)


def profile_with_instructions(package: str, role: str, *, timeout_sec: float, permissions: Permissions = PUBLICATION_DENIED) -> AgentProfile:
    """A Copilot profile named `role` carrying the `instructions.md` of `package`."""
    return AgentProfile(
        copilot, name=role, permissions=permissions, instructions=resource_text(package, "instructions.md"), timeout_sec=timeout_sec,
    )
