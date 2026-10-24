<issue-template>
## Parent Spec

#{{specIssueNumber}}

## What to build
<what-to-build-rule>
- Self-contained: describe enough of the end-to-end behavior that an implementing agent needs no further repo exploration to understand what to build.
- Describe behavior across all integration layers touched by this slice, not a layer-by-layer breakdown.
- Avoid specific file paths or code snippets — they go stale fast.
- Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it here and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.
</what-to-build-rule>

## Acceptance criteria
<acceptance-criteria-rule>
- Each criterion is a single, self-contained pass/fail check, verifiable without reading code or the spec.
- Phrase as an observable check: "When {{action}}, then {{observableOutcome}}" for behaviors; "If {{condition}}, then {{expectedOutcome}}" for invariants and boundary conditions.
- Use the domain language of the spec or `GLOSSARY.md`; apply the solution-agnostic rule — no file paths, class/variable names, or other implementation details.
- State the exact expected outcome — never vague words like "works", "correctly", "properly", "as expected".
- Fold every applicable Business Rule (`If {{condition}}, {{invariant}}`), Edge Case (`{{boundaryCondition}} → {{expectedHandling}}`), and relevant error condition into its own criterion here, rephrased per the format above — do not create separate sections for them.
</acceptance-criteria-rule>

- [ ] Acceptance criteria 1
- [ ] Acceptance criteria 2
- [ ] Acceptance criteria 3

## Requirements addressed
<requirements-addressed-rule>
- One bullet per addressed spec item: its number, then the spec's text copied verbatim — never numbers alone, never paraphrased, merged, or shortened.
- Omit a subsection if the spec has none of that kind, or none apply to this slice.
</requirements-addressed-rule>

**Functional Requirements:**

- {{number}}. {{verbatimSpecText}}
...

**Business Rule:**

- {{number}}. {{verbatimSpecText}}
...

**Edge cases:**

- {{number}}. {{verbatimSpecText}}
...

## Implementation Decisions
<implementation-decisions-rule>
- Preserve integration constraints and assumptions required for implementation.
- Use short technical statements and implementation-oriented language.
- No specific file paths or code snippets (they become outdated quickly).
</implementation-decisions-rule>

- {{implementationDecision1}}
- {{implementationDecision2}}

## Relevant Concepts
<relevant-concepts-rule>
- Mandatory whenever at least one Concept or ADR applies to this slice.
- One bullet per constraining Concept or ADR. Must carry enough detail to implement the rule without opening the file — the link back to the record is optional supporting context, not a substitute for the summary.
- Bullet must be self-explanatory — no further repo exploration needed to implement.
- No specific file paths or code snippets (they become outdated quickly).
</relevant-concepts-rule>

- {{ruleSummaryAsItAppliesToThisSlice}} ([{{conceptTitle}}](docs/concepts/{{kind}}-{{slug}}.md))
- {{ruleSummaryAsItAppliesToThisSlice}} ([{{decisionTitle}}](docs/adr/{{slug}}.md))

If a summary above isn't enough to implement its rule, open the linked record for full detail before implementing.

## Contracts Delta
<!-- Omit this section entirely if this slice touches no API, Database, or Resource contract. -->

*Run `doc-contracts` skill once per touched contract kind (API, Database, Resource) to write the REST endpoint, table schema, or resource contract delta as a terse diff;* inline its output verbatim here, one block per kind.

## Affected layers & modules
<affected-layers-rule>
- State which layers/modules this slice touches and any Cross-Module Dependency Rules that constrain it, so the implementing agent doesn't need to rediscover placement in the repo.
- Source layer headings and Cross-Module Dependency Rules from the Concept or ADR opened in step 2. Fall back to `ARCHITECTURE.md`'s structural sections (Building blocks/layering) only if neither suffices to place the code.
- If the slice belongs to a specific building block, also scan the `Deployables` table (under `Building blocks` in `ARCHITECTURE.md`) and load the matching block's record (`docs/building-blocks/{{slug}}.md`) for its layer headings and Cross-Module Dependency Rules.
</affected-layers-rule>

- {{layerOrModuleName}}: {{crossModuleDependencyRule}}

## Verify section

This section is used to verify the code changes. List the tests that will be added, updated, and run to verify the task's changes.
- *Mandatory*: The Verify section must be present in every issue. If a Concept about testing/verification exists in `docs/concepts/`, use it (and its link/summary above) to guide the verification. Include the commands that will run the integration or REST API automation tests.
- Only test external behavior, not implementation details.
- List which modules will be tested and prior art for the tests.

</issue-template>
