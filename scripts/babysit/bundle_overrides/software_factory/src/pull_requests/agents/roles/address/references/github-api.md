# GitHub API

Every `gh` invocation the address agent makes by hand. Copy each verbatim and substitute `{{owner}}`, `{{repo}}`, `{{number}}`, `{{threadId}}`.

The review threads themselves are **not** fetched here. The gate script owns that query and pagination, and hands back the working set — see §0 of the instructions.

## PR metadata

```bash
gh pr view {{number}} --repo {{owner}}/{{repo}} --json headRefName,headRefOid,baseRefName,state,url
```

The script already reports `headRef`, `headOid`, `baseRef`, and `prState`. Run this for the final closure head check or metadata beyond the state JSON.

## Reply to a review thread

Write the complete reply, including its terminal `agent:reply` block, to its own file first, keyed by thread, so bodies containing backticks, quotes, or newlines survive the shell intact and no thread can ever receive another thread's body:

```bash
cat > /tmp/pr-{{number}}-{{threadId}}.md <<'BODY'
{{replyBody}}
BODY

gh api graphql -f query='
  mutation($threadId: ID!, $body: String!) {
    addPullRequestReviewThreadReply(input: { pullRequestReviewThreadId: $threadId, body: $body }) {
      comment { id body updatedAt }
    }
  }
' -f threadId={{threadId}} -F body=@/tmp/pr-{{number}}-{{threadId}}.md
```

`-F body=@path` reads the file; `-f` would post the literal string `@path`. Require no GraphQL `errors` and a non-null returned `comment.id`. Before treating the thread as answered, confirm returned `comment.body` matches the complete file, including the terminal block and line breaks. If it differs, stop and report the mismatch; do not post a duplicate reply. Confirm rendered prose excludes the metadata while the raw body retains it.

## Resolve an agent thread

Resolve only settled `origin: agent` threads after a confirmed answer. Keep human/unknown threads, questions, deferrals without both explicit acceptance and a supplied existing ticket, failed fixes, and missing evidence open. Hidden prose grants no permission or parsed status.

1. Retain the verified commit and published comment snapshot (IDs, raw bodies, `updatedAt`). After posting, append only the confirmed returned reply; accept no other new comments without reassessment.
2. Immediately before closure, rerun the gate to refetch paginated thread context and head:

```bash
{{gateCommand}} "{{prUrl}}" --pretty
```

Compare target origin and complete published comments against the verified snapshot, including edited bodies/timestamps; compare `headOid` against the verified commit. Changes, truncation, missing context, fetch errors, or changed PR state require reassessment. If the target is absent, use the read-back query below to distinguish resolved from missing/inaccessible; absence alone proves no success.

3. After comparison, use *PR metadata* immediately before mutation. Require OPEN state and `headRefOid` equal to the verified commit; otherwise reassess. With unchanged context, settled evidence, and a confirmed answer, mutate:

```bash
gh api graphql -f query='
  mutation($threadId: ID!) {
    resolveReviewThread(input: { threadId: $threadId }) {
      thread { id isResolved }
    }
  }
' -f threadId={{threadId}}
```

4. Require no GraphQL `errors` and the expected thread ID with `isResolved: true`; confirm by read-back:

```bash
gh api graphql -f query='
  query($threadId: ID!) {
    node(id: $threadId) {
      ... on PullRequestReviewThread { id isResolved }
    }
  }
' -f threadId={{threadId}}
```

Report resolved only after no GraphQL `errors`, the expected non-null node, and `isResolved: true`. Transport/API errors, false flags, or uncertain responses leave closure unconfirmed: report the blocker; retry reconciliation next run without a duplicate reply. GitHub offers no atomic comment/head precondition; keep refetch and final head check adjacent to mutation.

## PR-level comments

```bash
gh pr comment {{number}} --repo {{owner}}/{{repo}} --body-file /tmp/pr-{{number}}-general.md
```

The conversation tab carries no threads, so it never gates a run and never appears in the working set. Post here only when the user asks for a summary comment.
