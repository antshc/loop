# GitHub API

Every `gh` invocation this skill makes by hand. Copy each verbatim and substitute `{{owner}}`, `{{repo}}`, `{{number}}`, `{{threadId}}`.

The review threads themselves are **not** fetched here. `scripts/pr_discussion_state.py` owns that query, its pagination, and the acting-login lookup, and hands back the working set — see §0 of `SKILL.md`.

## PR metadata

```bash
gh pr view {{number}} --repo {{owner}}/{{repo}} --json headRefName,baseRefName,state,url
```

The script already reports `headRef`, `baseRef`, and `prState`. Run this only when something outside the state JSON is needed.

## Reply to a review thread

Write the reply to its own file first, keyed by thread, so bodies containing backticks, quotes, or newlines survive the shell intact and no thread can ever receive another thread's body:

```bash
cat > /tmp/pr-{{number}}-{{threadId}}.md <<'BODY'
{{replyBody}}
BODY

gh api graphql -f query='
  mutation($threadId: ID!, $body: String!) {
    addPullRequestReviewThreadReply(input: { pullRequestReviewThreadId: $threadId, body: $body }) {
      comment { id body }
    }
  }
' -f threadId={{threadId}} -F body=@/tmp/pr-{{number}}-{{threadId}}.md
```

`-F body=@path` reads the file; `-f` would post the literal string `@path`. Read the returned `comment.body` back and confirm it matches the file before treating the thread as answered.

## PR-level comments

```bash
gh pr comment {{number}} --repo {{owner}}/{{repo}} --body-file /tmp/pr-{{number}}-general.md
```

The conversation tab carries no threads, so it never gates a run and never appears in the working set. Post here only when the user asks for a summary comment.
