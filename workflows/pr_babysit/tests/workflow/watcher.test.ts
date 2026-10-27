import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { Check, Snapshot } from '../../infrastructure/github.ts';

import { PrState } from '../../infrastructure/state.ts';
import { ciState, observe, pendingThreads } from '../../workflow/watcher.ts';
import { comment, thread } from '../support/remote.ts';

let root: string;

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-watcher-')));
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

const state = (): PrState => new PrState(root, 'a/b', 1);

describe('the Address gate', () => {
  test('skips fixed and resolved threads but accepts follow-ups and self-notes', () => {
    const fixed = thread([comment(), comment('c2', 'me', 'Fixed!')]);
    expect(pendingThreads([fixed, thread([comment()], true)], 'me')).toEqual([]);

    fixed.comments.push(comment('c3', 'reviewer', 'Also handle empty cart'));
    const pending = pendingThreads([fixed], 'me');
    expect(pending[0]?.comments as unknown).toEqual([comment('c3', 'reviewer', 'Also handle empty cart')]);
    expect((pending[0]?.previous_reply as { body: string }).body).toBe('Fixed!');

    expect(pendingThreads([thread([comment('c1', 'me')])], 'me')).toHaveLength(1);
    expect(pendingThreads([thread([comment(), comment('c2', 'me', 'fix!: follow up')])], 'me')).toHaveLength(1);
  });

  test('never dispatches untrusted feedback', () => {
    const untrusted = { ...comment('c1', 'outsider'), authorAssociation: 'NONE' };

    expect(observe({ other: [], threads: [thread([untrusted])] }, state(), 'me')).toEqual([]);
  });

  test('trusts collaborators, the operator and review bots in PR-level feedback', () => {
    const other = (login: string, association: null | string): Snapshot['other'][number] => ({
      author: { login },
      author_association: association,
      body: `from ${login}`,
      id: login,
      kind: 'comment',
      url: `https://github.com/a/b/pull/1#${login}`,
    });
    const other_ = [other('member', 'MEMBER'), other('copilot[bot]', 'NONE'), other('stranger', 'NONE'), other('me', null)];

    const items = observe({ other: other_, threads: [] }, state(), 'me');

    expect(items.map((item) => (item.body as string).slice(5))).toEqual(['member', 'copilot[bot]']);
  });
});

describe('observed work', () => {
  test('seen is not handled, and a new head does not reopen handled work', () => {
    const snapshot = { other: [], threads: [thread()] };
    const first = observe(snapshot, state(), 'me');
    expect(observe(snapshot, state(), 'me')).toEqual(first);

    const current = state();
    (current.data.items[first[0]?.key ?? ''] as { status: string }).status = 'handled';
    current.save();
    expect(observe(snapshot, current, 'me')).toEqual([]);

    snapshot.threads[0]?.comments.push(comment('c2'));
    expect(observe(snapshot, current, 'me')).toHaveLength(1);
  });

  test('a thread that is no longer pending is superseded', () => {
    const current = state();
    expect(observe({ other: [], threads: [thread()] }, current, 'me')).toHaveLength(1);

    expect(observe({ other: [], threads: [thread([comment()], true)] }, current, 'me')).toEqual([]);
    expect(Object.values(current.data.items).map((item) => (item as { status: string }).status)).toEqual(['superseded']);
  });
});

describe('CI state', () => {
  const old: Check = { app: { id: 1 }, conclusion: 'failure', id: 1, name: 'test', status: 'completed' };

  test('the latest attempt supersedes the previous failure', () => {
    const success: Check = { ...old, conclusion: 'success', id: 2 };
    expect(ciState({ checks: [old, success], runs: [], statuses: [] }).green).toBe(true);

    const running: Check = { ...success, conclusion: null, id: 3, status: 'in_progress' };
    expect(ciState({ checks: [old, success, running], runs: [], statuses: [] }).pending).toBe(true);
  });

  test('failed statuses and runs are reported with the failing runs', () => {
    const run = { conclusion: 'failure', head_sha: 's', id: 9, status: 'completed', workflow_id: 1 };

    const failed = ciState({
      checks: [],
      runs: [run],
      statuses: [{ context: 'ci', id: 1, state: 'error' }],
    });

    expect(failed.failed).toBe(true);
    expect(failed.runs).toEqual([run]);
  });
});
