---
name: pub-spec
description: Publish a spec as a GitHub issue labeled `spec` in the current repository. Use when asked to publish, file, or open a spec issue from a given Markdown file or from the session's plan.
---

# Publish spec

Input: a Markdown file path, or none → the session's plan.

1. **Verify repo.** Run the origin check for safety and repository targeting. Stop on failure.
2. **Resolve source.** Path given → read that file. None → the plan from this session; none exists → ask for a source. Done when the full body text is in hand.
3. **Derive title.** First `#` heading, else a one-line summary of the spec. Strip the heading from the body when used as title.
4. **Ensure label.** `gh label list --search spec`; absent → `gh label create spec`.
5. **Dedupe.** `gh issue list --label spec --search "<title>" --state all`; same title exists → ask whether to update (`gh issue edit`) or create.
6. **Publish.** Write the body to a temp file, then `gh issue create --title "<title>" --label spec --body-file <file>`. Body verbatim; no rewording.
7. **Report** the issue URL. Done when the URL is returned.
