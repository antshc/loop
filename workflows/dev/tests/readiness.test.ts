import { describe, expect, test } from 'bun:test';

import type { Delivery, ReviewOutcome } from '../review.ts';

import { finalize, readinessBlockers, summary } from '../readiness.ts';
import { ReviewResult, Spec, Ticket, TicketWorkflowError } from '../records.ts';
import { closedTicket, PULL_REQUEST_URL, rejection, ReviewHarness } from './support/review-harness.ts';

interface Golden {
  readonly blockers: readonly string[];
  readonly draftSummary: string;
  readonly input: Scenario;
  readonly readySummary: string;
}

interface Scenario {
  readonly chore: null | { readonly note: string; readonly outOfScope: readonly string[]; readonly status: string };
  readonly commits: readonly string[];
  readonly currentHead: string;
  readonly delivered: boolean;
  readonly deliveryHead: string;
  readonly error: string;
  readonly held: readonly number[];
  readonly labels: readonly string[];
  readonly name: string;
  readonly result: null | {
    readonly axes: readonly { readonly name: string; readonly note?: string; readonly outcome: string }[];
    readonly findings?: readonly { readonly kind: string; readonly summary: string }[];
    readonly head: string;
    readonly posted?: readonly string[];
  };
  readonly tickets: readonly { readonly labels: readonly string[]; readonly number: number; readonly state: string }[];
  readonly verification: { readonly evidenceDir: null | string; readonly verdict: string };
}

const golden = (await Bun.file(new URL('fixtures/review-parity.json', import.meta.url)).json()) as {
  readonly readiness: readonly Golden[];
};

function build(input: Scenario): { currentHead: string; delivery: Delivery; outcome: ReviewOutcome; spec: Spec } {
  const spec = new Spec({
    heldTickets: input.held,
    labels: input.labels,
    number: 1,
    tickets: input.tickets.map(
      (ticket) =>
        new Ticket({ labels: ticket.labels, number: ticket.number, state: ticket.state, title: `T${String(ticket.number)}`, url: `u${String(ticket.number)}` }),
    ),
    title: 'CHK-12: Add basket',
    url: 'https://github.com/antshc/loop/issues/1',
  });
  const delivery: Delivery = {
    chore: input.chore,
    commits: input.commits,
    delivered: input.delivered,
    headSha: input.deliveryHead,
    pullRequestUrl: 'https://github.com/antshc/loop/pull/9',
    verification: {
      blocksReady: !['pass', 'skipped'].includes(input.verification.verdict),
      evidenceDir: input.verification.evidenceDir,
      verdict: input.verification.verdict,
    },
  };
  const result =
    input.result === null
      ? null
      : new ReviewResult({
          axes: input.result.axes,
          findings: input.result.findings,
          headSha: input.result.head,
          posted: input.result.posted,
        });
  return { currentHead: input.currentHead, delivery, outcome: { error: input.error, result }, spec };
}

describe('readiness against the Python Workflow', () => {
  for (const { blockers, draftSummary, input, readySummary } of golden.readiness) {
    test(`${input.name}: blockers and summaries match`, () => {
      const { currentHead, delivery, outcome, spec } = build(input);

      const found = readinessBlockers(spec, delivery, outcome, currentHead);

      expect(found).toEqual([...blockers]);
      expect(summary(delivery, outcome, true, [])).toBe(readySummary);
      expect(summary(delivery, outcome, false, found)).toBe(draftSummary);
    });
  }
});

