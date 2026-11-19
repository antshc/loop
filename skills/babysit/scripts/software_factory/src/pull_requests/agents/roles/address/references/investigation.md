# Investigation

One subagent per substantive `issue`, each returning a `verdict` backed by `evidence`.

## Discover the investigation skills

Investigation skills are named `search-*`, `inspect-*`, and `explore-*`. The skills available to you are already enumerated in your system context. Filter that list to names matching those prefixes.

Prioritize these matched skills over generic search/read tools for any issue within their stated scope.

## Dispatch

Spawn one subagent (the `task` tool) per substantive `issue`, in a single parallel batch of at most 3. Issues have separate `evidence` paths, so they never wait on each other.

Use the `explore` agent type when the `issue` is answerable from this codebase alone. Use the `general-purpose` agent type when it turns on an external fact — a package's real API, a framework's documented behaviour, a spec — so the general-purpose subagent inherits web, docs, and package tools.

## Prompt template

```
Read-only investigation. Gather evidence and report; leave every file unchanged.

ISSUE: {{issueStatement}}

WHERE: {{paths}} at {{lines}}

WHAT THE REVIEWER SAID:
{{commentExcerpts}}

INVESTIGATION SKILLS AVAILABLE TO YOU:
{{investigationSkills}}

Consider each listed skill by name and use every one that bears on this issue before falling back to raw search. Name the ones you used in your report.

Establish whether the reviewer is right by reading the code, its callers, and its tests. Report:

1. VERDICT — fix-needed | no-change-needed | unclear
2. EVIDENCE — at least one `path:line` citation per claim, each with the quoted line. A verdict with no citation is not a verdict.
3. ROOT CAUSE — what the code actually does today and why the reviewer's reading holds or fails.
4. PROPOSED CHANGE — compare credible remedies and recommend the smallest evidence-backed correction, naming the exact symbols and files. Distinguish required facts from optional optimization. No diff.
5. BLAST RADIUS — every other call site, test, or contract the proposed change touches.

Choose `unclear` only when missing evidence or conflicting requirements prevent a safe verdict or remedy. State the blocked decision and one question that would unblock it. Multiple possible remedies or an unknown optimum alone do not make a verified concern unclear.
```

## Verdict contract

| Verdict | Means |
|---|---|
| `fix-needed` | The reviewer is right and the code needs an edit. |
| `no-change-needed` | Cited evidence shows the code already satisfies the reviewer, or the concern rests on a misreading. |
| `unclear` | A specific evidence or requirement gap blocks a safe verdict or remedy. |

A subagent that returns no `path:line` citation has returned `unclear`, whatever verdict it typed.
