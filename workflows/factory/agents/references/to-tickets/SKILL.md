---
name: to-tickets
description: Breaks a Spec issue into agent-executable tracer-bullet vertical slices and, after user approval, publishes them as GitHub sub-issues with native blockers and parallel waves. Use when cutting a spec or plan.md into tickets, sub-issues, AFK/HITL slices, or offline Markdown tickets.
argument-hint: "{{specIssueNumber}} [{{implementationDetails}}, `plan.md`] | offline {{localSpecPath}}"
disable-model-invocation: true
---

# To Tickets

Turn one Spec into user-approved, self-contained tracer-bullet tickets linked to that Spec.

## Inputs

- `{{specIssueNumber}}` — parent Spec issue; required online.
- `[implementationDetails]` — inline text or file path (e.g. `plan.md`, `/memories/session/plan.md`); replaces the spec body as slicing source.
- `offline {{localSpecPath}}` — explicit offline mode; needs local spec content plus Initiative/source-repository metadata.

Load [github.md](./references/github.md) before any GitHub read/write or offline save.

## Constraints

- **Approval guard:** **MUST NOT** create, label, link, or save tickets (GitHub issues or `docs/tickets/` files) until the user explicitly approves the current breakdown in step 4. Silence, partial feedback, corrections, and ambiguous replies are not approval. Any slice change after approval needs re-presentation and re-approval. Before approval, only read and draft. Rationale: published tickets create tracker state the user must clean up.
- **MUST NOT** close or modify the parent Spec issue.
- **Source precedence:** spec (or supplied implementation details) is authoritative for behavior and requirements; matched Concepts/ADRs for implementation patterns and policies; code for current state. On conflict, surface it in step 4; do not silently pick a side.
- **Label guard:** every created ticket **MUST** carry `hitl`, whatever its HITL/AFK type. **MUST NOT** create or apply an `afk` label or any label encoding HITL/AFK type. Rationale: `implement-spec` skips `hitl` tickets; the user removes `hitl` to release a reviewed ticket for execution.
- Use offline publishing only when explicitly requested or after a reported GitHub/tool/authentication failure. **MUST NOT** report offline or partial output as GitHub success.

## Workflow

### 1. Resolve inputs

Determine mode:

- **Online, spec only:** resolve the tracker repository once from the docs/harness root (current repository for single-repo work) with `gh repo view --json nameWithOwner --jq .nameWithOwner` → `{{trackerRepository}}` (tracker, not the source repository). Fetch via **Read spec** in [github.md](./references/github.md); slicing source = issue title, body, and comments.
- **Online, spec + implementation details:** resolve `{{trackerRepository}}` and read the spec as above; slicing source = implementation details (read file paths; use inline text directly).
- **Explicit offline:** follow **Offline publishing** in [github.md](./references/github.md); use the supplied local spec and metadata; skip all GitHub lookups.

Set `{{repoLabel}}` to the spec's single `repo:target:<owner>/<name>` label (offline: from metadata). Missing `{{specIssueNumber}}` online, missing offline spec/metadata, or zero/several routing labels → ask the user; **MUST NOT** invent values.

*Done when* slicing source, `{{specIssueNumber}}`, `{{repoLabel}}`, and (online) `{{trackerRepository}}` are known.

### 2. Explore code and records

- Explore code the spec touches until you can name current behavior, integration layers, and verification seams. Reuse exploration already done this session.
- Use `GLOSSARY.md` vocabulary for ticket titles and bodies.
- Read the `Architecture Decision Records` and `Crosscutting Concepts` indexes in `ARCHITECTURE.md`; open each record relevant to the changed area.
  - **Concepts** (shared domain behavior, implementation patterns, operational policies such as layering, validation, persistence, testing) — slices and their acceptance/testing decisions **MUST** conform.
  - **ADRs** (localized decisions) — respect and reference them in ticket bodies.
- Find prefactoring that makes the change easy: "Make the change easy, then make the easy change."

*Done when* matched Concepts/ADRs, prefactor candidates, and source conflicts are listed.

### 3. Draft vertical slices

Break the slicing source into **tracer bullet** slices: thin vertical paths through ALL integration layers end-to-end, not horizontal layer slices.

<vertical-slice-rules>

- Each slice delivers a narrow but COMPLETE path through every layer (schema, API, UI, tests).
- A completed slice is demoable or verifiable on its own.
- Prefactoring slices come first.
- Name each slice's verification seam (e.g. an integration test observing the database layer); prefer the highest seam that still exercises the slice end-to-end.
- Type each slice `HITL` (needs human interaction, e.g. architectural decision or design review) or `AFK` (implementable and mergeable without humans). Prefer AFK.
- Slices in one wave must not edit the same code; merge them or add a blocker.

</vertical-slice-rules>

Derive the **parallel map** from blockers: wave 1 = slices without blockers; wave n = slices whose blockers all sit in earlier waves.

*Done when* every spec Functional Requirement, Business Rule, and Edge Case maps to at least one slice or is flagged as uncovered.

### 4. Review the breakdown with the user

Present a numbered list; per slice show:

- **Title**: short descriptive name
- **Type**: HITL | AFK
- **Blocked by**: slices that must complete first
- **Wave**: parallel execution wave
- **Functional Requirements covered**: spec requirement numbers addressed (full verbatim text is copied into each ticket in step 5)

Then print the parallel map as `Wave n: {{slices}}`, uncovered requirements, and source conflicts.

Ask in ordinary conversation; **MUST NOT** use question/input tools:

- Does the granularity feel right (too coarse / too fine)?
- Are blockers and waves correct? Does any wave have two slices editing the same code?
- Should any slices be merged or split?
- Are HITL/AFK types correct?

**On correction:** apply it, recompute blockers and waves, print the full updated breakdown and parallel map, then end the turn awaiting approval. **MUST NOT** publish in the same turn as a correction.

*Done when* the user explicitly approves the current breakdown.

### 5. Publish tickets

Before any write, confirm the approval guard holds for the current breakdown; otherwise return to step 4.

Render each ticket body from [issue-template.md](./references/issue-template.md) verbatim — every section, each drafted per its `<...-rule>`. Copy each addressed requirement's spec text verbatim into **Requirements addressed**; numbers alone are insufficient.

Branch on mode:

- **Offline:** save approved bodies and the symbolic dependency map via **Offline publishing** in [github.md](./references/github.md); skip GitHub steps below.
- **Online:**
  1. Run **Reconcile reruns** in [github.md](./references/github.md); reuse matched children, create only missing ones.
  2. Per missing slice, run **Create sub-ticket** with `parentIssueNumber` = `{{specIssueNumber}}` and label `hitl,{{repoLabel}}` (per **Label guard**).
  3. After every ticket exists, run **Link blocking** per slice with blockers: `issueNumber` = slice, `blockedBy` = its blockers' issue numbers; link only missing edges.

**Failures:**

- Label not found → follow **Labels** in [github.md](./references/github.md), then retry the failed operation once.
- Other GitHub/tool/authentication failure → stop dependent operations, report the failed operation, and preserve known issue numbers, completed/pending links, and remaining drafts via **Offline publishing**.

## Completion criteria

- **Online:** every approved slice has exactly one child issue under `{{specIssueNumber}}` labeled `hitl` and `{{repoLabel}}`, no `afk` label; each body contains every template section (Contracts Delta only when a contract is touched); every approved **Blocked by** edge is a native link confirmed by reread; parent Spec unchanged. Report the final map as `Wave n: #{{issue}}, …`.
- **Offline or partial:** report local paths and unpublished/partially published status; no invented GitHub issue numbers.
