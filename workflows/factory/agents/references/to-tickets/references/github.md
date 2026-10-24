# GitHub ticket operations

Use `{{trackerRepository}}` resolved by the skill for all commands. It holds the spec and its tickets; `repo:target:<owner>/<name>` identifies the source repository, not the tracker. Stop and report read/write failures; do not infer absent issues or edges from a failed lookup.

## Read spec

**Reads:** `{{specIssueNumber}}`.

```
gh issue view {{specIssueNumber}} --repo {{trackerRepository}} --json number,title,body,labels,comments
```

**Returns:** number, title, body, labels, comments. Require the `spec` label and exactly one `repo:target:` label before publication.

## Labels

Missing baseline `hitl`, `spec`, or `bug` → *run `init-wf` skill for wf setup and GitHub issue labels, using labels-only mode* in the tracker repository. Ensure the spec's routing label also exists:

```
gh label create {{repoLabel}} --repo {{trackerRepository}} --color 0e8a16 --description "Source repository for this spec's tickets" --force
```

## Reconcile reruns

List all children, including closed ones:

```
gh api --paginate repos/{{trackerRepository}}/issues/{{specIssueNumber}}/sub_issues?per_page=100
```

Map approved slices to existing child titles and bodies; labels do not encode HITL/AFK type. Reuse an unambiguous matching ticket; conflicting matches require user resolution. After interrupted creation, also inspect unlinked issues with the routing label and matching title/parent-spec content before creating replacements. A created issue remains recorded even if its parent-link request failed.

## Create sub-ticket

**Reads:** `{{title}}`, `{{body}}`, comma-separated `{{label}}`, `{{parentIssueNumber}}`.

1. Follow **Labels**. Write the body verbatim to a temporary UTF-8 file:
   ```
   gh issue create --repo {{trackerRepository}} --label "{{label}}" --title "{{title}}" --body-file <file>
   ```
2. Record the returned URL/number immediately as `{{childIssueNumber}}`. Resolve its internal ID:
   ```
   gh api repos/{{trackerRepository}}/issues/{{childIssueNumber}} --jq .id
   ```
3. Unless already linked to this parent, link with the numeric ID, not the issue number:
   ```
   gh api repos/{{trackerRepository}}/issues/{{parentIssueNumber}}/sub_issues --method POST -F sub_issue_id={{childIssueId}}
   ```
4. Confirm the child appears in the parent's paginated sub-issues and its body/labels match the approved slice. Remove the temporary file.

**Returns:** verified child number and URL; on failure, report any created issue and unfinished link.

## Link blocking

**Reads:** `{{issueNumber}}`, `{{blockedBy}}` (blocker issue numbers).

List existing blockers first:

```
gh api --paginate repos/{{trackerRepository}}/issues/{{issueNumber}}/dependencies/blocked_by?per_page=100
```

For each missing blocker:

```
gh api repos/{{trackerRepository}}/issues/{{blockerNumber}} --jq .id
gh api repos/{{trackerRepository}}/issues/{{issueNumber}}/dependencies/blocked_by --method POST -F issue_id={{blockerId}}
```

**Returns:** confirmed blocker edges after rereading the dependency list. Ignore an already-linked response only after confirming that exact edge exists; surface other failures. Do not close or rewrite the parent spec.

## Offline publishing

Explicit offline mode → use supplied local spec content and routing metadata; skip GitHub reads/writes. A missing spec or metadata requires user input, not invented content. An online failure → report the failed operation and preserve any known issue numbers/URLs and completed/pending links.

Save approved issue bodies under `docs/tickets/`, with distinct stable slice filenames. Include intended titles, labels, parent spec, symbolic blocker references, and execution waves in local metadata. Never invent GitHub issue numbers for unpublished slices. Preserve differing existing files unless replacement is approved.

Report local paths and unpublished/partially published status, not GitHub success. If a create response was lost, mark publication unknown and reconcile before retrying. Offline output does not satisfy the online native-link completion condition.

## Gotchas

`gh issue edit --add-blocked-by` is unavailable; use the REST dependency endpoint. API internal `id` differs from issue `number`; `-F` sends numeric IDs. Preserve literal multiline text with `--body-file`. Do not replace native sub-issue/dependency edges with body links or substitute Jira.
