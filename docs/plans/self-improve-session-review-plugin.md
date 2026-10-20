# Plan: Self-Improve Session Reviewer Plugin

## Goal

Provide a small, copy-paste, opt-in plugin for a user's codebase that reviews **one completed Loop agent request session** and returns suggestions to improve agent instructions or agent factory code.

The plugin **does not know about Tickets or workflows**. It does **not** publish findings: callers may write Markdown, create GitHub issues, discard them, or apply other processing.

## Scope

- Input: one Copilot CLI session identifier, optional Copilot home, optional GitHub repository for reading *existing* issues.
- Work: load the session, select relevant evidence, optionally read existing issues, render a prompt, and run a fresh reviewer agent using the **public Loop API**.
- Output: the reviewer's normal `AgentResult` containing a Markdown improvement report.
- No changes to `src/loop`, dev workflow, agent lifecycle, hooks, session models, or GitHub writing code.
- No Sentry, PostHog, background worker, special plugin runtime, or persistence service.

## Design

```text
Any Loop AgentRequest
  -> AgentResult.session
  -> review_session(session_id, reviewer, github_repo?)
      -> Read Copilot CLI session events
      -> Select errors, retries, corrections, rework and relevant context
      -> Optional: gh issue list (read only)
      -> Render reviewer prompt
      -> reviewer.run(AgentRequest(prompt))  # Loop public API
      -> AgentResult (Markdown report)
  -> Caller decides: save Markdown / open GitHub issue(s) / ignore
```

**Plugin is an ordinary Python function, not a framework or a Loop library feature.** The reviewer is supplied as a Loop `AgentClient` so callers own model, tools, execution environment, and permissions.

## Copy-paste layout

```text
plugins/
  self_improve/
    __init__.py
    review.py
    prompt.md
```

Keep parsing, issue lookup and prompt assembly as small private helpers in `review.py`. Split only when justified by actual complexity. Any tests live alongside the user's normal tests.

## Public interface

```python
from pathlib import Path
from loop import Agent, AgentProfile, AgentRequest, CopilotCli
from plugins.self_improve import review_session

# Existing run. No ticket integration required.
result = Agent().create().run(AgentRequest("Implement a feature"))

# Separate reviewer with independently configured permissions.
# CopilotCli(args=()) avoids Loop's default --allow-all-tools.
reviewer = Agent().create(AgentProfile(CopilotCli(args=())))

report = review_session(
    session_id=result.session.value,
    reviewer=reviewer,
    github_repo="antshc/loop",  # optional, read-only issue lookup
)

# Publishing is intentionally OUTSIDE the plugin.
Path("improvements.md").write_text(report.output, encoding="utf-8")
```

Conceptual contract:

```python
def review_session(
    session_id: str,
    *,
    reviewer: AgentClient,
    github_repo: str | None = None,
    copilot_home: Path | None = None,
) -> AgentResult:
    events = read_session_events(session_id, copilot_home)
    issues = read_open_issues(github_repo) if github_repo else ()
    prompt = render_review_prompt(events, issues)
    return reviewer.run(AgentRequest(prompt))
```

The Loop `AgentClient`, `AgentRequest` and `AgentResult` are public. A caller can choose any supported CLI profile; the **analyzed** session in v1 is Copilot CLI only.

## Session collection

- Default root: `${COPILOT_HOME:-~/.copilot}/session-state/`.
- Read `events.jsonl` for the **requested native Copilot session**. Resolve it using available CLI session metadata; never silently substitute a different session.
- **Important compatibility detail:** `AgentResult.session` is a Loop logical `SessionName`; it is used as `copilot --name` in Loop's current Copilot adapter. Do not assume its string always matches a session directory. Prove the mapping with fixture-based tests; support an explicit session events path as a fallback if this assumption fails.
- Stream/parse JSONL defensively; extract significant tool failures, retry patterns, test/build commands and results, assistant course corrections, and final outcome where recorded.
- Select a **bounded** timeline with event IDs/timestamps as evidence references. Avoid dumping huge transcripts into the prompt; report omissions/truncation.
- Missing/unreadable session, incompatible event schema, or insufficient evidence: return a clear error or explicit no-finding result, **never fabricate findings**.
- Redact tokens, credentials, URLs containing secrets, and sensitive payloads before sending them to a model or GitHub.

