# Agent builder + execution decorators (contract prototype)

A standalone Python sketch of a fluent agent composition API. `Agent()` registers private dependencies and returns a builder. `create()` returns an `AgentClient` interface; decorators compose optional execution behavior without changing `run()` or `close()`.

## Usage

```python
from agent import Agent, AgentProfile, AgentRequest, GitOptions, BranchStrategy, SessionName, codex, copilot

# One-shot: default Copilot profile, one git lifecycle per run()
agent = Agent().with_git().with_docker().create()
try:
    print(agent.run(AgentRequest("Implement ticket #123")).output)
finally:
    agent.close()

# Role profiles: which CLI and model fill a role is a workflow choice
PLANNER = AgentProfile(copilot, "claude-opus-4.5", "high")
DEVELOPER = AgentProfile(codex, "gpt-5-codex", "high")
REVIEWER = AgentProfile(copilot, "claude-sonnet-4.5")

# Shared worktree: one git lifecycle, several CLIs; one named session, kept per CLI
builder = Agent().with_git(GitOptions(strategy=BranchStrategy("loop/ticket-123"))).with_session()
with builder.open(session=SessionName("ticket-123")) as wt:  # no name: loop-<hex>
    plan = wt.agent(PLANNER).run(AgentRequest("Plan ticket #123"))
    developer = wt.agent(DEVELOPER)
    developer.run(AgentRequest(f"Implement this plan:\n{plan.output}"))
    developer.run(AgentRequest("Fix failing tests"))  # resumes the Codex session
    # A new name gives a fresh session, e.g. an unbiased review
    wt.agent(REVIEWER, session=SessionName.new()).run(AgentRequest("Review the diff against the plan"))
```

`with_session()` means "continue across runs" for `create()` and `open()`. Without it every run is stateless (`Start(SessionName.new())`, nothing stored) and passing a name raises `ValueError`. A `SessionName` is an independent session per CLI: the store is keyed by `(SessionName, cli.name)`.

### Agent CLI hooks

Native CLI hooks observe a run (session start/end, agent stop, prompts, tools, subagents, compaction). They are shell commands, applied builder-wide, and never steer the CLI: the shim `agent/hooks/shim.py` normalises each payload, discards stdout and always exits 0.

```python
builder = Agent().with_git(GitOptions(strategy=BranchStrategy("loop/ticket-123"))).with_agent_cli_hooks(
    SessionStartAgentCliHook("./scripts/session-start.sh"),
    SessionEndAgentCliHook("notify-send", timeout_sec=3),
)
DEVELOPER = AgentProfile(CodexCli(hook_trust_bypass=True), "gpt-5-codex")  # Codex skips hooks otherwise
```

Security: `--dangerously-bypass-hook-trust` (Codex) and `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true` (Copilot) also enable any hook file the agent writes into the worktree. Keep the Codex bypass opt-in. Run one agent per worktree at a time: Copilot fires every `loop-*.json` in it.

### Loop hooks

Loop hooks are shell commands, one class per point, run by Loop in declared order. A failing or timed-out hook raises `LoopHookError(point, ...)`.

- `WorktreeReadyLoopHook`: in a fresh worktree (`cwd` = the worktree) before the agent starts; failure force-removes the worktree and the agent never starts. `create()` runs it on every `run()`; `open()` once.
- `WorktreeRemovingLoopHook`: in the worktree after the safety-net commit and before removal, on success and failure; the worktree is removed even when the hook fails.
- `RunFinishedLoopHook`: in the repository after the worktree is removed (and merged, for `MergeToHeadStrategy`); only when the run succeeded.

They receive `LOOP_REPOSITORY`, `LOOP_WORKTREE` and `LOOP_HOOK_POINT`.

```python
GitOptions(
    strategy=BranchStrategy("loop/ticket-123"),
    loop_hooks=(WorktreeReadyLoopHook('cp "$LOOP_REPOSITORY/.env" .env'), WorktreeReadyLoopHook("npm ci", timeout_sec=600)),
)
```

Hooks may write only gitignored paths; nothing enforces it, and a dirty worktree makes the normal removal fail.

Run locally from the prototype directory:

```sh
cd docs/prototypes/agent-builder
python -m agent --dry-run
python -m unittest discover -s . -p 'test_*.py'
```

Python 3.12+, standard library only. No real Git, Docker, Copilot or Codex installation required for the tests.

