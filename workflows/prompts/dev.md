# Dev

Initiative: ${{INITIATIVE}}
Spec #${{SPEC_NUMBER}}: ${{SPEC_TITLE}}
${{SPEC_URL}}

You are in the worktree at `${{WORKTREE_PATH}}`, on branch `${{FEATURE_BRANCH}}`, based on
`${{TARGET_BRANCH}}`.

Actionable Tickets for this Spec: ${{TICKET_NUMBERS}}

Implement as many actionable Tickets as you can. Run the project's build and tests until they
pass. Do not commit, and do not push: Orb commits, pushes, and updates every Ticket after this
run ends.

End your final message with one fenced JSON block, one entry per Ticket you worked on:

```json
{"tickets": [{"number": 123, "status": "complete", "summary": "one-line summary of what changed"}]}
```

`status` is one of `complete`, `partial`, or `blocked`. Immediately after that block, end your
message with:

<promise>COMPLETE</promise>

