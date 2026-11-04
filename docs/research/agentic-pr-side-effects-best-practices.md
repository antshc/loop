# Agent-controlled Git and PR side effects: industry practices

Research date: 2026-11-04. Scope: commits, pushes, PR replies, thread resolution, CI restarts, and merge actions in long-running agentic workflows.

## Conclusion

Industry practice does **not** require all Git or GitHub writes to be outside agents. The recurring rule is **bounded authority with code-enforced gates, recoverable side effects, and auditability**. Two valid designs:

1. **Workflow-owned publication:** agent edits and proposes replies; runtime verifies, commits, pushes, and publishes. Strong default for retryable workflows.
2. **Guarded agent tools:** agent invokes narrow actions; tools enforce permissions, expected revisions, verification, idempotency, and receipts. Useful when agent autonomy materially simplifies orchestration.

Do not rely on prompts alone to enforce publication rules.

## Primary evidence

| System | Observed pattern | Source |
|---|---|---|
| GitHub Copilot cloud agent | Agent produces commits and publishes to one allowed branch through managed operations; cannot approve/merge its PR; logs, signed commits and review gates support auditing | [GitHub: risks and mitigations](https://docs.github.com/en/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations) |
| GitHub Agentic Workflows | Read-only by default; writes via declared, validated `safe-outputs`; credentials isolated from agent execution | [GitHub: Agentic Workflows](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-github-agentic-workflows) |
| OpenAI Agents SDK | Tool-input/output guardrails and per-tool human approval; approval does not guarantee exactly-once effects | [Guardrails](https://openai.github.io/openai-agents-python/guardrails/), [Human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/) |
| Temporal | Durable workflows retry external operations as activities; idempotence or outcome reconciliation is required when work can be repeated | [Retry policies](https://docs.temporal.io/encyclopedia/retry-policies), [MCP integration](https://docs.temporal.io/develop/python/integrations/mcp) |

## Required controls

1. **Least privilege:** restrict repositories, target PR, branch, operation type, credentials and available tools. Deny force push and unrelated writes.
2. **Deterministic gates:** enforce tests, clean worktree, intended diff, PR openness, review policy and expected remote HEAD outside model judgment.
3. **Freshness:** pin the observed head/base SHA; recheck immediately before every write. A mismatch stops publication and starts a new observation.
4. **Idempotency:** give each logical external write a durable identity. On retry, confirm the remote effect or reconcile uncertainty before resubmission. A local ledger record alone is not proof of publication.
5. **Untrusted inputs:** treat review comments, CI logs and repository content as data, not instructions. Keep write tokens away from agents that only need read access.
6. **Audit and escalation:** store decision, evidence, tool arguments, commit SHA, check outcome and remote receipt. Require human input on ambiguous merge intent, unverified outcomes and policy-sensitive operations.
7. **Bounded loops:** cap CI retries, agent repair attempts and reviewer↔address cycles. Do not re-run an unchanged failed action indefinitely.

These are architectural controls, not a requirement that Python itself perform every write.

## Recommended tool contracts for Loop

| Capability | Preconditions | Durable result |
|---|---|---|
| `commit_verified(issue_id, tree)` | Scoped worktree; expected parent; changed paths allowed; configured checks pass | Commit SHA, verification result |
| `publish_head(commit_sha, expected_remote_sha)` | Open PR; permitted branch; non-force update; compare remote HEAD | Confirmed remote SHA or explicit conflict |
| `reply_once(thread_id, source_comment_id, body, operation_id)` | Current thread/head; matching reviewed outcome; no existing equivalent reply | Posted comment ID or reconciled existing ID |
| `resolve_agent_thread(thread_id)` | Settled agent-owned thread; confirmed fix/reply; fresh thread state | Confirmed resolved state |
| `rerun_failed_check(failure_id)` | Classified transient; current head; retry budget available | Provider run/queue ID |
| `merge_pr(pr_id)` | Explicit merge policy and authorization, all required checks/reviews satisfied | Merge receipt; otherwise unavailable |

For systems without destination-side idempotency keys, use read-back/reconciliation and fail closed when the outcome is uncertain. Do not claim exact-once delivery.

## Loop-specific assessment

Current [PR babysitter](https://github.com/antshc/loop/tree/main/software_factory/workflows/pr_babysit):

- **Address** and **Maintainer** make judgments and edits. The runtime verifies, commits, publishes fixes, posts review replies and controls Jenkins retries.
- Publication verifies the remote SHA before posting replies, and reconciles replies after interruptions. Conflict recovery pins revisions and verifies the merge tree.
- Worktrees and the execution ledger preserve interrupted work. These are useful controls; moving raw GitHub access into agent prompts would not remove their necessity.
- The existing [merge gate evaluator](https://github.com/antshc/loop/blob/main/software_factory/src/pull_requests/infrastructure/github/pull_requests/merge_gate.py) models required checks, approvals, code owners, conversations, and unavailable policy. The babysitter's [_ready()](https://github.com/antshc/loop/blob/main/software_factory/src/pull_requests/stages/pull_requests.py) is a simpler separate path.

### Recommendation

**Keep workflow-owned publication for now.** If needed, expose the guarded capabilities above to agents while keeping validation, retries, publication receipts and reconciliation in Python. First integrate the existing merge gate into readiness reporting. Do not add a generic unrestricted `git push` or GitHub write tool merely to increase agent autonomy.

A direct-writing agent model is viable when permissions and tool contracts are enforced at runtime. GitHub Copilot is an example of that managed, branch-scoped approach; GitHub Agentic Workflows illustrate the separate safe-output approach.
