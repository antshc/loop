import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  type AgentBuilder,
  AgentProfile,
  AgentRequest,
  type AgentWorkspace,
  BranchStrategy,
  copilot,
} from '../../../src/loop/index.ts';
import { loadPrompt, renderPrompt } from '../agent.ts';
import { type DevDeps, processSpecs } from '../backlog.ts';
import { devWorkflow } from '../definition.ts';
import { FileExecutionStore } from '../journal.ts';
import { type GithubFactory, GitHubRepo, TicketsTracker } from '../platforms.ts';
import { type Repository, RepositoryId, type TicketContext, type TicketResult } from '../records.ts';
import { FakeGh } from './support/fake-gh.ts';
import {
  DirectoryWorktree,
  FakeRepositoryGh,
  FakeRepositoryGit,
  FakeRunner,
  FakeWorkflowGit,
  issue,
  newAgentFor,
  testDirectory,
} from './support/harness.ts';

let directory: string;

beforeEach(() => {
  directory = testDirectory('dev-routing-');
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

function repository(name: string): Repository {
  return {
    checkout: join(directory, 'workspace', name),
    ownerName: new RepositoryId('owner', name),
    worktrees: join(directory, 'workspace', `${name}.worktrees`),
  };
}

describe('harness Specs route to separate repositories', () => {
  test.each([false, true])('with agent delegates of their own (first Ticket fails: %p)', async (firstFails) => {
    const prompts = join(directory, 'prompts');
    mkdirSync(prompts);
    writeFileSync(join(prompts, 'first.md'), 'Implement {{TITLE}} for the first repository');
    writeFileSync(join(prompts, 'second.md'), 'Implement {{TITLE}} for the second repository');
    const harness = repository('harness');
    const one = repository('one');
    const two = repository('two');
    const onePath = join(one.worktrees, 'first');
    const twoPath = join(two.worktrees, '2_4_second');
    const oneGit = new FakeWorkflowGit();
    const twoGit = new FakeWorkflowGit();
    oneGit.agentCommit('ccode(Checkout|9): one history');
    twoGit.agentCommit('ccode(Checkout|19): two history');
    oneGit.pushedCount = 2;
    twoGit.pushedCount = 2;
    twoGit.remoteBranches = new Set(['release/2.4']);
    const git = new FakeRepositoryGit(
      new Map([
        [one.checkout, oneGit],
        [onePath, oneGit],
        [two.checkout, twoGit],
        [twoPath, twoGit],
      ]),
    );
    const trackerGh = new FakeGh({
      specs: [
        issue(1, 'Checkout: First', { labels: ['spec', 'repo:target:OWNER/ONE', 'repo:base:main'] }),
        issue(2, 'Checkout: Second', { labels: ['spec', 'repo:target:owner/two', 'repo:base:release/2.4'] }),
      ],
      tickets: new Map([
        [1, [issue(10, 'First task')]],
        [2, [issue(20, 'Second task')]],
      ]),
    });
    const oneGh = new FakeRepositoryGh(one.ownerName);
    const twoGh = new FakeRepositoryGh(two.ownerName);
    const githubs = new Map([
      [harness.checkout, { gh: trackerGh, id: harness.ownerName }],
      [one.checkout, { gh: oneGh, id: one.ownerName }],
      [two.checkout, { gh: twoGh, id: two.ownerName }],
    ]);
    const githubPaths: string[] = [];
    const githubFactory: GithubFactory = (checkout) => {
      githubPaths.push(checkout);
      const target = githubs.get(checkout);
      if (target === undefined) {
        throw new RangeError(`unexpected checkout ${checkout}`);
      }
      return new GitHubRepo(target.id.owner, target.id.repo, target.gh.run);
    };
    const implementer = new AgentProfile(copilot, { model: 'implementation-model' });
    const oneRunner = new FakeRunner();
    const twoRunner = new FakeRunner();
    const oneService = new DirectoryWorktree(onePath);
    const twoService = new DirectoryWorktree(twoPath);
    const builders: (() => AgentBuilder)[] = [
      newAgentFor(oneRunner, oneService, harness.checkout),
      newAgentFor(twoRunner, twoService, harness.checkout),
    ];
    const delegates: string[] = [];

    const implementOne = async (context: TicketContext, worktree: AgentWorkspace): Promise<TicketResult> => {
      delegates.push('one');
      expect(context.ticket.number).toBe(10);
      expect(context.initiativeCommits).toEqual(['c000000 ccode(Checkout|9): one history']);
      expect([context.baseBranch, context.featureBranch]).toEqual(['main', 'first']);
      const prompt = renderPrompt(loadPrompt('first', prompts), { TITLE: context.ticket.title });
      await worktree.agent(implementer).run(new AgentRequest(prompt));
      const sha = oneGit.agentCommit(`${context.identifier.toSubject()}first`);
      return firstFails
        ? { identifier: context.identifier.toString(), reason: 'cannot deliver first', status: 'failed' }
        : { commit: sha, identifier: context.identifier.toString(), status: 'completed', summary: 'first delivered', verification: 'tested' };
    };
    const implementTwo = async (context: TicketContext, worktree: AgentWorkspace): Promise<TicketResult> => {
      delegates.push('two');
      expect(context.ticket.number).toBe(20);
      expect(context.initiativeCommits).toEqual(['c000000 ccode(Checkout|19): two history']);
      expect([context.baseBranch, context.featureBranch]).toEqual(['release/2.4', '2_4_second']);
      const prompt = renderPrompt(loadPrompt('second', prompts), { TITLE: context.ticket.title });
      await worktree.agent(implementer).run(new AgentRequest(prompt));
      const sha = twoGit.agentCommit(`${context.identifier.toSubject()}second`);
      return { commit: sha, identifier: context.identifier.toString(), status: 'completed', summary: 'second delivered', verification: 'tested' };
    };

    const code = await devWorkflow().run((phases) => {
      const trackerGithub = githubFactory(harness.checkout);
      const deps: DevDeps = {
        git,
        githubFactory,
        logDir: join(directory, 'logs'),
        loopHooks: [],
        maxTicketFailures: 2,
        newAgent: () => {
          const next = builders.shift();
          if (next === undefined) {
            throw new RangeError('more agents were opened than expected');
          }
          return next();
        },
        phases,
        specNumber: null,
        store: new FileExecutionStore(join(directory, 'logs')),
        ticketRepository: harness.ownerName,
        tracker: new TicketsTracker('owner', 'harness', (args) => trackerGithub.run(args)),
      };
      return processSpecs(
        [
          { repository: one, runAgents: implementOne },
          { repository: two, runAgents: implementTwo },
        ],
        deps,
      );
    });

    expect(code).toBe(firstFails ? 1 : 0);
    expect(delegates).toEqual(firstFails ? ['one', 'one', 'two'] : ['one', 'two']);
    expect(oneGit.fetched).toEqual([one.checkout]);
    expect(twoGit.fetched).toEqual([two.checkout]);
    expect(oneService.opened[0]?.repositoryPath).toBe(one.checkout);
    expect(twoService.opened[0]?.repositoryPath).toBe(two.checkout);
    expect(oneService.opened[0]?.rootPath).toBe(one.worktrees);
    expect(twoService.opened[0]?.rootPath).toBe(two.worktrees);
    expect(oneService.opened[0]?.strategy).toEqual(new BranchStrategy('first', 'origin/main'));
    expect(twoService.opened[0]?.strategy).toEqual(new BranchStrategy('2_4_second', 'origin/release/2.4'));
    expect([oneService.closed, twoService.closed]).toEqual([1, 1]);
    expect(oneRunner.prompts).toEqual(Array.from({ length: firstFails ? 2 : 1 }, () => 'Implement First task for the first repository'));
    expect(twoRunner.prompts).toEqual(['Implement Second task for the second repository']);
    expect(oneRunner.profiles).toEqual(Array.from({ length: firstFails ? 2 : 1 }, () => implementer));
    expect(githubPaths).toEqual(firstFails ? [harness.checkout, two.checkout] : [harness.checkout, one.checkout, two.checkout]);
    expect(oneGit.pushes).toEqual(firstFails ? [] : [[onePath, 'first']]);
    expect(twoGit.pushes).toEqual([[twoPath, '2_4_second']]);
    expect(twoGit.commits).toHaveLength(3);
    for (const [target, github] of [[one, oneGh], [two, twoGh]] as const) {
      expect(github.calls.every((call) => call[0] === 'pr' && call[call.indexOf('--repo') + 1] === target.ownerName.toString())).toBe(true);
    }
    const writes = trackerGh.calls.filter((call) => call[0] === 'issue');
    expect(writes.every((call) => call[call.indexOf('--repo') + 1] === 'owner/harness')).toBe(true);
    expect(trackerGh.calls.some((call) => call[0] === 'pr')).toBe(false);
    expect(writes.filter((call) => call[1] === 'close').map((call) => call[2])).toEqual(firstFails ? ['20'] : ['10', '20']);
    const announcements = writes.filter((call) => call[1] === 'comment').map((call) => call.at(-1) ?? '');
    expect(announcements.some((body) => body.includes('https://github.com/owner/two/pull/99'))).toBe(true);
    expect(announcements.some((body) => body.includes('https://github.com/owner/one/pull/99'))).toBe(!firstFails);
  });

  test('a duplicate case-insensitive repository configuration fails before processing Specs', async () => {
    const harness = repository('repo');
    const duplicate = { ...harness, ownerName: new RepositoryId('OWNER', 'REPO') };
    const gh = new FakeRepositoryGh(harness.ownerName);
    const delegate = (): Promise<TicketResult> => Promise.reject(new Error('a duplicate configuration must not invoke a delegate'));

    const error = await devWorkflow()
      .run((phases) =>
        processSpecs(
          [
            { repository: harness, runAgents: delegate },
            { repository: duplicate, runAgents: delegate },
          ],
          {
            git: new FakeWorkflowGit(),
            githubFactory: null,
            logDir: directory,
            loopHooks: [],
            maxTicketFailures: 2,
            newAgent: () => {
              throw new Error('no agent must open');
            },
            phases,
            specNumber: null,
            store: new FileExecutionStore(directory),
            ticketRepository: harness.ownerName,
            tracker: new TicketsTracker('owner', 'repo', (args) => gh.run(args)),
          },
        ),
      )
      .then(
        () => null,
        (failure: unknown) => failure,
      );

    expect((error as Error).message).toContain('duplicate repository workflow');
    expect(gh.calls).toEqual([]);
  });
});