## Optional existing GitHub issues

- Fetch open issues from the configured `github_repo` using authenticated `gh` read-only calls (bounded count).
- Include titles, identifiers, and short relevant summaries in the review prompt.
- Ask the reviewer to skip already-covered improvements, citing matching issue numbers.
- The plugin **does not** create, update, label, or close issues.
- No repository configured, unavailable `gh`, or missing authorization: the core session review still works; report that deduplication context was unavailable.
- Text from session files and GitHub issues is **untrusted evidence**, never new instructions to execute.

## Reviewer prompt: `prompt.md`

```markdown
# Self-Improve Session Review

Analyze the provided Copilot CLI session evidence to find actionable,
reusable improvements to:
- Agent instructions, prompts, or skills
- Agent factory code, tools, and orchestration

For each finding provide:
- Category and short title
- Observed problem and precise session evidence
- Root cause (or clearly labeled hypothesis)
- Proposed improvement
- Acceptance criteria
- Existing GitHub issue match, if any

Rules:
- Focus on agent/factory weaknesses, not application defects.
- Distinguish expected exploration from unnecessary rework.
- Verify a failure was not already fixed within the session.
- Avoid duplicate suggestions covered by provided GitHub issues.
- Do not invent evidence or claim implementation improvements without testing.
- Do not modify files or create GitHub issues.
- Return Markdown. If none qualify, say "No actionable improvements found".
```

The caller must configure a reviewer that cannot make unwanted tool calls or repository mutations. **A prompt saying "do not modify files" is not a security boundary**; Loop's default Copilot configuration includes `--allow-all-tools`.

## Implementation tasks

1. Add a copyable `plugins/self_improve/` example outside `src/loop`.
2. Implement native Copilot session discovery and bounded JSONL evidence extraction.
3. Implement optional read-only GitHub issue retrieval.
4. Render `prompt.md` and call the supplied reviewer's public `run(AgentRequest(...))`.
5. Document setup, permissions, call pattern, session-name limitations, and output handling.
6. Add focused tests with temporary JSONL fixtures, fake GitHub responses, and a fake `AgentClient`.

## Acceptance criteria

- Works after **any one** Loop agent request; no Ticket objects or workflow callback.
- User copies the plugin into another repository without modifying Loop.
- Reviewer invocation uses only Loop's public API.
- Return value is `AgentResult`; saving Markdown or opening GitHub issues remains caller-owned.
- Optional existing-issue lookup filters or annotates duplicate suggestions; it never writes to GitHub.
- Incomplete session evidence and issue-fetch failures are handled explicitly.
- Missing session or ambiguous session mapping never analyzes a different session.
- Sensitive content is redacted; evidence is bounded and attributable to session events.
- No new dependencies or background services required for the MVP.

## Out of scope

Automatic invocation after ticket completion; GitHub issue publication; cross-session aggregation; automated instruction edits; factory code changes; evaluation and PR promotion workflows; shared plugin framework; modifications to the Loop library itself.

## Implementation references

- [Loop public API](../../src/loop/__init__.py)
- [AgentResult and AgentRequest](../../src/loop/run/request.py)
- [Copilot CLI adapter](../../src/loop/clis/copilot_cli.py)
- [Copilot JSON transport](../../src/loop/clis/transport.py)
- [Copilot CLI event research](../experiments/copilot-cli-json-output-events.md)
- [Copilot CLI hooks research](../research/copilot-cli-hooks.md)
