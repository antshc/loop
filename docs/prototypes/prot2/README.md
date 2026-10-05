# prot2

Prototype for composing agent workflows on capsules. A workflow is an ordinary Python script that imports only from `orb`, wires a capsule, git and GitHub clients, and owns its own control flow. Implementations run against fakes at the process boundary (`FakeCopilotCli`, `FakeDocker`, `FakeGh`, `FakeGitClient`, `FakeAgentClient`).

- `NoCapsule` runs the agent in the workspace with no sandbox.
- `DockerCapsule` starts a container and runs the agent's commands inside it with `docker exec`.
- The agent receives the prompt, the prompt args used to render it, and options (configuration, including `session_key`).
- `PromptPreprocessor` replaces `${{KEY}}` placeholders; a template-authored !`cmd` runs through the capsule's executor.

## Class diagram

```mermaid
%%{init: {'themeVariables': {'lineColor': '#8b949e'}}}%%
%% diagram-id: orb-prot2-classes
classDiagram
    namespace Orb {
        class Capsule {
            <<Interface>>
            +workspace : str
            +run(prompt, prompt_args, options) AgentResult
            +exec(command) str
            +close()
        }
        class NoCapsule
        class DockerCapsule
        class AgentClient {
            <<Interface>>
            +run(prompt, prompt_args, options) AgentResult
        }
        class AgentClientBase {
            +run(prompt, prompt_args, options) AgentResult
            -_invoke(prompt, options, session, resume) AgentResult
        }
        class CopilotClient {
            -_invoke(prompt, options, session, resume) AgentResult
        }
        class PromptPreprocessor {
            +process(prompt, prompt_args) str
        }
        class SessionStore {
            <<Interface>>
            +get(key) AgentSession
            +save(session)
        }
        class AgentOptions {
            +model
            +session_key
            +session_name_prefix
            +timeout_s
            +add_dirs
        }
        class AgentSession {
            +key
            +name
        }
        class AgentResult {
            +stdout
            +stderr
            +exit_code
        }
        class GitClient {
            +create_branch(name)
            +create_worktree(branch) Path
            +head(worktree) str
            +commits_since(worktree, revision) tuple
            +merge_into_host(worktree)
            +remove_worktree(worktree)
        }
        class GitHubClient {
            +get_specs() list
            +get_issues() list
            +get_pull_requests() list
            +review_threads(pull_request_id) list
            +reply_to_thread(pull_request_id, thread_id, body)
        }
    }
    namespace Workflow {
        class Dev {
            +main(argv)
        }
    }

    NoCapsule ..|> Capsule
    DockerCapsule ..|> Capsule
    Capsule *-- AgentClient
    AgentClientBase ..|> AgentClient
    CopilotClient --|> AgentClientBase : Extends
    AgentClientBase o-- PromptPreprocessor
    AgentClientBase o-- SessionStore
    SessionStore ..> AgentSession : Use
    AgentClient ..> AgentOptions : Use
    AgentClient ..> AgentResult : Use
    Dev o-- Capsule
    Dev o-- GitClient
    Dev o-- GitHubClient

    classDef default fill:#242424,stroke:#8b949e,color:#c9d1d9,stroke-width:1px
```

## Run

```
pip install -e .
pytest
```
