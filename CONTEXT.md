# ralphv2

AFK automated development loop and PR review-comment automation driven by Copilot.

## Language

**Ralph**  
An autonomous development loop that repeatedly invokes a coding agent in fresh sessions. Each iteration reconstructs progress from durable external state such as issues, Git commits, source files, and tests, performs bounded work, persists the result, and continues until explicit completion criteria are met.


**Shipyard**  
An orchestration framework for Headless AI Agents using Git worktrees. It handles running agents against a single repository or multiple repositories, Git branch/worktree lifecycle, and iterative agent execution.

**Crew**:  
An AI Agent Team: multiple specialized agents with distinct responsibilities coordinated toward a shared outcome. Coordination may occur through direct interaction, an orchestrator, or shared external state.  
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

**Ticket**:  
A platform-neutral unit of tracked work, such as a GitHub issue or an Azure DevOps work item, that Shipyard reads and acts on.  
_Avoid_: Issue, Work item

**Spec**:  
A Ticket that groups the Tickets one dev run delivers, carrying the target branch and initiative in its metadata.  
_Avoid_: Epic, parent issue

**Workflow**:  
One autonomous procedure Shipyard runs as a `ship` subcommand, such as `dev`, discovered and loaded as a plugin.  
_Avoid_: Slice, feature, template
