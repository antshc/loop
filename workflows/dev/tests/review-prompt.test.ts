import { describe, expect, test } from 'bun:test';

import type { ReviewContext, ReviewResult } from '../records.ts';

import { configureLogging } from '../../../src/loop/index.ts';
import { loadReviewTemplate, renderReviewPrompt, reviewArgs, ReviewPromptError, reviewWithAgent } from '../review-agent.ts';
import { silenceLogging } from './support/observer.ts';
import { rejection, ReviewHarness, reviewReply } from './support/review-harness.ts';
import { Stream } from './support/stream.ts';

interface PromptGolden {
  readonly context: { readonly head: string; readonly initiative: string; readonly root: string; readonly url: string };
  readonly prompt: string;
}

const golden = (await Bun.file(new URL('fixtures/review-parity.json', import.meta.url)).json()) as {
  readonly prompts: readonly PromptGolden[];
};

const emptyContext = { head: '', initiative: '', root: '', url: '' };

const contextOf = (item: PromptGolden['context']): ReviewContext => ({
  headSha: item.head,
  initiative: item.initiative,
  pullRequestUrl: item.url,
  sessionName: 'loop(1) x',
  sourceRoot: item.root,
});

describe('the reviewer prompt', () => {
  test('renders byte for byte as the Python prompt did', () => {
    const template = loadReviewTemplate();

    expect(golden.prompts).toHaveLength(2);
    for (const { context, prompt } of golden.prompts) {
      expect(renderReviewPrompt(template, reviewArgs(contextOf(context)))).toBe(prompt);
    }
  });

  test('is the template kept unchanged in the dev prompt folder', async () => {
    const onDisk = await Bun.file(new URL('../prompts/review.md', import.meta.url)).text();

    expect(loadReviewTemplate()).toBe(onDisk);
    expect(onDisk.startsWith('# Review\n')).toBe(true);
  });

  test('fills every placeholder with the context and leaves none behind', () => {
    const rendered = renderReviewPrompt(loadReviewTemplate(), reviewArgs(contextOf(golden.prompts[0]?.context ?? emptyContext)));

    expect(rendered).not.toContain('{{');
    expect(rendered).toContain('whose head is commit\n`0123abc`');
  });

  test('refuses a placeholder without an argument', () => {
    expect(() => renderReviewPrompt('Review {{HEAD_SHA}} {{MISSING}}', { HEAD_SHA: 'abc' })).toThrow(
      new ReviewPromptError('missing prompt argument: MISSING'),
    );
  });

  test('warns about an argument no placeholder uses, once per name, sorted', () => {
    const stream = new Stream();
    configureLogging({ level: 'info', logFile: null, logger: '', stream });
    try {
      expect(renderReviewPrompt('Review {{A}}', { A: '1', B: '3', Z: '2' })).toBe('Review 1');
    } finally {
      silenceLogging();
    }

    const warnings = stream.text.split('\n').filter((line) => line.includes('unused prompt argument'));
    expect(warnings).toHaveLength(2);
    expect([warnings[0]?.endsWith('unused prompt argument: B'), warnings[1]?.endsWith('unused prompt argument: Z')]).toEqual([true, true]);
  });
});

describe('the reviewer delegate', () => {
  const context: ReviewContext = {
    headSha: 'abc123',
    initiative: 'CHK-1',
    pullRequestUrl: 'https://github.com/antshc/loop/pull/9',
    sessionName: 'loop(1) Add basket',
    sourceRoot: '/work/loop',
  };

  const review = async (harness: ReviewHarness): Promise<ReviewResult> =>
    harness.newAgent().open((workspace) => reviewWithAgent(context, workspace, { template: loadReviewTemplate() }));

  test('runs the rendered prompt through one reviewer agent and decodes its answer', async () => {
    const harness = new ReviewHarness();
    harness.respond = () => reviewReply('abc123', { findings: [{ axis: 'bugs', kind: 'bug', summary: 'npe' }] });

    const result = await review(harness);

    expect(harness.prompts).toEqual([renderReviewPrompt(loadReviewTemplate(), reviewArgs(context))]);
    expect([result.completed, result.headSha, result.blockingFindings.length]).toEqual([true, 'abc123', 1]);
  });

  test('raises when the agent answers without a response object', async () => {
    const harness = new ReviewHarness();
    harness.respond = () => 'I could not run the skill';

    expect(await rejection(review(harness))).toMatchObject({ message: 'no response object in agent output' });
  });
});
