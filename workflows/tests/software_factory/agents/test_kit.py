"""The private agent kit: prompt templates, the response-envelope extraction, and the agent builders' import-time checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from loop import DEFAULT, Message

from workflows.software_factory.agents._kit import (
    PromptError,
    mode_agent,
    placeholders,
    render,
    template_agent,
    to_json,
)


@dataclass(frozen=True)
class _Request(Message):
    name: str


def test_render_substitutes_placeholders_and_leaves_other_braces_alone() -> None:
    assert (
        render("A={{A}} B={{ B }} {A} ${A}", {"A": "1", "B": "2"}) == "A=1 B=2 {A} ${A}"
    )


def test_render_never_expands_placeholders_arriving_through_arguments() -> None:
    assert render("{{BODY}}", {"BODY": "{{BODY}}"}) == "{{BODY}}"


def test_render_rejects_a_missing_argument() -> None:
    with pytest.raises(PromptError, match="missing prompt argument: X"):
        render("{{X}}", {})


def test_placeholders_lists_every_name_once() -> None:
    assert placeholders("Do {{ WHAT }} in {{WHERE}} {{WHAT}}") == {"WHAT", "WHERE"}


def test_to_json_omits_absent_fields_and_projects_nested_messages_tuples_and_paths() -> (
    None
):
    @dataclass(frozen=True)
    class Inner(Message):
        value: str
        note: str | None = None

    @dataclass(frozen=True)
    class Outer(Message):
        inner: Inner
        keys: tuple[str, ...]
        path: Path
        extra: dict

    outer = Outer(Inner("v"), ("a", "b"), Path("/p"), {"none": None})

    assert (
        json.dumps(to_json(outer))
        == '{"inner": {"value": "v"}, "keys": ["a", "b"], "path": "/p", "extra": {"none": null}}'
    )


def _package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str | None
) -> str:
    name = f"role_{tmp_path.name}"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    if prompt is not None:
        (package / "prompt.md").write_text(prompt)
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_a_template_agent_encodes_its_prompt_from_its_package_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, monkeypatch, "Hello {{NAME}}")

    agent = template_agent(
        "greeter",
        package=package,
        profile=DEFAULT,
        args={"NAME": lambda request: request.name},
        decode=lambda *_: None,
    )

    assert agent.encode(_Request("Ada")) == "Hello Ada"


def test_a_template_agent_fails_at_build_when_its_prompt_resource_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, monkeypatch, None)

    with pytest.raises(FileNotFoundError, match="prompt.md"):
        template_agent(
            "greeter", package=package, profile=DEFAULT, args={}, decode=lambda *_: None
        )


@pytest.mark.parametrize(
    ("template", "args", "named"),
    [
        ("Hello {{NAME}} {{OTHER}}", {"NAME": lambda request: request.name}, "OTHER"),
        ("Hello", {"NAME": lambda request: request.name}, "NAME"),
    ],
    ids=["placeholder-without-argument", "argument-without-placeholder"],
)
def test_a_template_agent_fails_at_build_when_placeholders_and_arguments_differ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: str,
    args: dict,
    named: str,
) -> None:
    package = _package(tmp_path, monkeypatch, template)

    with pytest.raises(PromptError, match=named):
        template_agent(
            "greeter",
            package=package,
            profile=DEFAULT,
            args=args,
            decode=lambda *_: None,
        )


def test_a_mode_agent_encodes_the_mode_before_the_request_fields() -> None:
    agent = mode_agent("modal", "go", profile=DEFAULT, decode=lambda *_: None)

    assert agent.encode(_Request("Ada")) == '{"mode": "go", "name": "Ada"}'
