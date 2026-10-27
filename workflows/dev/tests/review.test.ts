import { describe, expect, test } from 'bun:test';

import { ProcessFailedError, type WorkflowRun } from '../../../src/loop/index.ts';
import { type ReviewContext, type ReviewWorkflow, TicketWorkflowError } from '../records.ts';
import { type ReviewOutcome, reviewPullRequest } from '../review.ts';
import { rejection, ReviewHarness, reviewReply } from './support/review-harness.ts';

interface Reviewed {
  readonly outcome: ReviewOutcome;
  readonly phase: undefined | WorkflowRun['executions'][number];
}

const review = (harness: ReviewHarness, runReview: ReviewWorkflow = harness.runReview): Promise<Reviewed> =>
  harness.run(async (phases) => {
    const outcome = await reviewPullRequest(harness.spec, harness.delivery(), harness.repository, runReview, harness.reviewDeps(phases));
    return { outcome, phase: phases.executions.find((phase) => phase.name === 'review') };
  });

describe('the review phase', () => {
  test('runs the reviewer on the checkout and completes when every gate holds', async () => {
    const harness = new ReviewHarness();

    const { outcome, phase } = await review(harness);

    expect(outcome.result?.completed).toBe(true);
    expect(outcome.error).toBeUndefined();
    expect(phase?.status).toBe('completed');
    expect(phase?.gates).toEqual([
      { check: 'reviewer left the checkout unchanged', note: '', ok: true },
      { check: 'every axis completed', note: 'an axis did not complete', ok: true },
      { check: 'reviewed the current head', note: "reviewed 'c0000001'", ok: true },
    ]);
    expect(phase?.attributes).toEqual({ blocking_findings: 0, findings: 0, head: 'c0000001', posted: 0, spec: 1 });
    expect(harness.prompts).toHaveLength(1);
    expect(harness.prompts[0]).toContain('whose head is commit\n`c0000001`');
  });

  test('reports the phase through the workflow log as started and completed', async () => {
    const harness = new ReviewHarness();

    await review(harness);

    expect(harness.lines).toHaveLength(4);
    expect(harness.lines[1]).toBe('Review pull request · spec #1 started');
    expect(harness.lines[2]).toMatch(/^Review pull request · spec #1 completed · \d+\.\ds · blocking_findings=0$/);
  });

  test('hands the delegate the pull request, checkout, initiative, head and session name', async () => {
    const harness = new ReviewHarness();
    const contexts: ReviewContext[] = [];

    await review(harness, (context, workspace) => {
      contexts.push(context);
      return harness.runReview(context, workspace);
    });

    expect(contexts).toEqual([
      {
        headSha: 'c0000001',
        initiative: 'CHK-1',
        pullRequestUrl: 'https://github.com/antshc/loop/pull/9',
        sessionName: 'loop(1) CHK-1: Add basket',
        sourceRoot: '/work/loop',
      },
    ]);
  });

  test('counts findings, blocking findings and posted comments on the phase', async () => {
    const harness = new ReviewHarness();
    harness.respond = (prompt) =>
      reviewReply(/commit\s+`([^`]+)`/.exec(prompt)?.[1] ?? '', {
        findings: [
          { axis: 'bugs', kind: 'bug', summary: 'Basket loses products' },
          { axis: 'quality', kind: 'quality', summary: 'naming' },
        ],
      });

    const { outcome, phase } = await review(harness);

    expect(outcome.result?.blockingFindings).toHaveLength(1);
    expect(phase?.attributes).toMatchObject({ blocking_findings: 1, findings: 2, posted: 0 });
    expect(phase?.status).toBe('completed');
  });

  test('fails the phase, not the run, when an axis did not complete', async () => {
    const harness = new ReviewHarness();
    harness.respond = (prompt) => reviewReply(/commit\s+`([^`]+)`/.exec(prompt)?.[1] ?? '', { axes: ['bugs', 'quality', 'smells'] });

    const { outcome, phase } = await review(harness);

    expect(outcome.result?.completed).toBe(false);
    expect([phase?.status, phase?.reason]).toEqual(['failed', 'an axis did not complete']);
  });

  test('fails the phase when the review covered another head', async () => {
    const harness = new ReviewHarness();
    harness.respond = () => reviewReply('', { head: 'old' });

    const { outcome, phase } = await review(harness);

    expect(outcome.result?.headSha).toBe('old');
    expect([phase?.status, phase?.reason]).toEqual(['failed', "reviewed 'old'"]);
  });

  test('quotes the reviewed head the way Python did', async () => {
    const harness = new ReviewHarness();
    harness.respond = () => reviewReply('', { head: "it's" });

    const { phase } = await review(harness);

    expect(phase?.reason).toBe('reviewed "it\'s"');
  });

  for (const [name, change, error] of [
    ['committed', (harness: ReviewHarness) => harness.git.addCommit('agent'), 'the agent committed (c0000002); only the harness commits'],
    ['changed the working tree', (harness: ReviewHarness) => { harness.git.edit('src/Basket.cs'); }, 'the read-only agent changed the working tree'],
  ] as const) {
    test(`ends failed with the violation when the reviewer ${name}`, async () => {
      const harness = new ReviewHarness();

      const { outcome, phase } = await review(harness, (context, workspace) => {
        change(harness);
        return harness.runReview(context, workspace);
      });

      expect(outcome).toEqual({ error });
      expect([phase?.status, phase?.reason]).toEqual(['failed', error]);
      expect(phase?.gates).toEqual([{ check: 'reviewer left the checkout unchanged', note: error, ok: false }]);
    });
  }

  test('may start on a checkout the user already changed', async () => {
    const harness = new ReviewHarness();
    harness.git.edit('notes.md');

    const { outcome, phase } = await review(harness);

    expect(outcome.result?.completed).toBe(true);
    expect(phase?.status).toBe('completed');
  });

  test('turns a failed reviewer process into an outcome with its error', async () => {
    const harness = new ReviewHarness();
    const failure = new ProcessFailedError({ argv: ['copilot', '-p'], exitCode: 1, signal: null, stderr: 'boom', stdout: '' });

    const { outcome, phase } = await review(harness, () => Promise.reject(failure));

    expect(outcome).toEqual({ error: `the reviewer failed: ${failure.message}` });
    expect([phase?.status, phase?.reason]).toEqual(['failed', 'Reviewer could not complete']);
  });

  test('turns an undecodable answer into an outcome with its error', async () => {
    const harness = new ReviewHarness();
    harness.respond = () => 'I could not run the skill';

    const { outcome, phase } = await review(harness);

    expect(outcome).toEqual({ error: 'the reviewer failed: no response object in agent output' });
    expect([phase?.status, phase?.reason]).toEqual(['failed', 'Reviewer could not complete']);
  });

  test('turns a failed review into an outcome with the reviewer reason', async () => {
    const harness = new ReviewHarness();
    harness.respond = () => JSON.stringify({ result: { reason: 'no skill' }, status: 'failed' });

    const { outcome } = await review(harness);

    expect(outcome.error).toBe('the reviewer failed: review failed: no skill');
  });

  test('lets an unexpected error end the phase failed and propagate', async () => {
    const harness = new ReviewHarness();
    let phases: undefined | WorkflowRun;

    const error = await rejection(
      harness.run((run) => {
        phases = run;
        return reviewPullRequest(harness.spec, harness.delivery(), harness.repository, () => Promise.reject(new TypeError('bug')), harness.reviewDeps(run));
      }),
    );

    expect(error).toBeInstanceOf(TypeError);

    const phase = phases?.executions.find((candidate) => candidate.name === 'review');
    expect([phase?.status, phase?.reason]).toEqual(['failed', 'TypeError']);
  });

  test('treats a TicketWorkflowError from the delegate as a failed review', async () => {
    const harness = new ReviewHarness();

    const { outcome } = await review(harness, () => Promise.reject(new TicketWorkflowError('cannot resolve')));

    expect(outcome.error).toBe('the reviewer failed: cannot resolve');
  });
});
