import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { ActionsRun, GithubService, PullRequest, ReplyTarget, Snapshot } from '../../infrastructure/github.ts';

import { type AgentBuilder, fakeAgent, FakeCliRunner, FakeReply, GitCli, GitRuntime } from '../../../../src/loop/index.ts';
import { Git } from '../../infrastructure/git.ts';
import { Repository, Settings } from '../../settings.ts';

export interface Scenario {
  bare: string;
  /** The commit the remote branch starts at. */
  base: string;
  checkout: string;
  root: string;
  settings: Settings;
}

type ThreadComment = Snapshot['threads'][number]['comments'][number];

export const NO_OP = [process.execPath, '-e', ''];

export interface ScriptedAgents {
  readonly modes: string[];
  /** Builds a fake agent whose replies come from the scripted Runner. */
  newAgent: () => AgentBuilder;
}

/** The workflow's Git with the identity check skipped, because the test remote is a local path. */
export class LocalGit extends Git {
  override validate(): Promise<void> {
    return Promise.resolve();
  }
}

/** An in-memory GitHub whose PR head is the `feature` branch of a local bare repository. */
export class Remote implements GithubService {
  readonly ciFailure: boolean;
  closed = false;
  discussions: Snapshot['threads'];
  readonly replies: string[] = [];
  readonly retries: number[] = [];
  readonly #bare: string;
  readonly #base: string;

  constructor(bare: string, base: string, options: { ciFailure?: boolean; reviews?: boolean } = {}) {
    this.#bare = bare;
    this.#base = base;
    this.discussions = options.reviews === false ? [] : [thread()];
    this.ciFailure = options.ciFailure ?? false;
  }

  login(): Promise<string> {
    return Promise.resolve('me');
  }

  logs(): Promise<string> {
    return Promise.resolve('cart.py: duplicate product');
  }

  openPrs(): Promise<PullRequest[]> {
    return Promise.resolve([]);
  }

  pullRequest(repo: string, number: number): Promise<PullRequest> {
    return Promise.resolve(this.#pr(repo, number));
  }

  reply(_repo: string, _number: number, _item: ReplyTarget, body: string): Promise<void> {
    this.replies.push(body);
    this.discussions[0]?.comments.push(comment(`reply${String(this.replies.length)}`, 'me', body));
    return Promise.resolve();
  }

  requestCopilot(): Promise<void> {
    return Promise.resolve();
  }

  retry(_repo: string, runId: number): Promise<void> {
    this.retries.push(runId);
    return Promise.resolve();
  }

  snapshot(repo: string, number: number): Promise<Snapshot> {
    const pr = this.#pr(repo, number);
    const failed = this.ciFailure && pr.head.sha === this.#base;
    const runs: ActionsRun[] = failed
      ? [{ conclusion: 'failure', head_sha: pr.head.sha, id: 1, status: 'completed', workflow_id: 1 }]
      : [];
    return Promise.resolve({
      checks: [{ conclusion: failed ? 'failure' : 'success', id: 1, name: 'test', status: 'completed' }],
      other: [],
      pr,
      runs,
      statuses: [],
      threads: structuredClone(this.discussions),
    });
  }

  #pr(repo: string, number: number): PullRequest {
    const sha = git(this.#bare, 'rev-parse', 'refs/heads/feature');
    return {
      draft: false,
      head: { ref: 'feature', repo: { full_name: repo }, sha },
      html_url: `https://github.com/${repo}/pull/${String(number)}`,
      mergeable: true,
      mergeable_state: 'clean',
      merged: this.closed,
      number,
      state: this.closed ? 'closed' : 'open',
      user: { login: 'me' },
    };
  }
}

export function comment(id = 'c1', login = 'reviewer', body = 'Check this'): ThreadComment {
  return { author: { login }, authorAssociation: 'COLLABORATOR', body, id };
}

/** Run git in `path` and return trimmed stdout; throws when git fails. */
export function git(path: string, ...args: string[]): string {
  const target = path.endsWith('.git') ? ['--git-dir', path] : ['-C', path];
  const result = Bun.spawnSync(['git', ...target, ...args], { stderr: 'pipe', stdout: 'pipe' });
  if (result.exitCode !== 0) throw new Error(`git ${args.join(' ')} failed: ${result.stderr.toString()}`);
  return result.stdout.toString().trim();
}

/** A checkout and a bare remote sharing one `feature` branch, plus settings that point at them. */
export function scenario(): Scenario {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-')));
  const bare = join(root, 'remote.git');
  const checkout = join(root, 'repo');
  mkdirSync(bare);
  mkdirSync(checkout);
  git(root, 'init', '--bare', bare);
  git(checkout, 'init');
  git(checkout, 'config', 'user.name', 'Test');
  git(checkout, 'config', 'user.email', 'test@example.com');
  writeFileSync(join(checkout, 'cart.py'), 'fixed = False\n');
  git(checkout, 'add', '.');
  git(checkout, 'commit', '-m', 'initial');
  git(checkout, 'branch', '-M', 'feature');
  git(checkout, 'remote', 'add', 'origin', bare);
  git(checkout, 'push', 'origin', 'feature');
  const settings = new Settings({
    interval: 0.01,
    repositories: [new Repository('a/b', checkout, NO_OP, NO_OP)],
    stateDir: join(root, 'state'),
  });
  return { bare, base: git(checkout, 'rev-parse', 'HEAD'), checkout, root, settings };
}

/** Answers the Workflow's JSON requests the way a cooperative agent would. */
export function scriptedAgents(options: { invalid?: boolean; transient?: boolean } = {}): ScriptedAgents {
  const modes: string[] = [];
  const runner = new FakeCliRunner(
    (request, context) => {
      const task = JSON.parse(request.prompt.split('## Task\n').at(-1) ?? '') as {
        issue: { keys: string[] };
        items: { key: string }[];
        mode: string;
      };
      modes.push(task.mode);
      let output: unknown;
      if (task.mode === 'group') {
        output = { issues: [{ id: 'cart', keys: [task.items[0]?.key], statement: 'duplicate cart entry' }] };
      } else if (task.mode === 'resolve') {
        writeFileSync(join(context.agent.cwd, 'cart.py'), 'fixed = True\n');
        output = {
          evidence: ['cart.py:1: duplicate prevented'],
          replies: Object.fromEntries(task.issue.keys.map((key) => [key, 'Fixed!'])),
          verdict: 'fix-needed',
        };
      } else if (task.mode === 'diagnose') {
        output = { classification: options.transient === true ? 'transient' : 'branch', reason: 'CI evidence' };
      } else {
        writeFileSync(join(context.agent.cwd, 'cart.py'), 'fixed = True\n');
        output = { reason: 'fixed cart', verdict: 'fixed' };
      }
      return new FakeReply(options.invalid === true ? 'not json' : JSON.stringify(output));
    },
    { sleep: () => Promise.resolve() },
  );
  return { modes, newAgent: () => fakeAgent(runner, new GitRuntime(new GitCli())) };
}

export function thread(comments: ThreadComment[] = [comment()], resolved = false): Snapshot['threads'][number] {
  return { comments, id: 't1', isOutdated: false, isResolved: resolved, line: 4, path: 'cart.py' };
}
