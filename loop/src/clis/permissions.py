from __future__ import annotations

from dataclasses import dataclass


class UnsupportedPermissions(ValueError):
    """The selected CLI cannot enforce the requested permission policy."""


@dataclass(frozen=True)
class Permissions:
    """Native tool approval patterns, with deny rules taking precedence over allows."""

    tools: str | tuple[str, ...] = "allow-all"
    deny_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.tools, str) and self.tools != "allow-all":
            raise ValueError("tools must be 'allow-all' or a tuple of tool patterns")
        if isinstance(self.deny_tools, str):
            raise ValueError("deny_tools must be a tuple of tool patterns")
        patterns = (
            self.deny_tools
            if isinstance(self.tools, str)
            else (*self.tools, *self.deny_tools)
        )
        if any(
            not isinstance(pattern, str) or not pattern.strip() for pattern in patterns
        ):
            raise ValueError("tool permission patterns must be nonempty strings")
        if not isinstance(self.tools, str):
            object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "deny_tools", tuple(self.deny_tools))


def has_permission_args(args: tuple[str, ...]) -> bool:
    """Recognize native tool-policy arguments that conflict with a typed policy."""
    flags = {
        "--allow-all",
        "--yolo",
        "--allow-all-tools",
        "--allow-tool",
        "--deny-tool",
    }
    return any(arg.split("=", 1)[0] in flags for arg in args)
