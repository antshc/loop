import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { CommandError, runCommand } from '../commands.ts';
import { githubFor, GitHubRepo, PullRequestClient, TicketsTracker } from '../platforms.ts';
import { RepositoryId, Spec, TicketWorkflowError } from '../records.ts';
import { FakeGh } from './support/fake-gh.ts';

let directory: string;

beforeEach(() => {
  directory = realpathSync(mkdtempSync(join(tmpdir(), 'dev-platforms-')));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

function spec(): Spec {
  return new Spec({ labels: [], number: 1, title: 't', url: 'u' });
}

function tracker(gh: FakeGh = new FakeGh()): TicketsTracker {
  return new TicketsTracker('owner', 'repo', gh.run);
}

describe('the tracker', () => {
  test('maps each Ticket write to the matching gh command', async () => {
    const gh = new FakeGh();
    const target = spec();
    target.comment('hello');
    target.handToHuman('done');

    await tracker(gh).updateSpec(target);

    expect(gh.calls).toEqual([
      ['issue', 'comment', '1', '--repo', 'owner/repo', '--body', 'hello'],
      ['issue', 'edit', '1', '--repo', 'owner/repo', '--add-label', 'hitl'],
      ['issue', 'comment', '1', '--repo', 'owner/repo', '--body', 'done'],
    ]);
    expect(target.pendingChanges).toEqual([]);
  });

  test('closes a Ticket with its comment', async () => {
    const gh = new FakeGh();
    const [ticket] = (await tracker(gh).specs())[0]?.tickets ?? [];
    const target = new Spec({ labels: [], number: 1, tickets: ticket === undefined ? [] : [ticket], title: 't', url: 'u' });
    target.closeTicket(10, 'delivered');

    await tracker(gh).updateSpec(target);

    expect(gh.calls.at(-1)).toEqual(['issue', 'close', '10', '--repo', 'owner/repo', '--comment', 'delivered']);
  });

  test('a failed change stays pending and its command error reaches the caller', async () => {
    const target = spec();
    target.comment('x');
    target.comment('y');
    const failing = new TicketsTracker('owner', 'repo', () => Promise.reject(new CommandError('gh issue comment 1', 1, 'not found')));

    const error = await failing.updateSpec(target).then(
      () => null,
      (failure: unknown) => failure,
    );

    expect(error).toBeInstanceOf(CommandError);
    expect((error as Error).message).toContain('not found');
    expect(target.pendingChanges).toHaveLength(2);
  });

  test('loads every open Spec with its deliverable Tickets and the held ones apart', async () => {
    const specs = await tracker().specs();

    expect(specs.map((item) => item.number)).toEqual([1, 2]);
    const [first] = specs;
    expect(first?.tickets.map((ticket) => ticket.number)).toEqual([10]);
    expect(first?.heldTickets).toEqual([11, 12]);
    expect(first?.subIssueTotal).toBe(4);
    expect(first?.comments.map((comment) => [comment.author, comment.body, comment.createdAt])).toEqual([
      ['ann', 'hello', '2026-01-01T00:00:00Z'],
      ['ghost', 'bye', '2026-01-02T00:00:00Z'],
    ]);
    expect(first?.needsPlan).toBe(false);
    expect(specs[1]?.needsPlan).toBe(true);
  });

  test('creates planned Tickets as sub-issues in order with their blockers', async () => {
    const gh = new FakeGh();
    const target = new Spec({ labels: ['spec'], number: 2, title: 'Add logout button', url: 'u' });

    const created = await tracker(gh).createTickets(target, [
      { body: 'first', title: 'One' },
      { blockedBy: [0, 5], body: 'second\n', title: 'Two' },
    ]);

    expect(created.map((ticket) => [ticket.number, ticket.body])).toEqual([
      [101, 'first'],
      [102, 'second\n\nBlocked by #101'],
    ]);
    expect(target.tickets).toEqual(created);
    expect(target.subIssueTotal).toBe(2);
    expect((await tracker(gh).specs())[1]?.tickets.map((ticket) => ticket.number)).toEqual([101, 102]);
  });

  test('names the Tickets already created when creating one fails', async () => {
    let issues = 0;
    const gh = (args: readonly string[]): Promise<string> => {
      if (args[0] === 'issue') {
        issues += 1;
        return issues === 1 ? Promise.resolve('https://github.com/owner/repo/issues/7') : Promise.reject(new CommandError('gh issue create', 1, 'boom'));
      }
      return Promise.resolve('55');
    };

    const error = await new TicketsTracker('owner', 'repo', gh)
      .createTickets(spec(), [
        { body: 'a', title: 'A' },
        { body: 'b', title: 'B' },
      ])
      .then(
        () => null,
        (failure: unknown) => failure,
      );

    expect(error).toBeInstanceOf(TicketWorkflowError);
    expect((error as Error).message).toContain('creating Tickets failed after creating #7');
  });
});

describe('the pull request client', () => {
  const client = (gh: FakeGh): PullRequestClient => new PullRequestClient(new GitHubRepo('owner', 'repo', gh.run));

  test('reuses an open PR without creating another', async () => {
    const gh = new FakeGh();

    const pullRequest = await client(gh).createDraftPullRequest('feature/login', 'main', 'My PR');

    expect([pullRequest.number, pullRequest.branch, pullRequest.url]).toEqual([
      10,
      'feature/login',
      'https://github.com/owner/repo/pull/10',
    ]);
    expect(gh.calls.every((call) => !(call[0] === 'pr' && call[1] === 'create'))).toBe(true);
  });

  test('asks gh for a draft from head to base', async () => {
    const gh = new FakeGh();

    const pullRequest = await client(gh).createDraftPullRequest('feature/x', 'main', 'My PR', 'body');

    expect([pullRequest.number, pullRequest.title, pullRequest.branch]).toEqual([99, 'My PR', 'feature/x']);
    expect(pullRequest.url).toBe('https://github.com/owner/repo/pull/99');
    expect(gh.calls.at(-1)).toEqual([
      'pr', 'create', '--repo', 'owner/repo', '--head', 'feature/x', '--base', 'main',
      '--title', 'My PR', '--body', 'body', '--draft',
    ]);
  });

  test('refreshes the Ticket checklist of a reused pull request', async () => {
    const gh = new FakeGh();
    const pulls = client(gh);

    const first = await pulls.createDraftPullRequest('feature/basket', 'main', 'Basket', '- [x] #1\n- [ ] #2');
    const updated = await pulls.createDraftPullRequest('feature/basket', 'main', 'Basket', '- [x] #1\n- [x] #2');

    expect([first.number, updated.number]).toEqual([99, 99]);
    expect(gh.calls.filter((call) => call[0] === 'pr' && call[1] === 'create')).toHaveLength(1);
    expect(gh.calls.at(-1)).toEqual(['pr', 'edit', '99', '--repo', 'owner/repo', '--body', '- [x] #1\n- [x] #2']);
  });

  test('takes a pull request out of draft', async () => {
    const gh = new FakeGh();

    await client(gh).markReady('99');

    expect(gh.calls).toEqual([['pr', 'ready', '99', '--repo', 'owner/repo']]);
  });
});

describe('the GitHub repository', () => {
  test('reads the origin remote', async () => {
    const repo = join(directory, 'repo');
    mkdirSync(repo);
    await runCommand(['git', 'init', '-q', '-b', 'main'], { cwd: repo });
    writeFileSync(join(repo, 'README.md'), 'hello\n');
    await runCommand(['git', 'remote', 'add', 'origin', 'https://example.com/owner/repo.git'], { cwd: repo });

    const unsupported = await GitHubRepo.fromOrigin(repo).then(
      () => null,
      (failure: unknown) => failure,
    );
    expect((unsupported as Error).message).toContain('unsupported remote');

    await runCommand(['git', 'remote', 'set-url', 'origin', 'git@github.com:acme/harness.git'], { cwd: repo });
    expect((await GitHubRepo.fromOrigin(repo)).slug).toBe('acme/harness');
  });

  test('passes GraphQL variables as typed fields', async () => {
    const gh = new FakeGh();

    await new GitHubRepo('o', 'r', gh.run).graphql('q', { paginate: true, variables: { number: 3, word: 'x' } });
    await new GitHubRepo('o', 'r', gh.run).graphql('q', { scoped: false });

    expect(gh.calls).toEqual([
      ['api', 'graphql', '--paginate', '--slurp', '-f', 'query=q', '-f', 'owner=o', '-f', 'repo=r', '-F', 'number=3', '-f', 'word=x'],
      ['api', 'graphql', '-f', 'query=q'],
    ]);
  });

  test('uses the injected factory when supplied', () => {
    const repository = { checkout: directory, ownerName: new RepositoryId('a', 'b'), worktrees: directory };
    const injected = new GitHubRepo('x', 'y', () => Promise.resolve(''));

    expect(githubFor(repository, () => injected)).toBe(injected);
    expect(githubFor(repository, null).slug).toBe('a/b');
  });
});
