import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import type { TicketContext } from '../records.ts';

import { type AgentWorkspace, BranchStrategy } from '../../../src/loop/index.ts';
import { implementTicket } from '../agent.ts';
import { FileExecutionStore } from '../journal.ts';
import { RepositoryId } from '../records.ts';
import {
  COMPLETED,
  crash,
  DevHarness,
  envelope,
  type Handler,
  hookError,
  issue,
  testDirectory,
} from './support/harness.ts';

const TWO_TICKETS = (): Map<number, Record<string, unknown>[]> =>
  new Map([[1, [issue(10, 'Add login form'), issue(11, 'Add login tests')]]]);
const TICKET_10_URL = 'https://github.com/owner/repo/issues/10';

let directory: string;
let harness: DevHarness;

beforeEach(() => {
  directory = testDirectory('dev-delivery-');
  harness = new DevHarness(directory);
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

const callNumbers = (calls: (readonly string[])[]): (string | undefined)[] => calls.map((call) => call[2]);

const hitlTargets = (): Set<string | undefined> =>
  new Set(harness.calls('issue', 'edit').filter((call) => call.at(-1) === 'hitl').map((call) => call[2]));

function withSpecs(specs: Record<string, unknown>[], tickets?: Map<number, Record<string, unknown>[]>): DevHarness {
  return new DevHarness(directory, { specs, tickets });
}

describe('where the agents run', () => {
  test('in the worktree on the feature branch based on the target', async () => {
    const code = await harness.run(() => harness.commitAndReport(10));

    expect(code).toBe(0);
    const options = harness.gitService.opened[0];
    expect(options?.strategy).toEqual(new BranchStrategy('add-login-page', 'origin/main'));
    expect(options?.rootPath).toBe(harness.repository.worktrees);
    expect(harness.prompts[0]).toContain(`\`${harness.worktree}\``);
    expect(harness.gitService.closed).toBe(1);
  });

  test('only the configured harness is used', async () => {
    const code = await harness.run(() => harness.commitAndReport(10));

    expect(code).toBe(0);
    expect(harness.git.fetched).toEqual([harness.root]);
    expect(harness.gitService.opened[0]?.repositoryPath).toBe(harness.root);
  });

  test('the target repository match is case-insensitive', async () => {
    const target = withSpecs(
      [issue(1, 'Add a widget', { labels: ['spec', 'repo:target:OWNER/REPO', 'repo:base:main'] })],
      new Map([[1, [issue(10, 'Build the widget')]]]),
    );

    const code = await target.run(() => target.commitAndReport(10, { initiative: '1' }));

    expect(code).toBe(0);
    expect(target.git.fetched).toEqual([target.root]);
    expect(target.calls('pr', 'create')).toHaveLength(1);
  });
});

describe('routing a Spec', () => {
  test('an unconfigured repo target is labelled hitl with no worktree opened', async () => {
    const target = withSpecs(
      [issue(1, 'Add a widget', { labels: ['spec', 'repo:target:acme/widgets', 'repo:base:main'] })],
      new Map([[1, [issue(10, 'Build the widget')]]]),
    );

    const code = await target.run();

    expect(code).toBe(0);
    expect(target.gitService.opened).toEqual([]);
    expect(target.git.fetched).toEqual([]);
    expect(target.calls('issue', 'edit')[0]?.at(-1)).toBe('hitl');
    const comment = target.calls('issue', 'comment')[0]?.at(-1) ?? '';
    expect(comment).toContain('repo:target:acme/widgets');
    expect(comment).toContain('harness owner/repo');
  });

  test.each([
    ['no target', ['spec', 'repo:base:main']],
    ['a malformed target', ['spec', 'repo:target:not-a-slug', 'repo:base:main']],
    ['two targets', ['spec', 'repo:target:owner/repo', 'repo:target:other/one', 'repo:base:main']],
  ])('a Spec with %s is labelled hitl and never falls back to the harness', async (_name, labels) => {
    const target = withSpecs([issue(1, 'Add login page', { labels })]);

    const code = await target.run();

    expect(code).toBe(0);
    expect(target.git.fetched).toEqual([]);
    expect(target.calls('issue', 'edit')[0]?.at(-1)).toBe('hitl');
  });

  test('a Spec labelled hitl is skipped', async () => {
    const target = withSpecs([
      issue(1, 'Add login page', { labels: ['spec', 'hitl', 'repo:target:owner/repo', 'repo:base:main'] }),
    ]);

    const code = await target.run();

    expect(code).toBe(0);
    expect(target.writes()).toEqual([]);
  });

  test('a missing remote target branch is labelled hitl with no worktree opened', async () => {
    harness.git.remoteBranches.delete('main');

    const code = await harness.run();

    expect(code).toBe(0);
    expect(harness.calls('issue', 'edit')[0]?.at(-1)).toBe('hitl');
    expect(harness.gitService.opened).toEqual([]);
  });

  test('a requested Spec that does not exist fails the run', async () => {
    expect(await harness.run(undefined, { specNumber: 999 })).toBe(1);
  });
});

describe('failures', () => {
  test('a Ticket that failed once in an earlier run is escalated on its next failure without a retry', async () => {
    await harness.store.recordFailure(TICKET_10_URL, { items: [10], owner: 'owner', repo: 'repo', taskId: '10', title: 'x' });

    const code = await harness.run();

    expect(code).toBe(1);
    expect(harness.prompts).toHaveLength(1);
    expect(hitlTargets()).toEqual(new Set(['1', '10']));
  });

  test("failures persist per Ticket in the day's execution log", async () => {
    const store = new FileExecutionStore(harness.logDir, { clock: () => new Date('2026-01-01T12:00:00Z') });

    const code = await harness.run(undefined, { store });

    expect(code).toBe(1);
    const records = JSON.parse(readFileSync(join(harness.logDir, 'dev-execution-log-2026-01-01.json'), 'utf8')) as Record<string, unknown>[];
    expect([records[0]?.count, records[0]?.last_run, records[0]?.last_items]).toEqual([2, '2026-01-01T12:00:00Z', [10]]);
  });

  test('a first failure retries the same Ticket immediately without hitl', async () => {
    const code = await harness.run(() => (harness.prompts.length === 1 ? '' : harness.commitAndReport(10)));

    expect(code).toBe(0);
    expect(harness.prompts).toHaveLength(2);
    expect(harness.calls('issue', 'edit')).toEqual([]);
    expect(callNumbers(harness.calls('issue', 'close'))).toEqual(['10']);
    expect(await harness.store.failedAttempts(TICKET_10_URL)).toBe(0);
  });

  test('a rejected retry is not attempted again with a failure cap of one', async () => {
    const code = await harness.run(undefined, { maxTicketFailures: 1 });

    expect(code).toBe(1);
    expect(harness.prompts).toHaveLength(1);
    expect(hitlTargets()).toEqual(new Set(['1', '10']));
  });

  test('two Tickets each failing once are not escalated', async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });
    const seen = new Set<number>();

    const code = await target.run((prompt) => {
      const number = prompt.includes('"number": 10') ? 10 : 11;
      if (!seen.has(number)) {
        seen.add(number);
        return '';
      }
      return target.commitAndReport(number);
    });

    expect(code).toBe(0);
    expect(target.calls('issue', 'edit')).toEqual([]);
    expect(callNumbers(target.calls('issue', 'close'))).toEqual(['10', '11']);
  });

  test('a failing worktree hook fails the Spec with its reason', async () => {
    harness.gitService.openError = hookError();

    const code = await harness.run();

    expect(code).toBe(1);
    expect(harness.prompts).toEqual([]);
    expect(harness.calls('issue', 'comment').some((call) => (call.at(-1) ?? '').includes('setup.sh'))).toBe(true);
  });

  test('a prompt placeholder with no argument fails the run', async () => {
    const repositories = [
      {
        repository: harness.repository,
        runAgents: (context: TicketContext, workspace: AgentWorkspace) =>
          implementTicket(context, workspace, 'Task {{UNKNOWN}}'),
      },
    ];

    const code = await harness.run(undefined, { repositories });

    expect(code).toBe(1);
    expect(harness.prompts).toEqual([]);
    expect(harness.calls('issue', 'comment').some((call) => (call.at(-1) ?? '').includes('missing prompt argument: UNKNOWN'))).toBe(true);
  });

  test.each([
    ['no commit', (): Handler => () => envelope('Checkout|10', 'completed', COMPLETED), 'HEAD did not change'],
    ['no JSON', (): Handler => () => 'no json here', 'no response object'],
    [
      'a truncated response',
      (): Handler => () => '{"identifier": "Checkout|10", "status": "completed", "result": {"commit"',
      'no response object',
    ],
    [
      'another Ticket',
      (h: DevHarness): Handler => h.commitThen((sha) => envelope('Checkout|11', 'completed', { ...COMPLETED, commit: sha })),
      'identifier',
    ],
    [
      'the wrong commit',
      (h: DevHarness): Handler => h.commitThen(() => envelope('Checkout|10', 'completed', { ...COMPLETED, commit: 'deadbeef' })),
      'result.commit',
    ],
    [
      'missing fields',
      (h: DevHarness): Handler => h.commitThen((sha) => envelope('Checkout|10', 'completed', { commit: sha, summary: 's' })),
      'required',
    ],
    [
      'the wrong subject',
      (h: DevHarness): Handler => () => h.commitAndReport(10, { subject: 'feat: wrong subject' }),
      'does not start with',
    ],
    ['a crash', (): Handler => crash(), 'did not exit successfully'],
  ])('a failed validation (%s) restores the worktree and escalates the Ticket and the Spec', async (_name, makeHandler, reason) => {
    const code = await harness.run(makeHandler(harness));

    expect(code).toBe(1);
    expect(harness.git.commits).toHaveLength(1);
    expect(harness.git.restores.length).toBeGreaterThan(0);
    expect(harness.git.pushes).toEqual([]);
    expect(harness.calls('pr', 'create')).toEqual([]);
    expect(hitlTargets()).toEqual(new Set(['1', '10']));
    const comments = harness.calls('issue', 'comment');
    expect(new Set(callNumbers(comments))).toEqual(new Set(['1', '10']));
    for (const comment of comments) {
      expect(comment.at(-1)).toContain(reason);
    }
    expect(harness.calls('issue', 'close')).toEqual([]);
  });

  test('two commits in one run fail the run', async () => {
    const code = await harness.run(() => {
      harness.git.agentCommit('ccode(Checkout|10): first');
      return harness.commitAndReport(10);
    });

    expect(code).toBe(1);
    expect(harness.git.commits).toHaveLength(1);
    expect(harness.calls('issue', 'comment').some((call) => (call.at(-1) ?? '').includes('exactly one commit'))).toBe(true);
  });

  test('an uncommitted change fails the run', async () => {
    const code = await harness.run(() => {
      const response = harness.commitAndReport(10);
      harness.git.dirty = true;
      return response;
    });

    expect(code).toBe(1);
    expect(harness.git.dirty).toBe(false);
    expect(harness.calls('issue', 'comment').some((call) => (call.at(-1) ?? '').includes('uncommitted'))).toBe(true);
  });

  test('a failed response after committing discards the commit and comments its reason', async () => {
    const code = await harness.run(() => {
      harness.git.agentCommit('ccode(Checkout|10): half');
      return envelope('Checkout|10', 'failed', { reason: 'needs a decision' });
    });

    expect(code).toBe(1);
    expect(harness.git.commits).toHaveLength(1);
    expect(harness.git.pushes).toEqual([]);
    for (const comment of harness.calls('issue', 'comment')) {
      expect(comment.at(-1)).toContain('needs a decision');
    }
  });

  test('a failed Ticket stops the delivery of the next Ticket', async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });

    const code = await target.run();

    expect(code).toBe(1);
    expect(target.prompts).toHaveLength(2);
    expect([...target.calls('issue', 'close'), ...target.calls('issue', 'edit')].every((call) => call[2] !== '11')).toBe(true);
  });

  test('only the selected Ticket is closed', async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });

    await target.run((prompt) => (prompt.includes('Checkout|10') ? target.commitAndReport(10) : ''));

    expect(callNumbers(target.calls('issue', 'close'))).toEqual(['10']);
  });
});

