# Loop

AFK automated development loop and PR review-comment automation driven by Copilot.

## Language

**Loop**  
A library, not a CLI: an orchestration framework for Headless AI Agents using Git worktrees, installed with `pip` from this repository. It handles running agents against a single repository or multiple repositories, Git branch/worktree lifecycle, and iterative agent execution.

**Ralph**  
The orchestrator: it drives the autonomous development loop, repeatedly invoking Crew agents in fresh sessions. Each iteration reconstructs progress from durable external state such as Tickets, Git commits, source files, and tests, performs bounded work, persists the result, and continues until explicit completion criteria are met.

**Crew**:  
The AI Agent Team of Codey, Chorey, and Testy: specialized agents with distinct responsibilities coordinated toward a shared outcome. Coordination may occur through direct interaction, Ralph, or shared external state.  
_Avoid_: AI Agent Team

**Harness**:  
The repository from which an agent is executed and where its docs, specs, instructions, and skills live. It either holds the source code itself or wraps the repositories that do.  
_Avoid_: Control repo, wrapper repo

**Single Repo**:  
A harness layout in which the harness and the source code are one repository.  
_Avoid_: Monorepo, standalone harness

**Multi Repo**:  
A harness layout in which the harness is a separate parent repository wrapping one or more source repositories.  
_Avoid_: Wrapping harness, parent harness

**Codebase Checkout**:  
The clone of a Spec's target repository whose code changes through worktrees: the harness itself in a Single Repo, a clone inside the Workspace Folder in a Multi Repo.  
_Avoid_: Working copy, source clone

**Workspace Folder**:  
The harness's `workspace` folder: it holds one clone per wrapped source repository in a Multi Repo and the worktrees of every Codebase Checkout in both layouts.  
_Avoid_: Repos folder

**Ticket**:  
A platform-neutral prompt/task, such as a GitHub issue or an Azure DevOps work item, that Loop reads and hands to an agent to act on.  
_Avoid_: Issue, Work item

**Initiative**:  
A coordinated product change tracked as one planning effort, identified by an Initiative id; it has at most one Spec per target repository.  
_Avoid_: Project, epic

**Spec**:  
A Ticket that groups the Tickets one dev run delivers for one Initiative and one target repository; its title is prefixed with the Initiative id, and it names its target repository and base branch.  
_Avoid_: Epic, parent issue

**Workflow**:  
One autonomous procedure, such as `dev`, written by the user as a Python script on the Loop library and run through the user's own shell alias.  
_Avoid_: Slice, feature, template, plugin, built-in workflow

**Loop hook**:
A user-declared shell command that Loop runs on the host at a Loop hook point, before the agent starts; a failing Loop hook fails the run.  
_Avoid_: Hook (unqualified), Agent CLI hook, callback, script, setup step

**Loop hook point**:
A named moment in the Run lifecycle at which Loop runs the Loop hooks declared for it, such as `worktree-ready`.  
_Avoid_: Hook point (unqualified), Agent CLI hook point, event, trigger, stage

**Agent CLI hook**:
A user-declared shell command that an agent CLI runs natively at an Agent CLI hook point during an agent run; it only observes and never fails or steers the run.  
_Avoid_: Hook (unqualified), Loop hook, callback, event handler

**Agent CLI hook point**:
A named moment in an agent CLI's own lifecycle at which it runs the Agent CLI hooks declared for it, such as session start or agent stop.  
_Avoid_: Hook point (unqualified), Loop hook point, event, trigger

**Agent runner**:  
The long-lived pairing of one worktree with one agent client that a Workflow runs prompts through; the agent runs directly on the host with the user's own permissions, with no isolation.  
_Avoid_: Worktree runner, Sandbox, container, environment

**Run lifecycle**:  
The ordered stages of one Workflow run on a worktree: worktree creation and `worktree-ready` Loop hooks, one or more agent runs, publication of the agent's work, and worktree removal.  
_Avoid_: Sandbox lifecycle

**Agent request**:  
One request to an agent; it either starts a new Agent session or resumes an existing one.  
_Avoid_: Prompt call, message

**Agent result**:  
The final response to an Agent request, including the Agent session's identity and the execution status.  
_Avoid_: Output, reply

**Agent session**:  
The persistent context of an agent, identified independently of any container or CLI process.  
_Avoid_: Conversation, process, container

**Session lifecycle**:  
The creation, persistence, resumption, and cleanup of Agent sessions, managed by the agent adapter.  
_Avoid_: Process lifecycle

**Stateless execution**:  
An Agent request that omits a session id, and so starts a new Agent session.  
_Avoid_: One-shot, ephemeral run
