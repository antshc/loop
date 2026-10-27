import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { rmSync } from 'node:fs';

import { type AgentWorkspace, GitOptions } from '../../../src/loop/index.ts';
import { deliverTickets } from '../attempt.ts';
import { devWorkflow } from '../definition.ts';
import { TicketsTracker } from '../platforms.ts';
import {
  type AgentWorkflow,
  RepositoryId,
  type Spec,
  type TicketContext,
  type TicketResult,
  TicketWorkflowError,
} from '../records.ts';
import { DevHarness, issue, newAgentFor, testDirectory } from './support/harness.ts';

let directory: string;

beforeEach(() => {
  directory = testDirectory('dev-attempt-');
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

function complete(harness: DevHarness, context: TicketContext): TicketResult {
  const sha = harness.git.agentCommit(`${context.identifier.toSubject()}work`);
  return { commit: sha, identifier: context.identifier.toString(), status: 'completed', summary: 'done', verification: 'ran tests' };
}

async function deliver(
  harness: DevHarness,
  delegate: AgentWorkflow,
  options: { maxFailures?: number; spec?: Spec } = {},
): Promise<boolean> {
  const tracker = new TicketsTracker('owner', 'repo', harness.gh.run);
  const spec = options.spec ?? (await tracker.specs())[0];
  if (spec === undefined) {
    throw new Error('the harness has no Spec');
  }
  return devWorkflow().run((phases) =>
    newAgentFor(harness.runner, harness.gitService, harness.root)()
      .withGit(new GitOptions())
      .open((worktree) =>
        deliverTickets(spec, worktree, delegate, {
          git: harness.git,
          maxFailures: options.maxFailures,
          phases,
          store: harness.store,
          ticketRepository: new RepositoryId('owner', 'repo'),
          tracker,
        }),
      ),
  );
}

describe('Ticket delivery', () => {
  test('the delegate receives the selected Ticket and the refreshed Initiative history', async () => {
    const harness = new DevHarness(directory, { tickets: new Map([[1, [issue(10, 'First'), issue(11, 'Second')]]]) });
    harness.git.agentCommit('ccode(Checkout|9): earlier');
    harness.git.agentCommit('ccode(Other|9): unrelated');
    const contexts: TicketContext[] = [];

    const delivered = await deliver(harness, (context, worktree: AgentWorkspace) => {
      contexts.push(context);
      expect(worktree.path).toBe(harness.worktree);
      expect([context.baseBranch, context.featureBranch]).toEqual(['main', 'add-login-page']);
      return Promise.resolve(complete(harness, context));
    });

    expect(delivered).toBe(true);
    expect(contexts.map((context) => context.ticket.number)).toEqual([10, 11]);
    expect(contexts[0]?.initiativeCommits).toEqual(['c000000 ccode(Checkout|9): earlier']);
    expect(contexts[1]?.initiativeCommits).toEqual(['c000000 ccode(Checkout|9): earlier', 'c000000 ccode(Checkout|10): work']);
    expect(harness.calls('issue', 'close').map((call) => call[2])).toEqual(['10', '11']);
    expect(harness.git.pushes).toEqual([]);
  });

  test('a retry excludes the rejected commits and preserves earlier work', async () => {
    const harness = new DevHarness(directory);
    harness.git.agentCommit('ccode(Checkout|9): earlier');
    const earlier = harness.git.commits.at(-1)?.sha;
    const contexts: TicketContext[] = [];

    const delivered = await deliver(harness, (context) => {
      contexts.push(context);
      if (contexts.length === 1) {
        harness.git.agentCommit(`${context.identifier.toSubject()}rejected`);
        return Promise.reject(new TicketWorkflowError('review found a defect'));
      }
      return Promise.resolve(complete(harness, context));
    });

    expect(delivered).toBe(true);
    expect(contexts[0]?.initiativeCommits).toEqual(contexts[1]?.initiativeCommits ?? []);
    expect(harness.git.restores).toEqual([earlier ?? '']);
    expect(await harness.store.failedAttempts('https://github.com/owner/repo/issues/10')).toBe(0);
    expect(harness.git.commits).toHaveLength(3);
  });

  test.each([
    ['a result for another Ticket', { identifier: 'Checkout|11' }, 'does not match task'],
    ['an unknown status', { status: 'unknown' }, 'unknown result status'],
    ['no summary', { summary: null }, 'required'],
    ['no verification', { verification: null }, 'required'],
    ['no commit', { commit: null }, 'required'],
    ['a failure without a reason', { reason: null, status: 'failed' }, 'missing reason'],
    ['a failure with a reason', { reason: 'review rejected', status: 'failed' }, 'review rejected'],
  ])('validates %s without relying on a response parser', async (_name, changes, reason) => {
    const harness = new DevHarness(directory);
    let attempts = 0;

    const delivered = await deliver(harness, (context) => {
      attempts += 1;
      return Promise.resolve({ ...complete(harness, context), ...changes });
    });

    expect(delivered).toBe(false);
    expect(attempts).toBe(2);
    expect(harness.git.commits).toHaveLength(1);
    expect(harness.git.restores).toHaveLength(2);
    for (const call of harness.calls('issue', 'comment')) {
      expect(call.at(-1)).toContain(reason);
    }
    expect(harness.calls('issue', 'close')).toEqual([]);
  });

  test('skips the non-actionable Tickets it is given', async () => {
    const harness = new DevHarness(directory, {
      tickets: new Map([[1, [issue(10, 'Closed'), issue(11, 'Blocked'), issue(12, 'Ready')]]]),
    });
    const spec = (await new TicketsTracker('owner', 'repo', harness.gh.run).specs())[0];
    if (spec === undefined) {
      throw new Error('the harness has no Spec');
    }
    const [closed, blocked] = spec.tickets;
    if (closed === undefined || blocked === undefined) {
      throw new Error('the harness has no Tickets');
    }
    closed.state = 'closed';
    blocked.labels = ['hitl'];
    const attempted: number[] = [];

    const delivered = await deliver(
      harness,
      (context) => {
        attempted.push(context.ticket.number);
        return Promise.resolve(complete(harness, context));
      },
      { spec },
    );

    expect(delivered).toBe(true);
    expect(attempted).toEqual([12]);
  });

  test('the failure cap must be positive', async () => {
    const harness = new DevHarness(directory);

    const error = await deliver(harness, () => Promise.reject(new Error('an invalid failure cap must not run agents')), {
      maxFailures: 0,
    }).then(
      () => null,
      (failure: unknown) => failure,
    );

    expect(error).toBeInstanceOf(TicketWorkflowError);
    expect((error as Error).message).toContain('at least one');
  });
});