describe('delivering Tickets', () => {
  test('a complete Ticket is pushed, gets its pull request and is closed with its SHA, summary and verification', async () => {
    let reported = '';

    const code = await harness.run(() => {
      const response = harness.commitAndReport(10, { summary: 'added the form' });
      reported = (JSON.parse(response) as { result: { commit: string } }).result.commit;
      return response;
    });

    expect(code).toBe(0);
    expect(harness.git.pushes).toEqual([[harness.worktree, 'add-login-page']]);
    const close = harness.calls('issue', 'close')[0];
    expect(close?.[2]).toBe('10');
    expect(close?.at(-1)).toContain(reported);
    expect(close?.at(-1)).toContain('added the form');
    expect(close?.at(-1)).toContain('ran tests');
    expect(harness.calls('pr', 'create')).toHaveLength(1);
  });

  test('two actionable Tickets each get a fresh run in order', async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });
    const numbers = [10, 11];

    const code = await target.run(() => target.commitAndReport(numbers.shift() ?? 0));

    expect(code).toBe(0);
    expect(target.prompts).toHaveLength(2);
    expect(callNumbers(target.calls('issue', 'close'))).toEqual(['10', '11']);
    expect(target.git.pushes).toHaveLength(2);
  });

  test('all Tickets delivered comments the pull request link on the open Spec', async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });
    const numbers = [10, 11];

    await target.run(() => target.commitAndReport(numbers.shift() ?? 0));

    const specComments = target.calls('issue', 'comment').filter((call) => call[2] === '1');
    expect(specComments).toHaveLength(1);
    expect(specComments[0]?.at(-1)).toContain('pull');
    expect(target.calls('issue', 'close').every((call) => call[2] !== '1')).toBe(true);
  });

  test("a Ticket's prompt excludes the Spec body and the other Tickets", async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });

    await target.run();

    const prompt = target.prompts[0] ?? '';
    expect(prompt).toContain('Body of #10');
    expect(prompt).toContain('Checkout|10');
    expect(prompt.replace('Body of #10', '')).not.toContain('Body of #1');
    expect(prompt).not.toContain('Add login tests');
  });

  test("the prompt lists only this Initiative's task commits", async () => {
    for (const subject of ['ccode(Checkout|9): earlier work', 'ccode(Other|3): other initiative', 'ccode: bare']) {
      harness.git.agentCommit(subject);
    }

    await harness.run();

    const prompt = harness.prompts[0] ?? '';
    expect(prompt).toContain('ccode(Checkout|9): earlier work');
    expect(prompt).not.toContain('other initiative');
    expect(prompt).not.toContain('bare');
  });

  test('the prompt states when the Initiative has no task commits', async () => {
    await harness.run();

    expect(harness.prompts[0]).toContain('No task commits for this Initiative');
  });

  test('a hitl stop still pushes the commits of Tickets closed in the run', async () => {
    const target = new DevHarness(directory, { tickets: TWO_TICKETS() });

    const code = await target.run((prompt) => (prompt.includes('Checkout|10') ? target.commitAndReport(10) : ''));

    expect(code).toBe(1);
    expect(target.git.pushes).toHaveLength(1);
    expect(target.calls('pr', 'create')).toHaveLength(1);
    expect(
      target
        .calls('issue', 'comment')
        .filter((call) => call[2] === '1')
        .every((call) => !(call.at(-1) ?? '').includes('pull request')),
    ).toBe(true);
  });

  test('a response preceded by agent noise is accepted', async () => {
    const code = await harness.run(() => {
      const noise = envelope('Other|1', 'completed', COMPLETED);
      return `working...\n${noise}\nmore text\n${harness.commitAndReport(10)}\n`;
    });

    expect(code).toBe(0);
  });
});