## Layout

Vertical feature packages; `agent/__init__.py` re-exports the public surface, so `from agent import Agent` is unchanged.

```text
agent/
  sessions/   SessionName, NativeHandle, Start/Resume, CliOutcome, SessionStore  (leaf)
  hooks/      LoopHook*, AgentCliHook*, shim.py                                  (leaf)
  run/        AgentRequest/Result, AgentOptions, AgentContext, RunContext
  clis/       AgentCli, AgentProfile, CopilotCli, CodexCli, CliRunner
  git/        GitService, GitOptions, strategies, GitCli, GitRuntime
  docker/     DockerService, DockerRuntime
  agents/     AgentClient, CliAgentClient, GitAgent, DockerAgent
  dryrun/     Logging* stub adapters
  builder.py  AgentBuilder, Worktree
  factory.py  Agent factory
```

Dependencies point one way: `builder`/`factory` → `agents` → `clis`, `git`, `docker` → `run` → `hooks`, `sessions`.

## Extending

Add a CLI by implementing `AgentCli`:

- `name`: unique, because it scopes session keys.
- `handle_for_new(name)`: the `NativeHandle` the CLI uses for a session started under `name`, or `None` when the CLI assigns its own.
- `command(request, profile, turn, context)`: the argv; `turn` is `Start(name)` or `Resume(name, handle)`. Read `profile.model`, `profile.reasoning_effort`, `profile.context` and append `profile.args`.
- `parse(stdout, turn, exit_code)`: a `CliOutcome` carrying the handle to resume; raises `SessionHandleMissing` when none can be determined. A non-zero exit raises (the runner uses `check=True`).
- `hook_points`: the `AgentCliHookPoint`s the CLI can fire (empty for none).
- `hook_wiring(hooks, turn, workdir)`: pure `AgentCliHookWiring` (extra argv, files, git excludes, env); `native_point(point)`: the CLI's own event name.

Then wrap the adapter in an `AgentProfile` and pass it to `wt.agent(...)`. The library runs the process in the worktree, keeps sessions per CLI, logs the command on dry-run and configures Docker, so the adapter does none of that.

```python
class ClaudeCodeCli:  # sketch, unverified flags
    name = "claude"

    def handle_for_new(self, name):
        return None  # Claude assigns its own id

    def command(self, request, profile, turn, context):
        model = ["--model", profile.model] if profile.model else []
        # Never --session-id: it requires a UUID
        session = ["-n", turn.name.value] if isinstance(turn, Start) else ["--resume", turn.handle.value]
        return ["claude", "-p", request.prompt, "--output-format", "json", *session, *model, *profile.args]

    def parse(self, stdout, turn, exit_code):
        data = json.loads(stdout)
        return CliOutcome(data["result"], NativeHandle(self.name, data["session_id"]), exit_code)

claude = ClaudeCodeCli()
REVIEWER = AgentProfile(claude, "sonnet")
with builder.open() as wt:
    wt.agent(REVIEWER).run(AgentRequest("Review the diff"))
```

Test a custom CLI with `ProcessCliRunner(run=fake)` and check its `command` and `parse`.

## Contracts

