# REVIEWER

You are on branch `{{BRANCH}}`. Review the work for issue #{{TASK_ID}}: {{ISSUE_TITLE}}

Diff against the target branch:

!`git diff {{TARGET_BRANCH}}...HEAD`

Check correctness, test coverage, and fit with existing conventions. Fix what you find, keep behavior, run the tests, and commit each fix with a message that starts with `REVIEW-{{TASK_ID}}:`. If nothing needs changing, change nothing.