describe('publishing', () => {
  test("a branch ahead at the Spec's start is pushed from the checkout before the worktree opens", async () => {
    harness.git.agentCommit('ccode(Checkout|9): earlier');
    const pushedBeforeOpen: boolean[] = [];
    harness.gitService.onOpen = () => {
      pushedBeforeOpen.push(harness.git.pushes.length > 0);
    };

    await harness.run();

    expect(harness.git.pushes[0]).toEqual([harness.root, 'add-login-page']);
    expect(pushedBeforeOpen).toEqual([true]);
    expect(harness.calls('pr', 'create')).toHaveLength(1);
  });

  test('a Spec with no actionable Ticket and a branch ahead is pushed with its pull request', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.git.agentCommit('ccode(Checkout|9): earlier');

    const code = await target.run();

    expect(code).toBe(0);
    expect(target.git.pushes).toEqual([[target.root, 'add-login-page']]);
    expect(target.calls('pr', 'create')).toHaveLength(1);
  });

  test('nothing ahead means no push and no pull request', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });

    const code = await target.run();

    expect(code).toBe(0);
    expect(target.git.pushes).toEqual([]);
    expect(target.writes()).toEqual([]);
  });

  test('nothing to publish constructs only the harness tracker client', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    const checkouts: string[] = [];

    const code = await target.run(undefined, {
      githubFactory: (checkout) => {
        checkouts.push(checkout);
        return target.github;
      },
    });

    expect(code).toBe(0);
    expect(checkouts).toEqual([target.root]);
    expect(target.calls('pr')).toEqual([]);
  });

  test('repeating a run with the remote up to date pushes nothing more', async () => {
    await harness.run(() => harness.commitAndReport(10));
    await harness.run(() => '');

    expect(harness.git.pushes).toHaveLength(1);
  });

  test('a draft pull request already open for the branch is not duplicated', async () => {
    const target = new DevHarness(directory, {
      prs: [{ body: '', headRefName: 'add-login-page', number: 5, title: 'existing', url: 'https://github.com/owner/repo/pull/5' }],
    });

    const code = await target.run(() => target.commitAndReport(10));

    expect(code).toBe(0);
    expect(target.calls('pr', 'create')).toEqual([]);
  });

  test('the pull request is created on the target repository by the host, not by the agent', async () => {
    const code = await harness.run(() => harness.commitAndReport(10));

    expect(code).toBe(0);
    expect(harness.prompts[0]).not.toMatch(/\bgh\b/);
    const create = harness.calls('pr', 'create')[0] ?? [];
    expect(create[create.indexOf('--repo') + 1]).toBe(new RepositoryId('owner', 'repo').toString());
    expect(create).toContain('--draft');
  });
});