- `Agent(options: AgentOptions | None = None) -> AgentBuilder`: *composition root*; registers `ProcessCliRunner` (`LoggingRunner` on dry-run), `GitRuntime(GitCli())`, and `DockerRuntime` internally.
- `SessionName(value)`: frozen; empty or whitespace-containing values raise `ValueError`; `SessionName.new()` returns `loop-<uuid4 hex>`.
- `NativeHandle(cli, value)`, `Start(name)`, `Resume(name, handle)`, `Turn = Start | Resume`, `CliOutcome(output, handle, exit_code)`: frozen session values; only adapters read handles. `SessionHandleMissing` and `SessionCliMismatch` are the session exceptions.
- `AgentCli`: protocol with `name`, `handle_for_new(name)`, `command(request, profile, turn, context)` and `parse(stdout, turn, exit_code)`. Implemented by `CopilotCli` (default args `--allow-all-tools`) and `CodexCli` (default args `--sandbox workspace-write`).
- `AgentProfile(cli, model=None, reasoning_effort=None, context=None, args=())`: frozen; make variants with `dataclasses.replace`.
- `copilot`, `codex`, `DEFAULT = AgentProfile(copilot)`: library instances; no model or role profiles are shipped.
- `ProcessCliRunner(run=subprocess.run)`: `run(profile, request, turn, context)` runs the CLI's argv in the agent cwd and returns `cli.parse(...)`.
- `CliAgentClient(runner, profile, defaults=None, *, store=None, session=None)`: makes the start-or-resume decision. Without a store every run is `Start(SessionName.new())`; `session` without a store raises `ValueError`. With a store it resumes the handle for `(session, cli.name)`, raises `SessionCliMismatch` for a foreign handle, and otherwise starts, saving `handle_for_new(name)` before the run so a retry resumes. It saves the outcome's handle after the run.
- `Worktree`: `path`, `session` and `agent(profile=DEFAULT, session=None) -> AgentClient`; raises `ValueError` when `session` is set but sessions are off, and `RuntimeError("worktree closed")` after the `open()` block.
- `AgentBuilder.open(session=None) -> ContextManager[Worktree]`: runs the git strategy once; without git yields the agent cwd. With sessions on, the worktree session is `session` or a generated name; `session` without `with_session()` raises `ValueError`.
- `AgentBuilder.create(profile=DEFAULT, session=None) -> AgentClient`: one-shot client with one git lifecycle per `run()`; same `session` rule as `open()`.
- `GitOptions`: `root_path` (worktrees root, must resolve inside the cwd; worktree strategies only), `repository_path` (optional, default the agent `cwd`; relative paths resolve from it) sets `git -C` for multi-repository setups, `strategy` (default `HeadStrategy()`), and `loop_hooks` (tuple of any `LoopHook`, default empty; rejected with `HeadStrategy`; `hooks_at(point)` returns those of one point in declared order). A worktree is created at `root_path/<branch>`.
- `LoopHookPoint` (`StrEnum`: `worktree-ready`, `worktree-removing`, `run-finished`); `LoopHook(command, timeout_sec=120.0)` is the frozen base with `point: ClassVar`; instantiate `WorktreeReadyLoopHook`, `WorktreeRemovingLoopHook` or `RunFinishedLoopHook`. The base raises `TypeError`; `ValueError` on an empty command or `timeout_sec <= 0`.
- `LoopHookError(point, command, output)`: raised when a hook exits non-zero or times out; a failed forced removal is attached with `add_note`.
- `GitStrategy`: protocol with `open(git, cwd, repository, options)`, a context manager yielding the agent's working directory. Implemented by the frozen dataclasses `HeadStrategy`, `MergeToHeadStrategy` and `BranchStrategy`, each owning its git lifecycle; see [Strategies](#strategies).
- `GitCli`: with `git -C <repository>`: `fetch` (`fetch --all --prune`), `add_worktree(repository, target, branch, base="HEAD")` (`check-ref-format --branch`; an existing local branch is reused as-is, otherwise `branch` from `origin/<branch>` if present else `base`; then `worktree add <target> <branch>`), `run_hook(hook, cwd, repository, worktree)` (shell command in `cwd` with a timeout; env `LOOP_REPOSITORY`, `LOOP_WORKTREE`, `LOOP_HOOK_POINT`), `remove_worktree(repository, target, *, force=False)` (`worktree remove [--force] <target>`; git refuses with uncommitted changes unless forced), `merge_ff_only` (`merge --ff-only <branch>`) and `delete_branch` (`branch -d <branch>`).
- `GitRuntime.open()`: resolves the repository and delegates to `options.strategy.open()`, which yields the directory the agent works in; a worktree is removed on exit, also on error. After the strategy exits successfully it runs `run-finished` hooks.
- `AgentOptions`: optional frozen caller overrides `docker_image` and `dry_run`; `Agent()` copies the set (non-`None`) values onto the `AgentContext`.
- `AgentBuilder.with_git(options: GitOptions | None = None) -> Self`: enable the git strategy; defaults to `GitOptions()` (`HeadStrategy`, no git calls).
- `AgentBuilder.with_docker() -> Self`: enable Docker execution configuration.
- `AgentClient.run(request: AgentRequest, context: AgentContext | None = None) -> AgentResult`: invariant public entry point. `AgentRequest(prompt)` is CLI-neutral; `AgentResult(output, session: SessionName, exit_code)` names the session the run used.
- `AgentBuilder.with_session() -> Self`: continue sessions across runs through a `SessionStore` (in-memory by default) with `get(name, cli) -> NativeHandle | None` and `save(name, handle)`, keyed by `(SessionName, cli.name)`.
- `AgentContext`: frozen agent settings `cwd`, `docker_image` and `add_dirs` (extra directories, each passed as `--add-dir`); the agent always starts in `cwd`, which already contains the worktree. CLI flags come from the adapter defaults plus `profile.args`.
- `RunContext`: per-run context; carries the `AgentContext` as `agent` and the declared `agent_cli_hooks`.
- `AgentCliHookPoint` (`StrEnum`), `AgentCliHook(command, timeout_sec=30)`: frozen base with `point: ClassVar`; instantiate one of the nine `{Point}AgentCliHook` subclasses (e.g. `SessionStartAgentCliHook`). The base raises `TypeError`; empty command or `timeout_sec <= 0` raise `ValueError`.
- `AgentBuilder.with_agent_cli_hooks(*hooks: AgentCliHook | str) -> Self`: no arguments raise `ValueError`; a `str` becomes a `SessionStartAgentCliHook` and a `SessionEndAgentCliHook`. A point the profile's CLI cannot fire raises `UnsupportedAgentCliHookPoint` from `create()`/`wt.agent()`.
- `AgentCliHookWiring(args, files, git_excludes, env)`: frozen; the runner writes `files` under the cwd, appends `git_excludes` to `info/exclude`, merges `env`, and deletes the files after the run (before the strategy's safety-net commit).
- `CodexCli(args, *, hook_trust_bypass=False)`: without the bypass it reports no hook points.
- `AgentClient.close() -> None`: lifecycle operation delegated through all wrappers.
- `LoggingRunner`: dry-run `CliRunner` that logs `cwd` plus the profile CLI's command.
- `CliRunner`, `GitService`, `DockerService`: narrow dependency protocols. The builder accepts their implementations; callers of `Agent()` do not see them.

## Class diagram

```mermaid
%%{init: {'themeVariables': {'lineColor': '#8b949e'}}}%%
%% diagram-id: agent-builder-classes
classDiagram
    namespace Composition {
        class AgentBuilder {
            -_use_git : bool
            -_use_docker : bool
            -_use_session : bool
            +with_git(options) Self
            +with_docker() Self
            +with_session() Self
            +open(session) ContextManager~Worktree~
            +create(profile, session) AgentClient
        }
        class Worktree {
            +path : Path
            +session : SessionName | None
            -_closed : bool
            +agent(profile, session) AgentClient
        }
    }
    namespace Run {
        class AgentRequest {
            <<frozen dataclass>>
            +prompt : str
        }
        class AgentResult {
            <<frozen dataclass>>
            +output : str
            +session : SessionName
            +exit_code : int
        }
        class AgentOptions {
            <<frozen dataclass>>
            +docker_image : str | None
        }
        class AgentContext {
            <<frozen dataclass>>
            +cwd : Path
            +add_dirs : tuple[Path]
            +docker_image : str | None
        }
        class RunContext {
            <<frozen dataclass>>
            +agent : AgentContext
        }
    }
    namespace Sessions {
        class SessionStore {
            <<Interface>>
            +get(name, cli) NativeHandle | None
            +save(name, handle) None
        }
        class MemorySessionStore
        class SessionName {
            <<frozen dataclass>>
            +value : str
            +new()$ SessionName
        }
        class NativeHandle {
            <<frozen dataclass>>
            +cli : str
            +value : str
        }
        class Start {
            <<frozen dataclass>>
            +name : SessionName
        }
        class Resume {
            <<frozen dataclass>>
            +name : SessionName
            +handle : NativeHandle
        }
        class CliOutcome {
            <<frozen dataclass>>
            +output : str
            +handle : NativeHandle
            +exit_code : int
        }
        class SessionHandleMissing
        class SessionCliMismatch
    }
    namespace Hooks {
        class LoopHookPoint {
            <<Enumeration>>
            WORKTREE_READY
            WORKTREE_REMOVING
            RUN_FINISHED
        }
        class LoopHook {
            <<frozen dataclass>>
            +command : str
            +timeout_sec : float
            +point : ClassVar~LoopHookPoint~
        }
        class WorktreeReadyLoopHook
        class WorktreeRemovingLoopHook
        class RunFinishedLoopHook
        class LoopHookError {
            +point : LoopHookPoint
            +command : str
            +output : str
        }
    }
    namespace Clis {
        class AgentCli {
            <<Interface>>
            +name : str
            +handle_for_new(name) NativeHandle | None
            +command(request, profile, turn, context) list~str~
            +parse(stdout, turn, exit_code) CliOutcome
        }
        class AgentProfile {
            <<frozen dataclass>>
            +cli : AgentCli
            +model : str | None
            +reasoning_effort : str | None
            +context : str | None
            +args : tuple[str]
        }
        class CopilotCli
        class CodexCli
        class UserDefinedCli
        class CliRunner {
            <<Interface>>
            +run(profile, request, turn, context) CliOutcome
        }
        class ProcessCliRunner {
            +run(profile, request, turn, context) CliOutcome
        }
    }
    namespace Docker {
        class DockerService {
            <<Interface>>
            +configure(context) AgentContext
        }
        class DockerRuntime
    }
    namespace Agents {
        class AgentClient {
            <<Interface>>
            +run(request, context) AgentResult
            +close() None
        }
        class CliAgentClient {
            -_store : SessionStore | None
            -_session : SessionName | None
            -_turn() Start | Resume
            +run(request, context) AgentResult
        }
        class AgentWrapper {
            +close() None
        }
        class GitAgent {
            +run(request, context) AgentResult
        }
        class DockerAgent {
            +run(request, context) AgentResult
        }
    }
    namespace Git {
        class GitService {
            <<Interface>>
            +open(cwd, options) ContextManager~Path~
        }
        class GitStrategy {
            <<Interface>>
            +open(git, cwd, repository, options) ContextManager~Path~
        }
        class HeadStrategy {
            <<frozen dataclass>>
            +open(git, cwd, repository, options) Iterator~Path~
        }
        class MergeToHeadStrategy {
            <<frozen dataclass>>
            +open(git, cwd, repository, options) Iterator~Path~
        }
        class BranchStrategy {
            <<frozen dataclass>>
            +branch : str
            +base_branch : str | None
            +open(git, cwd, repository, options) Iterator~Path~
        }
        class worktreeHelper {
            <<module function>>
            +_worktree(git, cwd, repository, options, branch, base) Iterator~Path~
        }
    }
        class GitRuntime {
            +open(cwd, options) ContextManager~Path~
        }
        class GitOptions {
            <<frozen dataclass>>
            +root_path : Path
            +repository_path : Path | None
            +strategy : GitStrategy
            +loop_hooks : tuple~LoopHook~
            +hooks_at(point) tuple~LoopHook~
        }
        class GitCli {
            +fetch(repository) None
            +add_worktree(repository, target, branch, base) None
            +run_hook(hook, cwd, repository, worktree) None
            +remove_worktree(repository, target, force) None
            +merge_ff_only(repository, branch) None
            +delete_branch(repository, branch) None
        }
    }
    namespace DryRun {
        class LoggingRunner {
            +run(profile, request, turn, context) CliOutcome
        }
    }

    CliAgentClient ..|> AgentClient
    GitAgent ..|> AgentClient
    DockerAgent ..|> AgentClient
    GitAgent --|> AgentWrapper : Extends
    DockerAgent --|> AgentWrapper : Extends

    AgentWrapper o-- AgentClient : inner
    CliAgentClient o-- CliRunner
    CliAgentClient o-- AgentProfile
    AgentProfile o-- AgentCli
    CopilotCli ..|> AgentCli
    CodexCli ..|> AgentCli
    UserDefinedCli ..|> AgentCli
    ProcessCliRunner ..> AgentCli : Use
    LoggingRunner ..> AgentCli : Use
    AgentBuilder ..> Worktree : Use
    Worktree ..> CliAgentClient : Use
    Worktree o-- SessionName
    GitAgent o-- GitService
    DockerAgent o-- DockerService
    CliAgentClient o-- SessionStore
    CliAgentClient ..> Start : Use
    CliAgentClient ..> Resume : Use
    CliAgentClient ..> SessionCliMismatch : Use
    Start *-- SessionName
    Resume *-- SessionName
    Resume *-- NativeHandle
    CliOutcome *-- NativeHandle
    AgentResult *-- SessionName
    CodexCli ..> SessionHandleMissing : Use
    MemorySessionStore ..|> SessionStore
    SessionStore ..> NativeHandle : Use
    CliRunner ..> CliOutcome : Use
    AgentClient ..> AgentRequest : Use
    AgentClient ..> AgentResult : Use

    AgentBuilder o-- CliRunner
    AgentBuilder o-- GitService
    AgentBuilder o-- DockerService
    AgentBuilder o-- SessionStore
    AgentBuilder ..> CliAgentClient : Use
    AgentBuilder ..> GitAgent : Use
    AgentBuilder ..> DockerAgent : Use

    AgentClient ..> AgentContext : Use
    CliRunner ..> RunContext : Use
    DockerService ..> AgentContext : Use
    RunContext *-- AgentContext
    AgentBuilder ..> AgentContext : Use
    AgentOptions ..> AgentContext : Use

    ProcessCliRunner ..|> CliRunner
    LoggingRunner ..|> CliRunner
    GitRuntime ..|> GitService
    GitRuntime o-- GitCli
    GitRuntime ..> GitOptions : Use
    GitAgent o-- GitOptions
    GitOptions *-- GitStrategy
    GitOptions o-- LoopHook
    LoopHook --> LoopHookPoint
    WorktreeReadyLoopHook --|> LoopHook
    WorktreeRemovingLoopHook --|> LoopHook
    RunFinishedLoopHook --|> LoopHook
    GitCli ..> LoopHookError : Use
    GitRuntime ..> GitStrategy : Use
    HeadStrategy ..|> GitStrategy
    MergeToHeadStrategy ..|> GitStrategy
    BranchStrategy ..|> GitStrategy
    MergeToHeadStrategy ..> worktreeHelper : Use
    BranchStrategy ..> worktreeHelper : Use
    MergeToHeadStrategy ..> GitCli : Use
    BranchStrategy ..> GitCli : Use
    worktreeHelper ..> GitCli : Use
    DockerRuntime ..|> DockerService

    note for AgentBuilder "Agent() registers the stub adapters; create() wraps git (outer) > docker > core; the core decides start or resume"
    note for CliAgentClient "Store key = (SessionName, cli.name); without a store every run is Start(SessionName.new())"
    note for GitRuntime "Delegates to options.strategy.open(); each strategy owns its git lifecycle"
    note for GitStrategy "head = no git; merge-to-head = tmp_hex worktree, ff-only merge, branch -d; branch = fetch, reuse or create, worktree"

    classDef default fill:#242424,stroke:#8b949e,color:#c9d1d9,stroke-width:1px
```

## Strategies

| Strategy | Effect |
| --- | --- |
| `HeadStrategy()` (default) | No worktree or branch; the agent works directly in the repository directory. No git calls. |
| `MergeToHeadStrategy()` | Creates a `tmp_<hex>` branch worktree from `HEAD`; when the run succeeds, removes the worktree, merges the branch into the current `HEAD` with `--ff-only`, then deletes it. When the run fails, the worktree is removed and the temp branch is kept unmerged. |
| `BranchStrategy(branch, base_branch=None)` | `fetch`, then a worktree on `branch`. A new branch starts from `origin/<branch>` if it exists, else `base_branch` (default `HEAD`); an existing local branch is reused as-is. The branch is kept on exit. |

The agent must commit inside the worktree; `worktree remove` refuses to remove one with uncommitted changes. Omitting `with_git()` disables git handling entirely; `HeadStrategy` is the explicit no-op and rejects Loop hooks.

```python
agent = Agent().create()                                              # no git layer
agent = Agent().with_git().create()                                   # HeadStrategy
agent = Agent().with_git(GitOptions(strategy=MergeToHeadStrategy())).create()
agent = Agent().with_git(GitOptions(strategy=BranchStrategy("loop/ticket-123", "develop"))).create()
```

## Single-repository example

The agent `cwd` is the repository itself, so `repository_path` stays unset and only the worktrees root matters. Run from the repo root.

```python
agent = Agent().with_git(GitOptions(
    root_path=Path("tmp/worktrees"),   # must resolve inside the cwd
    strategy=BranchStrategy("loop/ticket-123"),
)).create()
```

`GitOptions()` defaults to `root_path=".worktrees"`, `repository_path=None` and `HeadStrategy()`. With `MergeToHeadStrategy()` the default run creates `<repo>/.worktrees/tmp_<hex>`:

```text
<repo>/                         # agent cwd, also the git repository
├── .git
├── .worktrees/                 # root_path (add to .gitignore)
│   └── tmp_1a2b3c4d/           # target = root_path/branch, removed when the run ends
└── ...
```

- `root_path` must stay inside the cwd for worktree strategies: `GitRuntime.open()` raises `ValueError` otherwise, so a sibling such as `../repo.worktrees` is rejected. The sibling layout only works in the multi-repository setup, where the cwd is the harness dir above the repositories. `HeadStrategy` ignores `root_path`.
- The default root is inside the repository, so add `.worktrees/` to `.gitignore`.

## Multi-repository example

`repo_agent(repo, branch=None)` builds one worktree-isolated agent for `workspace/<repo>`, with worktrees in `workspace/<repo>.worktrees`: `BranchStrategy(branch)` when a branch is given, otherwise `MergeToHeadStrategy()`. Run it from the harness dir, which is the agent `cwd`.

```python
agent = repo_agent("repo1")  # MergeToHeadStrategy: tmp_<hex> worktree merged into repo1 HEAD
agent.run("Implement ticket #123 in repo1")
```

It is equivalent to:

```python
Agent().with_git(GitOptions(
    root_path=Path("workspace/repo1.worktrees"),
    repository_path=Path("workspace/repo1"),
    strategy=MergeToHeadStrategy(),
)).with_docker().create()
```

Folder structure while one run is active (`tmp_1a2b3c4d` is the generated branch):

```text
<harness>/                          # agent cwd (the agent starts here)
├── .git
└── workspace/
    ├── repo1/                      # repository_path -> git -C target
    │   └── .git                    # holds branch tmp_1a2b3c4d and worktree metadata
    ├── repo1.worktrees/            # root_path
    │   └── tmp_1a2b3c4d/           # target = root_path/branch, removed when the run ends
    │       ├── .git                # file pointing back to repo1/.git/worktrees/tmp_1a2b3c4d
    │       └── ...                 # checkout of tmp_1a2b3c4d, from HEAD
    └── repo2/
        └── .git
```

The agent runs with `cwd=<harness>`; the worktree is inside it, so no `--add-dir` is needed. One agent covers one repository; a second `with_git()` call replaces the first.

## Dry run

`Agent(AgentOptions(dry_run=True))` registers stub adapters that only log to the console (lines prefixed `[dry-run]`): `LoggingGitCli` logs each git command instead of running it, and `LoggingRunner` / `LoggingDocker` log the CLI commands (for every CLI) and Docker steps. No git, Docker or agent CLI process starts and nothing is created on disk.

```sh
python -m agent --dry-run   # run from the prototype dir
```

## Delegation order

```text
Agent().create()
  CliAgentClient.run()
    -> CliRunner.run(profile, ...)

Agent().with_git().with_docker().create()
  GitAgent.run()
    -> GitService.open() [enter: worktree add, then Loop hooks]
    -> DockerAgent.run()
      -> DockerService.configure()
      -> CliAgentClient.run()
        -> CliRunner.run(profile, ...)
    -> GitService.open() [exit even on error]
```

`with_git()` and `with_docker()` set builder flags; `create()` establishes the fixed wrapper order **git (outer) -> Docker (inner) -> core**. Calls preserve prompt and context, replacing only the Docker setting (`cwd` is unchanged; the worktree sits inside it). `close()` forwards to the innermost client.

## Prototype boundaries

**`DockerRuntime` is a stub, not real container execution.** `ProcessCliRunner` really runs the CLI process (tests inject a fake `run`). `GitRuntime` is real: it runs git through `GitCli` (fetch, branch, `worktree add`) against the repository at `repository_path` (default: the agent cwd) and removes the worktree when the run ends. `DockerRuntime.configure()` adds a Docker image to the context without running a container. Tests drive `GitCli` with a fake process runner, so no git is run; they verify composition and command contracts only.

Production integration with `loop` would replace these adapters and align the sketch's `run(str) -> str` / `close()` with the repository's actual `AgentClient.run(Prompt, model, reasoning_effort, AgentOptions) -> AgentResult` / `exit()` contract (`src/loop/contracts/agent_client.py`). In particular, a real Docker-capable runner must mount the newly created worktree and run the CLI inside the container. No production source files are changed by this prototype.
