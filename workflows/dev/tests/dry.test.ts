import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { GitService } from '../checkout.ts';

import { AgentRequest, RunContext, ValueError } from '../../../src/loop/index.ts';
import { runCommand } from '../commands.ts';
import { commitInWorktree, DryGh, DryGit, Fakes, ScriptedAgent } from '../dry.ts';

interface Golden {
  agent: Record<string, { output: string; prompt: string; tools: object[]; usage: object }>;
  git: {
    afterTwoCommits: [string, string][];
    head: string;
    pushedFirst: boolean;
    pushedSecond: boolean;
    refSha: string;
  };
  github: Record<string, { args: string[]; output: string }>;
}

const GOLDEN = JSON.parse(readFileSync(join(import.meta.dir, 'fixtures', 'dry-run.json'), 'utf8')) as Golden;

let directory: string;

beforeEach(() => {
  directory = realpathSync(mkdtempSync(join(tmpdir(), 'dev-dry-')));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

describe('the dry-run fakes match the Python Workflow', () => {
  test('the scripted agent answers every prompt with the same output, usage and tools', async () => {
    let commits = 0;
    const agent = new ScriptedAgent(() => `sha${String(++commits).padStart(4, '0')}`);

    for (const [name, expected] of Object.entries(GOLDEN.agent)) {
      const reply = await agent.respond(new AgentRequest(expected.prompt), new RunContext());

      expect(reply.output, name).toBe(expected.output);
      expect(
        {
          aiu: reply.usage.aiu,
          apiCalls: reply.usage.apiCalls,
          cacheReadTokens: reply.usage.cacheReadTokens,
          inputTokens: reply.usage.inputTokens,
          outputTokens: reply.usage.outputTokens,
          premiumRequests: reply.usage.premiumRequests,
        },
        name,
      ).toEqual(expected.usage as never);
      expect(
        reply.tools.map((tool) => ({
          arguments: tool.arguments,
          name: tool.name,
          ok: tool.ok,
          result: tool.result,
          seconds: tool.seconds,
        })),
        name,
      ).toEqual(expected.tools as never);
    }
  });

  test('canned GitHub reads and logged writes answer with the same text', async () => {
    const gh = new DryGh();

    for (const [name, call] of Object.entries(GOLDEN.github)) {
      expect(await gh.run(call.args), name).toBe(call.output);
    }
    expect(gh.calls).toHaveLength(Object.keys(GOLDEN.github).length);
  });

  test('the in-memory Git numbers commits, pushes once and reads the pushed head', async () => {
    const dry = new DryGit();
    const git: GitService = dry;
    dry.record('one');
    dry.record('two');

    expect(dry.commits.map((commit) => [commit.sha, commit.subject])).toEqual(GOLDEN.git.afterTwoCommits);
    expect((await git.head('.')).sha).toBe(GOLDEN.git.head);
    expect(await git.refSha('.', 'origin/main')).toBe(GOLDEN.git.refSha);
    expect(await git.pushIfAhead('.', 'b', 'main')).toBe(GOLDEN.git.pushedFirst);
    expect(await git.pushIfAhead('.', 'b', 'main')).toBe(GOLDEN.git.pushedSecond);
  });
});

describe('the in-memory Git', () => {
  test('restore drops the commits after a sha', async () => {
    const dry = new DryGit();
    const git: GitService = dry;
    const keep = dry.record('one');
    dry.record('two');

    await git.restore('.', keep);

    expect(dry.commits.map((commit) => commit.subject)).toEqual(['base', 'one']);
  });

  test('reads its history like the real service', async () => {
    const dry = new DryGit();
    const git: GitService = dry;
    const first = dry.record('ccode(X|1): a');
    await git.commit('.', 'other', 'body');

    expect((await git.commitsSince('.', first)).map((commit) => commit.subject)).toEqual(['other']);
    expect((await git.branchCommits('.', 'main')).map((commit) => commit.subject)).toEqual(['ccode(X|1): a', 'other']);
    expect((await git.initiativeCommits('.', 'main', 'ccode(X|')).map((commit) => commit.sha)).toEqual([first]);
    expect(await git.isClean('.')).toBe(true);
    expect(await git.remoteBranchExists('.', 'main')).toBe(true);
    expect((await git.head('.')).body).toBe('body');
  });
});

describe('the fakes', () => {
  test('each fake is offered only for its own seam', () => {
    const fakes = new Fakes(['github', 'store']);

    expect([fakes.git(), fakes.newAgent(), fakes.readers()]).toEqual([null, null, null]);
    expect(fakes.githubFactory()).not.toBeNull();
    expect(fakes.store()).not.toBeNull();
  });

  test('faking only the agent still wires the fake as the log reader', () => {
    const fakes = new Fakes(['agent']);

    expect(fakes.newAgent()).not.toBeNull();
    expect(fakes.readers()).toHaveLength(1);
  });

  test('a faked git without a faked agent is refused', () => {
    expect(() => new Fakes(['git'])).toThrow(ValueError);
    expect(() => new Fakes(['git'])).toThrow('faked agent');
  });

  test('an unknown seam is refused', () => {
    expect(() => new Fakes(['docker'])).toThrow('unknown seam docker');
  });

  test('a faked store keeps its log in a fresh temporary directory', async () => {
    const store = new Fakes(['store']).store();

    await store?.recordFailure('k', { items: [], owner: 'o', repo: 'r', taskId: '1', title: 't' });

    expect(await store?.failedAttempts('k')).toBe(1);
  });

  test('the canned GitHub repository is the dry-run target', () => {
    const repository = new Fakes(['github']).githubFactory()?.(directory);

    expect(repository?.slug).toBe('antshc/loop');
  });
});

describe('a fake agent over real Git', () => {
  test('commits in the worktree', async () => {
    await runCommand(['git', 'init', '-q'], { cwd: directory });
    await runCommand(
      ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'base'],
      { cwd: directory },
    );

    const sha = await commitInWorktree(directory, 'ccode(X|1): work');

    const head = await runCommand(['git', 'log', '-1', '--format=%H %s'], { cwd: directory });
    expect(head.trim()).toBe(`${sha} ccode(X|1): work`);
    expect(readdirSync(directory)).toContain('.git');
  });
});