describe('the whole pipeline', () => {
  function skill(): void {
    harness.gitService.onOpen = () => {
      const path = join(harness.worktree, '.github/skills/verify-basket/SKILL.md');
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, '---\nname: verify-basket\ndescription: Drive and prove the basket in the app.\n---\n');
    };
  }

  function verifier(options: { commit?: boolean; edit?: string; evidence?: boolean; verdict?: string } = {}): void {
    harness.runner.verifyHandler = (prompt) => {
      if (options.evidence !== false) {
        const directoryOfEvidence = /`([^`]*evidence[^`]*)`/.exec(prompt)?.[1] ?? '';
        writeFileSync(join(directoryOfEvidence, 'basket.json'), '{"products": ["coffee"]}');
      }
      if (options.edit !== undefined) {
        harness.git.edit(options.edit);
      }
      if (options.commit === true) {
        harness.git.agentCommit('rejected verifier commit');
      }
      return envelope('', 'completed', {
        commit_message: 'Fix basket',
        summary: 'Checked the basket',
        verdict: options.verdict ?? 'pass',
      });
    };
  }

  const deliver = (target = harness): Promise<number> => target.run(() => target.commitAndReport(10), { allPhases: true });

  test('publishes, refines and leaves the draft after the worktree closes', async () => {
    const code = await deliver();

    expect(code).toBe(0);
    expect(harness.runner.profiles.map((profile) => profile.name)).toEqual(['developer', 'chore']);
    expect(harness.calls('pr', 'ready')).toEqual([]);
    expect(callNumbers(harness.calls('issue', 'close'))).toEqual(['10']);
    const log = harness.records.join('\n');
    expect(log).toContain('Plan tickets · spec #1 skipped');
    expect(log).toContain('Verify application · spec #1 skipped');
    expect(log.indexOf('Publish draft PR · spec #1 completed')).toBeLessThan(log.indexOf('Refine changes · spec #1 started'));
    expect(log).toContain('Review pull request · spec #1 skipped');
    expect(log).toContain('Check readiness · spec #1 skipped');
    expect(log).not.toContain('worktree opened');
  });

  test('without a verifier or a cleanup agent both steps are skipped', async () => {
    const code = await harness.run(() => harness.commitAndReport(10));

    expect(code).toBe(0);
    const log = harness.records.join('\n');
    expect(log).toContain('Verify application · spec #1 skipped · 0.0s · No verifier configured');
    expect(log).toContain('Refine changes · spec #1 skipped · 0.0s · No cleanup configured');
    expect(log).toContain('Review pull request · spec #1 skipped · 0.0s · No reviewer configured');
    expect(log).toContain('Check readiness · spec #1 skipped · 0.0s · PR remains a draft without review');
  });

  test('the planner creates sub-issues and Tickets are delivered in dependency order', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = () =>
      envelope('', 'completed', {
        tickets: [
          { body: 'Create a basket', title: 'Add basket' },
          { blocked_by: [0], body: 'Add a product', title: 'Add coffee' },
        ],
      });
    const numbers = [101, 102];

    const code = await target.run(() => target.commitAndReport(numbers.shift() ?? 0), { allPhases: true });

    expect(code).toBe(0);
    const creates = target.calls('issue', 'create');
    expect(creates).toHaveLength(2);
    expect(creates[1]?.at(-1)).toContain('Blocked by #101');
    expect(callNumbers(target.calls('issue', 'close'))).toEqual(['101', '102']);
    expect(target.git.pushes).toHaveLength(2);
    expect(target.runner.profiles.map((profile) => profile.name)).toEqual(['planner', 'developer', 'developer', 'chore']);
  });

  test.each([
    ['no JSON', 'no JSON'],
    ['no Tickets', envelope('', 'completed', { tickets: [] })],
    ['a refusal', envelope('', 'failed', { reason: 'needs a decision' })],
  ])('an unusable plan (%s) hands off without opening an implementation worktree', async (_name, reply) => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = () => reply;

    const code = await target.run(undefined, { allPhases: true });

    expect(code).toBe(1);
    expect(target.calls('issue', 'create')).toEqual([]);
    expect(target.gitService.opened).toEqual([]);
    expect(target.calls('issue', 'edit')[0]?.at(-1)).toBe('hitl');
  });

  test('a planner that changes the checkout is rejected', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = () => {
      target.git.edit('src/Basket.cs');
      return envelope('', 'completed', { tickets: [{ body: 'b', title: 'Add basket' }] });
    };

    const code = await target.run(undefined, { allPhases: true });

    expect(code).toBe(1);
    expect(target.calls('issue', 'create')).toEqual([]);
    expect(target.calls('issue', 'comment').at(-1)?.at(-1)).toContain('the read-only agent changed the working tree');
  });

  test('closed or held sub-issues do not trigger another plan', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, [issue(10, 'Done', { state: 'CLOSED' })]]]) });

    const code = await target.run(undefined, { allPhases: true });

    expect(code).toBe(0);
    expect(target.runner.planPrompts).toEqual([]);
    expect(target.calls('issue', 'create')).toEqual([]);
  });

  test('a passing app verification saves evidence and commits only the fixes', async () => {
    skill();
    verifier({ edit: 'src/Basket.cs' });

    const code = await deliver();

    expect(code).toBe(0);
    expect(harness.git.commits.at(-1)?.subject).toBe('fix(Checkout): Fix basket');
    expect(harness.git.pushes).toHaveLength(2);
    expect(harness.calls('issue', 'comment').at(-1)?.at(-1)).toContain('app verification **pass**');
    expect(readdirSync(join(harness.logDir, 'evidence'), { recursive: true }).some((entry) => String(entry).endsWith('basket.json'))).toBe(true);
  });

  test.each([
    ['fail', { evidence: true }, 'fail'],
    ['pass', { evidence: false }, 'inconclusive'],
    ['pass', { edit: 'proof/features/basket.py', evidence: true }, 'partial'],
    ['pass', { commit: true, evidence: true }, 'inconclusive'],
  ])('a %s verdict that is unproven or rejected (%j) settles as %s and never marks the PR ready', async (verdict, options, settled) => {
    skill();
    verifier({ verdict, ...options });

    const code = await deliver();

    expect(code).toBe(1);
    expect(harness.calls('pr', 'ready')).toEqual([]);
    expect(harness.git.commits.map((commit) => commit.subject)).not.toContain('rejected verifier commit');
    expect(harness.calls('issue', 'comment').at(-1)?.at(-1)).toContain(`app verification **${settled}**`);
    const log = harness.records.join('\n');
    expect(log).toContain('Verify application · spec #1 failed');
    expect(log).toContain(`verdict=${settled}`);
  });

  test('a skill-less repository skips verification', async () => {
    verifier();

    const code = await deliver();

    expect(code).toBe(0);
    expect(harness.runner.verifyPrompts).toEqual([]);
    expect(harness.records.join('\n')).toContain('Verify application · spec #1 skipped');
  });

  test('cleanup changes are committed as one chore commit', async () => {
    let manifest = false;
    harness.runner.choreHandler = () => {
      manifest = existsSync(join(harness.worktree, 'bin/review_diff/_manifest.tsv'));
      harness.git.edit('src/Basket.cs');
      return envelope('', 'completed', { commit_message: 'Simplify basket', summary: 'Removed duplication' });
    };

    const code = await deliver();

    expect(code).toBe(0);
    expect(manifest).toBe(true);
    expect(harness.git.commits.at(-1)?.subject).toBe('chore(Checkout): Simplify basket');
    expect(harness.git.pushes).toHaveLength(2);
    expect(existsSync(join(harness.worktree, 'bin/review_diff'))).toBe(false);
  });

  test('a cleanup failure is discarded and leaves the delivered commit', async () => {
    harness.runner.choreHandler = crash();

    const code = await deliver();

    expect(code).toBe(0);
    expect(harness.git.commits).toHaveLength(2);
    expect(harness.git.restores.length).toBeGreaterThan(0);
    expect(harness.records.join('\n')).toContain('Refine changes · spec #1 failed');
  });

  test('a cleanup agent that commits is discarded', async () => {
    harness.runner.choreHandler = () => {
      harness.git.agentCommit('chore: by the agent');
      return envelope('', 'completed', { summary: 's' });
    };

    const code = await deliver();

    expect(code).toBe(0);
    expect(harness.git.commits.map((commit) => commit.subject)).not.toContain('chore: by the agent');
  });
});

