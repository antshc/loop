from __future__ import annotations

from pathlib import Path

import pytest

from conftest import commit_file, git
from orb import (
    AgentError,
    CommandError,
    Hook,
    Hooks,
    PromptError,
    ScriptedAgent,
    create_sandbox,
    extract_json,
    extract_tag,
    parallel_settled,
    render_prompt,
    run,
    worktree,
)
from orb.errors import ExtractionError


def test_render_prompt_substitutes_and_runs_template_commands() -> None:
    text = render_prompt(
        "A={{A}} B={{ B }}\n!`echo {{A}}`\n",
        {"A": "1"},
        builtins={"B": "2"},
        execute=lambda command: f"ran:{command}\n",
    )

    assert text == "A=1 B=2\nran:echo 1\n"


def test_render_prompt_never_runs_commands_arriving_through_arguments() -> None:
    ran: list[str] = []

    text = render_prompt(
        "{{BODY}}", {"BODY": "!`rm -rf /`"}, builtins={}, execute=lambda c: ran.append(c) or ""
    )

    assert ran == [] and text == "!`rm -rf /`"


def test_render_prompt_rejects_missing_and_overriding_arguments() -> None:
    with pytest.raises(PromptError, match="missing prompt argument: X"):
        render_prompt("{{X}}", {}, builtins={}, execute=str)
    with pytest.raises(PromptError, match="override built-ins"):
        render_prompt("", {"SOURCE_BRANCH": "x"}, builtins={"SOURCE_BRANCH": "y"}, execute=str)


def test_extract_tag_and_json() -> None:
    assert extract_tag("x <plan> {\"a\": 1} </plan> y", "plan") == '{"a": 1}'
    assert extract_json("<plan>[1, 2]</plan>", "plan") == [1, 2]
    with pytest.raises(ExtractionError):
        extract_json("nothing", "plan")
    with pytest.raises(ExtractionError):
        extract_json("<plan>{</plan>", "plan")


def test_parallel_settled_isolates_failures_and_keeps_order() -> None:
    def worker(n: int) -> int:
        if n == 2:
            raise ValueError("boom")
        return n * 10

    outcomes = parallel_settled([1, 2, 3], worker, max_parallel=2)

    assert [o.ok for o in outcomes] == [True, False, True]
    assert [o.value for o in outcomes if o.ok] == [10, 30]
    assert isinstance(outcomes[1].error, ValueError)
    with pytest.raises(ValueError):
        parallel_settled([1], worker, max_parallel=0)


def test_run_collects_commits_and_merges_unnamed_branch_into_host(repo: Path, tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("on {{SOURCE_BRANCH}} into {{TARGET_BRANCH}}: {{WHAT}}")
    agent = ScriptedAgent(lambda prompt, cwd: commit_file(cwd, "a.txt", "a", "add a") or "done")

    result = run(
        sandbox=worktree(), agent=agent, repo=repo, prompt_file=prompt_file, prompt_args={"WHAT": "x"}
    )

    assert len(result.commits) == 1 and result.stdout == "done"
    assert agent.prompts == [f"on {result.branch} into main: x"]
    assert (repo / "a.txt").read_text() == "a"
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(repo, "branch", "--list", result.branch) == ""


def test_run_without_commits_leaves_host_and_branches_untouched(repo: Path) -> None:
    result = run(sandbox=worktree(), agent=ScriptedAgent(lambda p, c: "nothing"), repo=repo, prompt="hi")

    assert result.commits == ()
    assert git(repo, "branch", "--list") == "* main"


def test_inline_prompt_is_not_templated(repo: Path) -> None:
    agent = ScriptedAgent(lambda p, c: "ok")

    run(sandbox=worktree(), agent=agent, repo=repo, prompt="{{LITERAL}}")

    assert agent.prompts == ["{{LITERAL}}"]
    with pytest.raises(PromptError):
        run(sandbox=worktree(), agent=agent, repo=repo, prompt="x", prompt_args={"A": "1"})
    with pytest.raises(PromptError):
        run(sandbox=worktree(), agent=agent, repo=repo)


def test_run_loops_until_completion_signal(repo: Path) -> None:
    outputs = iter(["working", "<promise>COMPLETE</promise>", "never"])
    agent = ScriptedAgent(lambda p, c: next(outputs))

    result = run(sandbox=worktree(), agent=agent, repo=repo, prompt="go", max_iterations=5)

    assert (result.iterations, result.completed) == (2, True)


def test_failed_agent_raises_and_cleans_up(repo: Path) -> None:
    from orb import AgentResult

    agent = ScriptedAgent(lambda p, c: AgentResult(success=False, output="nope"))

    with pytest.raises(AgentError, match="nope"):
        run(sandbox=worktree(), agent=agent, repo=repo, prompt="go")

    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_named_sandbox_is_reusable_and_keeps_branch(repo: Path) -> None:
    agent = ScriptedAgent(lambda prompt, cwd: commit_file(cwd, f"{len(prompt)}.txt", "x", "c") or "ok")

    with create_sandbox(sandbox=worktree(), repo=repo, branch="feature/x") as box:
        first = box.run(agent=agent, prompt="one")
        second = box.run(agent=agent, prompt="three")

    assert len(first.commits) == 1 and len(second.commits) == 1
    assert git(repo, "rev-list", "--count", "main..feature/x") == "2"
    assert (repo / "3.txt").exists() is False


def test_hooks_run_in_sandbox_and_failure_cleans_up(repo: Path) -> None:
    with create_sandbox(
        sandbox=worktree(), repo=repo, branch="b", hooks=Hooks((Hook("echo hi > hooked.txt"),))
    ) as box:
        assert (box.path / "hooked.txt").read_text() == "hi\n"

    with pytest.raises(CommandError):
        create_sandbox(sandbox=worktree(), repo=repo, branch="c", hooks=Hooks((Hook("exit 3"),)))
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_invalid_branch_names_are_rejected(repo: Path) -> None:
    with pytest.raises(CommandError):
        create_sandbox(sandbox=worktree(), repo=repo, branch="--evil")


def test_parallel_sandboxes_on_distinct_branches(repo: Path) -> None:
    def work(name: str) -> tuple[str, ...]:
        agent = ScriptedAgent(lambda p, cwd: commit_file(cwd, f"{name}.txt", name, name) or "ok")
        with create_sandbox(sandbox=provider, repo=repo, branch=f"orb/{name}") as box:
            return box.run(agent=agent, prompt="go").commits

    provider = worktree()
    outcomes = parallel_settled(["a", "b", "c", "d"], work, max_parallel=4)

    assert all(o.ok and len(o.value) == 1 for o in outcomes)
    assert set(git(repo, "branch", "--list", "orb/*").replace("*", "").split()) == {
        f"orb/{n}" for n in "abcd"
    }
