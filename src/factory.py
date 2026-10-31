from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .agents import AgentClient
from .builder import AgentBuilder
from .clis import ProcessCliRunner
from .docker import DockerRuntime
from .dryrun import (
    DirectoryGit,
    FakeCliRunner,
    LoggingDocker,
    LoggingGitCli,
    LoggingRunner,
)
from .git import (
    BranchStrategy,
    GitCli,
    GitOptions,
    GitRuntime,
    GitService,
    MergeToHeadStrategy,
)
from .run import AgentContext, AgentOptions
from .sessions import SessionName


def Agent(options: AgentOptions | None = None) -> AgentBuilder:
    """Composition root: register private default dependencies and return builder."""
    agent_context = _apply_options(AgentContext(), options)
    if options is not None and options.dry_run:
        return AgentBuilder(
            runner=LoggingRunner(),
            git=GitRuntime(LoggingGitCli()),
            docker=LoggingDocker(DockerRuntime()),
            agent_context=agent_context,
            dry_run=True,
        )
    return AgentBuilder(
        runner=ProcessCliRunner(),
        git=GitRuntime(GitCli()),
        docker=DockerRuntime(),
        agent_context=agent_context,
    )


def fake_agent(
    runner: FakeCliRunner,
    git: GitService | None = None,
    options: AgentOptions | None = None,
) -> AgentBuilder:
    """A builder that runs `runner` instead of a CLI in a temporary directory instead of a worktree, for end-to-end runs in isolation."""
    return AgentBuilder(
        runner=runner,
        git=git or DirectoryGit(),
        docker=DockerRuntime(),
        agent_context=_apply_options(AgentContext(), options),
    )


def _apply_options(context: AgentContext, options: AgentOptions | None) -> AgentContext:
    if options is None:
        return context
    if options.docker_image is not None:
        context = replace(context, docker_image=options.docker_image)
    if options.start_mode is not None:
        context = replace(
            context, start_mode=options.start_mode, harness_dir=options.harness_dir
        )
    return context


def repo_agent(
    repo: str, branch: str | None = None, session: SessionName | None = None
) -> AgentClient:
    """One worktree-isolated agent for workspace/<repo>, with worktrees in workspace/<repo>.worktrees; a `session` continues across runs."""
    workspace = Path("workspace")
    options = GitOptions(
        root_path=workspace / f"{repo}.worktrees",
        repository_path=workspace / repo,
        strategy=BranchStrategy(branch) if branch else MergeToHeadStrategy(),
    )
    builder = Agent().with_git(options).with_docker()
    return (
        builder.with_session().create(session=session) if session else builder.create()
    )
