# PLANNER

Open issues, as JSON:

<issues-json>

!`{{LIST_ISSUES_COMMAND}}`

</issues-json>

# TASK

Build a dependency graph of the open issues. Issue B is **blocked by** issue A when:

- B needs code or infrastructure that A introduces;
- A and B change overlapping files, so concurrent work would likely conflict;
- B depends on a decision or API shape that A establishes.

An issue is **unblocked** when no open issue blocks it. A PRD that has linked implementation issues cannot be worked on.

Give each unblocked issue the branch `orb/issue-{number}` exactly, so re-planning keeps accumulated progress.

# OUTPUT

Reply with a JSON object in `<plan>` tags and nothing else:

<plan>
{"issues": [{"number": 42, "title": "Fix auth bug", "branch": "orb/issue-42"}]}
</plan>

List only unblocked issues. If every issue is blocked, list the single highest-priority candidate. If there are no open issues, list none.
