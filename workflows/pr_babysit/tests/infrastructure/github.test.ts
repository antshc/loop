import { describe, expect, test } from 'bun:test';

import type { ProcessResult, ProcessRunner } from '../../../../src/loop/index.ts';

import { ProcessFailedError, ValueError } from '../../../../src/loop/index.ts';
import { digest, Github, remoteFingerprint, type Snapshot } from '../../infrastructure/github.ts';

type Reply = object | Respond;
type Respond = (argv: readonly string[]) => unknown;

const isRespond = (reply: Reply): reply is Respond => typeof reply === 'function';

/** A fake `gh`: records every invocation and answers with JSON from `reply`. */
function fakeGh(reply: Reply): { commands: string[][]; runner: ProcessRunner } {
  const commands: string[][] = [];
  const runner: ProcessRunner = {
    run: (argv, options) => {
      commands.push([...argv]);
      expect(argv[0]).toBe('gh');
      expect(options?.check).toBe(true);
      expect(options?.timeoutSec).toBe(120);
      const payload = isRespond(reply) ? reply(argv) : reply;
      const result: ProcessResult = { argv, exitCode: 0, signal: null, stderr: '', stdout: JSON.stringify(payload) };
      return Promise.resolve(result);
    },
    start: () => {
      throw new Error('gh is never started in the background');
    },
  };
  return { commands, runner };
}

describe('REST collections', () => {
  test('paginate and keep only the configured author, ignoring case', async () => {
    const { commands, runner } = fakeGh([
      [{ number: 1, user: { login: 'Me' } }],
      [{ number: 2, user: { login: 'other' } }],
    ]);

    const prs = await new Github(runner).openPrs('a/b', 'me');

    expect(prs.map((pr) => pr.number)).toEqual([1]);
    expect(commands[0]).toContain('--paginate');
    expect(commands[0]).toContain('--slurp');
  });
});

describe('review threads', () => {
  test('page through threads and comments, omitting the empty initial cursor', async () => {
    const { commands, runner } = fakeGh((argv) => {
      if (argv.some((arg) => arg.startsWith('query=query($id'))) {
        return { data: { node: { comments: { nodes: [{ id: 'c2' }], pageInfo: { endCursor: null, hasNextPage: false } } } } };
      }
      const empty = { nodes: [], pageInfo: { endCursor: null, hasNextPage: false } };
      if (argv.includes('cursor=next-thread')) {
        return { data: { repository: { pullRequest: { reviewThreads: empty } } } };
      }
      expect(argv).not.toContain('cursor=');
      const nodes = [
        { comments: { nodes: [{ id: 'c1' }], pageInfo: { endCursor: 'next-comment', hasNextPage: true } }, id: 't1' },
      ];
      return {
        data: { repository: { pullRequest: { reviewThreads: { nodes, pageInfo: { endCursor: 'next-thread', hasNextPage: true } } } } },
      };
    });

    const threads = await new Github(runner).threads('a/b', 1);

    expect(threads[0]?.comments.map((comment) => comment.id)).toEqual(['c1', 'c2']);
    expect(commands).toHaveLength(3);
  });

  test('pending review comments are not exposed', async () => {
    const nodes = [
      {
        comments: {
          nodes: [{ id: 'c1' }, { id: 'c2', pullRequestReview: { state: 'PENDING' } }],
          pageInfo: { endCursor: null, hasNextPage: false },
        },
        id: 't1',
      },
    ];
    const { runner } = fakeGh({
      data: { repository: { pullRequest: { reviewThreads: { nodes, pageInfo: { endCursor: null, hasNextPage: false } } } } },
    });

    const threads = await new Github(runner).threads('a/b', 1);

    expect(threads[0]?.comments.map((comment) => comment.id)).toEqual(['c1']);
  });
});

describe('mutations', () => {
  test('GraphQL errors cannot be marked successful', async () => {
    const { runner } = fakeGh({ errors: [{ message: 'forbidden' }] });

    const failure = await new Github(runner).reply('a/b', 1, { kind: 'thread', thread_id: 't1' }, 'Fixed!').catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ValueError);
    expect((failure as Error).message).toContain('forbidden');
  });

  test('a thread reply posts through GraphQL and a PR-level reply through the issue comments API', async () => {
    const { commands, runner } = fakeGh({});
    const github = new Github(runner);

    await github.reply('a/b', 7, { kind: 'thread', thread_id: 't1' }, 'one');
    await github.reply('a/b', 7, { kind: 'comment' }, 'two');

    expect(commands[0]).toContain('id=t1');
    expect(commands[1]).toEqual(['gh', 'api', 'repos/a/b/issues/7/comments', '-X', 'POST', '-f', 'body=two']);
  });

  test('retry reruns only the failed jobs and a failing gh surfaces as ProcessFailedError', async () => {
    const runner: ProcessRunner = {
      run: (argv) => Promise.reject(new ProcessFailedError({ argv, exitCode: 1, signal: null, stderr: '', stdout: '' })),
      start: () => {
        throw new Error('unused');
      },
    };

    expect(await new Github(runner).retry('a/b', 5).catch((error: unknown) => error)).toBeInstanceOf(ProcessFailedError);
    const { commands, runner: ok } = fakeGh({});
    await new Github(ok).retry('a/b', 5);
    expect(commands[0]).toEqual(['gh', 'run', 'rerun', '5', '-R', 'a/b', '--failed']);
  });

  test('an unparseable response is a ValueError', async () => {
    const runner: ProcessRunner = {
      run: (argv) => Promise.resolve({ argv, exitCode: 0, signal: null, stderr: '', stdout: 'not json' }),
      start: () => {
        throw new Error('unused');
      },
    };

    expect(await new Github(runner).login().catch((error: unknown) => error)).toBeInstanceOf(ValueError);
  });
});

describe('digests', () => {
  test('are byte-identical to the Python digest, including fractions and non-BMP text', () => {
    expect(digest({ '': '', a: { x: [], y: {} }, z: [1, 2.5, null, true, 'café ☃ 😀'] })).toBe(
      '287224a4cd079046e40ed3f162399f5751595856eeed13102868d1e9b9c81b6a',
    );
  });

  test('the remote fingerprint changes only with review and CI inputs', () => {
    const snapshot: Snapshot = {
      checks: [{ conclusion: null, id: 1, name: 'test', status: 'in_progress' }],
      other: [],
      pr: {
        draft: false,
        head: { ref: 'feature', sha: 'sha1' },
        html_url: 'u',
        number: 1,
        state: 'open',
        user: { login: 'me' },
      },
      runs: [],
      statuses: [],
      threads: [],
    };
    const before = remoteFingerprint(snapshot);

    expect(remoteFingerprint({ ...snapshot, pr: { ...snapshot.pr, draft: true } })).toBe(before);
    expect(remoteFingerprint({ ...snapshot, pr: { ...snapshot.pr, head: { ...snapshot.pr.head, sha: 'sha2' } } })).not.toBe(before);
  });
});
