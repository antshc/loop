import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

import { capturePullRequestDiff, DIFF_DIR, MANIFEST_NAME } from '../chore.ts';
import { GitHubRepo } from '../platforms.ts';
import { TicketWorkflowError } from '../records.ts';
import { testDirectory } from './support/harness.ts';

let directory: string;

beforeEach(() => {
  directory = testDirectory('dev-chore-');
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

const github = (diff: string, calls: (readonly string[])[] = []): GitHubRepo =>
  new GitHubRepo('o', 'r', (args) => {
    calls.push(args);
    return Promise.resolve(diff);
  });

const manifest = (directoryOfDiffs: string): string[] =>
  readFileSync(join(directoryOfDiffs, MANIFEST_NAME), 'utf8').split('\n').filter((line) => line !== '');

describe('the captured pull request diff', () => {
  test('is split into one file per changed path with a manifest of the real paths', async () => {
    const calls: (readonly string[])[] = [];
    const diff = 'diff --git a/src/a.cs b/src/a.cs\n+one\ndiff --git a/old.cs b/new.cs\n+two\n';

    const target = await capturePullRequestDiff(github(diff, calls), 'https://github.com/o/r/pull/3', directory);

    expect(target).toBe(join(directory, DIFF_DIR));
    expect(calls).toEqual([['pr', 'diff', 'https://github.com/o/r/pull/3', '--repo', 'o/r']]);
    expect(manifest(target)).toEqual(['src/a.cs\tsrc_a.cs', 'new.cs\tnew.cs']);
    expect(readFileSync(join(target, 'src_a.cs'), 'utf8')).toBe('diff --git a/src/a.cs b/src/a.cs\n+one\n');
    expect(readFileSync(join(target, 'new.cs'), 'utf8')).toBe('diff --git a/old.cs b/new.cs\n+two\n');
  });

  test('keeps paths that flatten to the same name apart', async () => {
    const diff = 'diff --git a/a/b.cs b/a/b.cs\n+x\ndiff --git a/a_b.cs b/a_b.cs\n+y\n';

    const target = await capturePullRequestDiff(github(diff), 'u', directory);

    expect(manifest(target)).toEqual(['a/b.cs\ta_b.cs', 'a_b.cs\t2_a_b.cs']);
  });

  test('never lets a changed path take the manifest name', async () => {
    const target = await capturePullRequestDiff(github(`diff --git a/${MANIFEST_NAME} b/${MANIFEST_NAME}\n+x\n`), 'u', directory);

    expect(manifest(target)).toEqual([`${MANIFEST_NAME}\t2_${MANIFEST_NAME}`]);
  });

  test('replaces the diff of an earlier capture', async () => {
    await capturePullRequestDiff(github('diff --git a/old.cs b/old.cs\n+x\n'), 'u', directory);

    const target = await capturePullRequestDiff(github('diff --git a/new.cs b/new.cs\n+y\n'), 'u', directory);

    expect(readdirSync(target).sort()).toEqual([MANIFEST_NAME, 'new.cs']);
  });

  test('is an empty manifest for an empty diff', async () => {
    const target = await capturePullRequestDiff(github(''), 'u', directory);

    expect(readdirSync(target)).toEqual([MANIFEST_NAME]);
    expect(existsSync(join(target, MANIFEST_NAME))).toBe(true);
  });

  test('rejects a header it cannot parse', async () => {
    const error = await capturePullRequestDiff(github('diff --git broken\n'), 'u', directory).then(
      () => null,
      (failure: unknown) => failure,
    );

    expect(error).toBeInstanceOf(TicketWorkflowError);
    expect((error as Error).message).toContain('unparseable diff header');
  });
});
