import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import { commitSubject, findVerificationSkills, madeNoCommit, protectedChanges, readonlyViolation } from '../gates.ts';
import { FakeWorkflowGit, testDirectory } from './support/harness.ts';

const PATH = '/w';

let directory: string;

beforeEach(() => {
  directory = testDirectory('dev-gates-');
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

function skill(root: string, folder: string, name: string, frontmatter: string): void {
  const path = join(root, folder, name, 'SKILL.md');
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `---\nname: ${name}\n${frontmatter}\n---\n# body\n`);
}

describe('the no-commit gate', () => {
  test('an agent that moved HEAD violates it', async () => {
    const git = new FakeWorkflowGit();
    const head = (await git.head()).sha;

    expect(await madeNoCommit(git, PATH, head)).toBeNull();
    git.agentCommit('agent');
    expect(await madeNoCommit(git, PATH, head)).toContain('only the harness commits');
  });
});

describe('the read-only gate', () => {
  test('a read-only agent must leave HEAD and the working tree as they were', async () => {
    const git = new FakeWorkflowGit();
    const head = (await git.head()).sha;
    const before = await git.status();

    expect(await readonlyViolation(git, PATH, head, before)).toBeNull();
    git.edit('src/a.py');
    expect(await readonlyViolation(git, PATH, head, before)).toContain('changed the working tree');
  });

  test('a read-only agent may start on a checkout the user already changed', async () => {
    const git = new FakeWorkflowGit();
    git.edit('notes.md');
    const head = (await git.head()).sha;
    const before = await git.status();

    expect(await readonlyViolation(git, PATH, head, before)).toBeNull();
  });

  test('a read-only agent that committed violates it before the working tree is compared', async () => {
    const git = new FakeWorkflowGit();
    const head = (await git.head()).sha;
    const before = await git.status();
    git.agentCommit('agent');

    expect(await readonlyViolation(git, PATH, head, before)).toContain('only the harness commits');
  });
});

describe('protected changes', () => {
  test.each([
    [['src/WebApp/Page.razor'], []],
    [['proof/run.ts', 'src/a.cs'], ['proof/run.ts']],
    [
      ['e2e/CartTest.spec.ts', '.github/skills/verify-mole/SKILL.md'],
      ['e2e/CartTest.spec.ts', '.github/skills/verify-mole/SKILL.md'],
    ],
    [['playwright.config.ts'], ['playwright.config.ts']],
    [['src/proof/notes.md', 'tests/e2e/x.cs'], []],
  ])('lists the files inside the proof framework of %j', (files, expected) => {
    expect(protectedChanges(files)).toEqual(expected);
  });
});

describe('commit subjects', () => {
  test.each([
    ['Add form', 'ccode(X|1): Add form'],
    ['ccode(X|1): Add form', 'ccode(X|1): Add form'],
    ['Add form\n\nlong body', 'ccode(X|1): Add form'],
    ['', 'ccode(X|1): fallback'],
    ['x'.repeat(200), `ccode(X|1): ${'x'.repeat(88)}`],
  ])('prefixes the first line of %j', (message, expected) => {
    expect(commitSubject('ccode(X|1): ', message, 'fallback')).toBe(expected);
  });

  test('caps by characters, not UTF-16 units', () => {
    const subject = commitSubject('p: ', '😀'.repeat(200), 'fallback');

    expect(Array.from(subject)).toHaveLength(100);
  });
});

describe('verification skills', () => {
  test('are the project skills that drive the app to prove behavior', () => {
    skill(directory, '.github/skills', 'verify-mole', 'description: Drive and prove the storefront in Chrome.');
    skill(directory, '.github/skills', 'docs', 'description: Writes documentation.');
    skill(
      directory,
      '.github/skills',
      'create-verification',
      'description: "Generates a skill that drives your app to prove behavior"\ndisable-model-invocation: true',
    );
    skill(directory, '.claude/skills', 'other', 'description: Drives the CLI and shows proof of results.');

    const found = findVerificationSkills(directory);

    expect(found.map((path) => dirname(path).split('/').at(-1))).toEqual(['verify-mole', 'other']);
  });

  test('are absent without a skills folder', () => {
    expect(findVerificationSkills(directory)).toEqual([]);
  });
});
