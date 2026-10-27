import { describe, expect, test } from 'bun:test';

import { ProcessFailedError } from '../../../src/loop/index.ts';
import { reviewAndFinalize } from '../review-stage.ts';
import { closedTicket, ReviewHarness, reviewReply } from './support/review-harness.ts';

const headOf = (prompt: string): string => /head is commit\s+`([^`]+)`/.exec(prompt)?.[1] ?? '';

const deliver = (
  harness: ReviewHarness,
  options: { delivery?: Parameters<ReviewHarness['delivery']>[0]; runReview?: boolean } = {},
): Promise<boolean> =>
  harness.run((phases) =>
    reviewAndFinalize(
      harness.spec,
      harness.delivery(options.delivery),
      { repository: harness.repository, runReview: options.runReview === false ? undefined : harness.runReview },
      harness.reviewDeps(phases),
    ),
  );

const lastComment = (harness: ReviewHarness): string => harness.ghCalls('issue', 'comment').at(-1)?.at(-1) ?? '';

describe('the review stage after the worktree closes', () => {
  test('reviews the pushed head, then marks the pull request ready', async () => {
    const harness = new ReviewHarness();

    expect(await deliver(harness)).toBe(true);

    expect(harness.prompts).toHaveLength(1);
    expect(harness.ghCalls('pr', 'ready')).toHaveLength(1);
    expect(harness.ghCalls('pr', 'merge')).toEqual([]);
    const started = harness.lines.indexOf('Review pull request · spec #1 started');
    expect(started).toBeGreaterThan(-1);
    expect(harness.lines.findIndex((line) => line.startsWith('Review pull request · spec #1 completed'))).toBeLessThan(
      harness.lines.indexOf('Check readiness · spec #1 started'),
    );
  });

  test('reviews the commit a cleanup added to the head', async () => {
    const harness = new ReviewHarness();
    const cleanup = harness.git.addCommit('chore(CHK-1): Simplify basket');

    await deliver(harness);

    expect(harness.prompts[0]).toContain(cleanup.sha);
  });

  for (const mode of ['bug', 'wrong-head', 'missing-axis', 'crash', 'checkout-change', 'held-ticket'] as const) {
    test(`keeps the draft and fails the delivery on ${mode}`, async () => {
      const harness = new ReviewHarness(mode === 'held-ticket' ? [closedTicket(10), closedTicket(11, ['hitl'])] : [closedTicket(10)]);
      harness.respond = (prompt) => {
        const head = headOf(prompt);
        switch (mode) {
          case 'bug':
            return reviewReply(head, { findings: [{ kind: 'bug', summary: 'Basket loses products' }] });
          case 'checkout-change':
            harness.git.edit('src/Basket.cs');
            return reviewReply(head);
          case 'crash':
            throw new ProcessFailedError({ argv: ['copilot'], exitCode: 1, signal: null, stderr: 'boom', stdout: '' });
          case 'missing-axis':
            return reviewReply(head, { axes: ['bugs', 'quality', 'smells'] });
          case 'wrong-head':
            return reviewReply(head, { head: 'old' });
          default:
            return reviewReply(head);
        }
      };

      const ready = await deliver(harness);

      expect(ready).toBe(false);
      expect(harness.ghCalls('pr', 'ready')).toEqual([]);
      expect(lastComment(harness)).toContain('left as a draft');
    });
  }

  test('skips review and finalize when no reviewer is configured', async () => {
    const harness = new ReviewHarness();

    expect(await deliver(harness, { runReview: false })).toBe(true);

    expect(harness.lines.filter((line) => line.includes(' skipped · ')).map((line) => line.replace(/ · \d+\.\ds/, ''))).toEqual([
      'Review pull request skipped · No reviewer configured',
      'Check readiness skipped · PR remains a draft without review',
    ]);
    expect(harness.ghCalls('pr', 'ready')).toEqual([]);
    expect(harness.ghCalls('issue', 'comment')).toEqual([]);
    expect(harness.prompts).toEqual([]);
  });

  test('fails the delivery without a reviewer when verification blocks readiness', async () => {
    const harness = new ReviewHarness();

    const passed = await deliver(harness, {
      delivery: { verification: { blocksReady: true, verdict: 'fail' } },
      runReview: false,
    });

    expect(passed).toBe(false);
  });

  test('keeps the draft when app verification did not pass', async () => {
    const harness = new ReviewHarness();

    const ready = await deliver(harness, { delivery: { verification: { blocksReady: true, evidenceDir: '/log/evidence', verdict: 'inconclusive' } } });

    expect(ready).toBe(false);
    expect(lastComment(harness)).toContain('App verification: inconclusive (evidence `/log/evidence`)');
    expect(harness.lines.some((line) => line.startsWith('Check readiness · spec #1 failed'))).toBe(true);
  });
});