describe('the finalize phase', () => {
  const clean = (harness: ReviewHarness): ReviewOutcome => ({
    result: new ReviewResult({
      axes: ['bugs', 'quality', 'smells', 'initiative'].map((name) => ({ name, outcome: 'completed' })),
      headSha: harness.git.commits.at(-1)?.sha ?? '',
    }),
  });

  const run = (harness: ReviewHarness, outcome: ReviewOutcome, delivery: Delivery = harness.delivery()) =>
    harness.run(async (phases) => {
      const ready = await finalize(harness.spec, delivery, outcome, harness.repository, {
        git: harness.git,
        phases,
        pulls: harness.pulls(),
        tracker: harness.tracker(),
      });
      return { phase: phases.executions.find((phase) => phase.name === 'finalize'), ready };
    });

  const lastComment = (harness: ReviewHarness): string => harness.ghCalls('issue', 'comment').at(-1)?.at(-1) ?? '';

  test('marks the pull request ready and comments the summary when every gate passes', async () => {
    const harness = new ReviewHarness();

    const { phase, ready } = await run(harness, clean(harness));

    expect(ready).toBe(true);
    expect(harness.ghCalls('pr', 'ready')).toEqual([['pr', 'ready', PULL_REQUEST_URL, '--repo', 'antshc/loop']]);
    expect(lastComment(harness)).toContain(`dev: pull request ${PULL_REQUEST_URL} is **ready for review**`);
    expect(lastComment(harness)).not.toContain('Kept as a draft');
    expect([phase?.status, phase?.attributes]).toEqual(['completed', { ready: true, spec: 1 }]);
    expect(phase?.gates).toEqual([{ check: 'PR head is unchanged since the review', note: 'c0000001', ok: true }]);
    expect(harness.git.fetched).toEqual(['/work/loop']);
  });

  test('reports the phase through the workflow log with the readiness summary', async () => {
    const harness = new ReviewHarness();

    await run(harness, clean(harness));

    expect(harness.lines).toContain('Check readiness · spec #1 started');
    expect(harness.lines.find((line) => line.startsWith('Check readiness · spec #1 completed'))).toMatch(/ · ready=True$/);
  });

  test('never merges, closes the Spec or closes a Ticket', async () => {
    const harness = new ReviewHarness();

    await run(harness, clean(harness));

    expect(harness.ghCalls('pr', 'merge')).toEqual([]);
    expect(harness.ghCalls('issue', 'close')).toEqual([]);
  });

  test('keeps the draft and fails the phase when the review left a blocking finding', async () => {
    const harness = new ReviewHarness();
    const outcome: ReviewOutcome = {
      result: new ReviewResult({
        axes: ['bugs', 'quality', 'smells', 'initiative'].map((name) => ({ name, outcome: 'completed' })),
        findings: [{ kind: 'bug', summary: 'Basket loses products' }],
        headSha: 'c0000001',
      }),
    };

    const { phase, ready } = await run(harness, outcome);

    expect(ready).toBe(false);
    expect(harness.ghCalls('pr', 'ready')).toEqual([]);
    expect(lastComment(harness)).toContain('**left as a draft**');
    expect(lastComment(harness)).toContain('- 1 unresolved bug/initiative finding(s)');
    expect([phase?.status, phase?.reason, phase?.attributes.ready]).toEqual([
      'failed',
      'PR remains a draft: readiness checks did not pass',
      false,
    ]);
    expect(harness.lines.find((line) => line.startsWith('Check readiness · spec #1 failed'))).toMatch(
      / · ready=False · PR remains a draft: readiness checks did not pass$/,
    );
  });

  test('keeps the draft when the review did not run', async () => {
    const harness = new ReviewHarness();

    const { ready } = await run(harness, { error: 'the reviewer failed: boom' });

    expect(ready).toBe(false);
    expect(lastComment(harness)).toContain('Review: not completed (the reviewer failed: boom)');
    expect(lastComment(harness)).toContain('- the reviewer failed: boom');
  });

  test('keeps the draft with a held Ticket', async () => {
    const harness = new ReviewHarness([closedTicket(10), closedTicket(11, ['hitl'])]);

    const { ready } = await run(harness, clean(harness));

    expect(ready).toBe(false);
    expect(lastComment(harness)).toContain('- a Ticket or the Spec is waiting for a human (`hitl`)');
  });

  test('fails the head gate first when the PR head moved after the review', async () => {
    const harness = new ReviewHarness();
    harness.git.refs.set('origin/add-basket', 'moved00');

    const { phase, ready } = await run(harness, clean(harness));

    expect(ready).toBe(false);
    expect(phase?.gates).toEqual([{ check: 'PR head is unchanged since the review', note: 'moved00', ok: false }]);
    expect(phase?.reason).toBe('moved00');
    expect(lastComment(harness)).toContain('- the PR head moved to moved00 after the review started');
  });

  test('keeps the draft and says why when marking the PR ready fails', async () => {
    const harness = new ReviewHarness();
    harness.failReady = true;

    const { phase, ready } = await run(harness, clean(harness));

    const reason = 'marking the PR ready failed: command failed (1): gh pr ready\nGraphQL: not mergeable';
    expect(ready).toBe(false);
    expect(phase?.reason).toBe(reason);
    expect(lastComment(harness)).toContain(`- ${reason}`);
    expect(lastComment(harness)).toContain('**left as a draft**');
  });

  test('refuses a Spec without a feature branch', async () => {
    const harness = new ReviewHarness([closedTicket(10)], ['spec']);

    expect(await rejection(run(harness, clean(harness)))).toEqual(
      new TicketWorkflowError('cannot resolve feature branch for Spec https://github.com/antshc/loop/issues/1'),
    );
    expect(harness.ghCalls('pr', 'ready')).toEqual([]);
  });
});