describe('plan validation', () => {
  test('a plan blocked on a later Ticket is rejected', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = () =>
      envelope('', 'completed', { tickets: [{ blocked_by: [1], body: 'b', title: 'A' }, { body: 'b', title: 'B' }] });

    const code = await target.run(undefined, { allPhases: true });

    expect(code).toBe(1);
    expect(target.calls('issue', 'comment').at(-1)?.at(-1)).toContain('is blocked by a Ticket that does not come before it: [1]');
  });

  test('a Ticket without a title is rejected', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = () => envelope('', 'completed', { tickets: [{ body: 'b', title: ' ' }] });

    expect(await target.run(undefined, { allPhases: true })).toBe(1);
  });

  test('more than fifteen Tickets are rejected', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = () =>
      envelope('', 'completed', { tickets: Array.from({ length: 16 }, (_, index) => ({ body: 'b', title: `T${String(index)}` })) });

    const code = await target.run(undefined, { allPhases: true });

    expect(code).toBe(1);
    expect(target.calls('issue', 'comment').at(-1)?.at(-1)).toContain('the planner returned 16 Tickets; the limit is 15');
  });

  test('a planner that fails with an error is reported with its reason', async () => {
    const target = new DevHarness(directory, { tickets: new Map([[1, []]]) });
    target.runner.planHandler = crash();

    const code = await target.run(undefined, { allPhases: true });

    expect(code).toBe(1);
    expect(target.calls('issue', 'comment').at(-1)?.at(-1)).toContain('the planner failed');
  });
});

describe('the host owns the platform', () => {
  test('push, pull request and Ticket state change after the agent finished, on the host', async () => {
    const during = { pushes: -1, writes: -1 };

    const code = await harness.run(() => {
      during.pushes = harness.git.pushes.length;
      during.writes = harness.writes().length;
      return harness.commitAndReport(10);
    });

    expect(code).toBe(0);
    expect(during).toEqual({ pushes: 0, writes: 0 });
    expect(harness.prompts[0]).toContain('Never push, open a pull request, or comment, label, or close any Ticket or Spec');
    expect(harness.git.pushes).toHaveLength(1);
    expect(harness.calls('pr', 'create')).toHaveLength(1);
    expect(callNumbers(harness.calls('issue', 'close'))).toEqual(['10']);
  });
});
