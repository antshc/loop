# ralphv2

AFK automated development loop and PR review-comment automation driven by Copilot.

## Language

**Ralph**  
An autonomous development loop that repeatedly invokes a coding agent in fresh sessions. Each iteration reconstructs progress from durable external state such as issues, Git commits, source files, and tests, performs bounded work, persists the result, and continues until explicit completion criteria are met.


**Shipyard**  
An orchestration framework for AI coding agents using Git worktrees. It handles running agents against a single repository or multiple repositories, Git branch/worktree lifecycle, and iterative agent execution.
