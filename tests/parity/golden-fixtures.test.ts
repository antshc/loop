import { describe, expect, test } from 'bun:test';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = join(import.meta.dir, '..', '..');

interface Category {
  /** Fixture paths relative to the repository root. */
  fixtures: string[];
  /** Test files that must read the fixtures. */
  readers: string[];
}

/**
 * The parity gate: fixtures captured from the retired Python implementation and replayed by the
 * test files named here. A category without a non-empty fixture and a reading test fails the suite.
 */
const CATEGORIES: Record<string, Category> = {
  'Copilot and Codex commands and start mode': {
    fixtures: ['tests/fixtures/commands.json', 'tests/fixtures/start-mode.json'],
    readers: ['tests/unit/cli-commands.test.ts'],
  },
  'Copilot and Codex event streams': {
    fixtures: ['tests/fixtures/transports.json'],
    readers: ['tests/unit/transport.test.ts'],
  },
  'Copilot session log': {
    fixtures: [
      'tests/fixtures/copilot-log/expected.json',
      'tests/fixtures/copilot-log/big-numbers.jsonl',
      'tests/fixtures/copilot-log/first-turn-of-resumed-log.jsonl',
      'tests/fixtures/copilot-log/first-turn.jsonl',
      'tests/fixtures/copilot-log/pre-1.0.95-cumulative-totals.jsonl',
      'tests/fixtures/copilot-log/resumed-turn.jsonl',
      'tests/fixtures/copilot-log/sparse-no-start.jsonl',
      'tests/fixtures/copilot-log/timestamp-precision.jsonl',
    ],
    readers: ['tests/unit/copilot-log.test.ts'],
  },
  'dev dry run and phase records': {
    fixtures: [
      'workflows/dev/tests/fixtures/dry-run.json',
      'workflows/dev/tests/fixtures/phase-records.json',
      'workflows/dev/tests/fixtures/prompts.json',
      'workflows/dev/tests/fixtures/review-parity.json',
    ],
    readers: [
      'workflows/dev/tests/dry.test.ts',
      'workflows/dev/tests/dry-delivery.test.ts',
      'workflows/dev/tests/prompts.test.ts',
      'workflows/dev/tests/review-prompt.test.ts',
    ],
  },
  'dev execution log': {
    fixtures: ['workflows/dev/tests/fixtures/execution-log-2026-01-02.json'],
    readers: ['workflows/dev/tests/journal.test.ts'],
  },
  'hook payload': {
    fixtures: ['tests/fixtures/hook-payload.json'],
    readers: ['tests/unit/hook-shim.test.ts'],
  },
  'log records': {
    fixtures: ['tests/fixtures/log-records.json'],
    readers: ['tests/unit/logging.test.ts'],
  },
  'PR state': {
    fixtures: ['workflows/pr_babysit/tests/fixtures/pr-state.json', 'workflows/pr_babysit/tests/fixtures/pr-state-queued.json'],
    readers: ['workflows/pr_babysit/tests/infrastructure/state.test.ts'],
  },
  'scheduler registry': {
    fixtures: ['workflows/pr_babysit/tests/fixtures/scheduler.json', 'workflows/pr_babysit/tests/fixtures/numbers.json'],
    readers: ['workflows/pr_babysit/tests/infrastructure/state.test.ts'],
  },
  'settings': {
    fixtures: ['workflows/pr_babysit/tests/fixtures/settings-cases.json'],
    readers: ['workflows/pr_babysit/tests/settings.test.ts'],
  },
  'span and metric names and attributes': {
    fixtures: ['tests/fixtures/otel-contract.json'],
    readers: ['tests/unit/otel-observer.test.ts'],
  },
};

function fixtureFiles(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules') continue;
      found.push(...fixtureFiles(path));
    } else if (path.split('/').includes('fixtures')) {
      found.push(relative(ROOT, path));
    }
  }
  return found;
}

describe('every golden fixture category is present and replayed', () => {
  for (const [name, category] of Object.entries(CATEGORIES)) {
    test(`${name}: fixtures are non-empty and read by a test`, () => {
      for (const fixture of category.fixtures) {
        const path = join(ROOT, fixture);
        expect(existsSync(path), `${fixture} exists`).toBe(true);
        const text = readFileSync(path, 'utf8');
        expect(text.trim().length, `${fixture} is non-empty`).toBeGreaterThan(0);
        if (fixture.endsWith('.json')) {
          const parsed = JSON.parse(text) as unknown;
          expect(Object.keys(parsed as object).length, `${fixture} has content`).toBeGreaterThan(0);
        }
      }
      for (const reader of category.readers) {
        const source = readFileSync(join(ROOT, reader), 'utf8');
        const names = category.fixtures.map((fixture) => fixture.split('/').pop() ?? fixture);
        const read = names.some((file) => source.includes(file)) || (source.includes('copilot-log') && name === 'Copilot session log');
        expect(read, `${reader} reads a ${name} fixture`).toBe(true);
      }
    });
  }

  test('every fixture file belongs to a category', () => {
    const listed = new Set(Object.values(CATEGORIES).flatMap((category) => category.fixtures));
    const roots = ['tests', 'workflows'].flatMap((directory) => fixtureFiles(join(ROOT, directory)));
    expect(roots.filter((file) => !listed.has(file)).sort()).toEqual([]);
  });
});
