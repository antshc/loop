import { afterAll, describe, expect, test } from 'bun:test';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import { CONTRACTS, describeViolations, findViolations } from '../../../tests/architecture/contracts.ts';

const REPOSITORY_ROOT = join(import.meta.dir, '..', '..', '..');
const ATTEMPT = join(REPOSITORY_ROOT, 'workflows', 'dev', 'attempt.ts');
const RULES = CONTRACTS.find((contract) => contract.id === 'ticket-orchestration-no-agent-workflow')?.rules ?? [];

const FIXTURES = join(REPOSITORY_ROOT, 'node_modules', '.cache', 'architecture-fixtures');
mkdirSync(FIXTURES, { recursive: true });
const fixtureRoot = mkdtempSync(join(FIXTURES, 'dev-'));
afterAll(() => {
  rmSync(fixtureRoot, { force: true, recursive: true });
});

function fixture(files: Readonly<Record<string, string>>): string {
  const root = mkdtempSync(join(fixtureRoot, 'case-'));
  for (const [file, content] of Object.entries(files)) {
    const path = join(root, file);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
  }
  return root;
}

describe('the Ticket orchestration boundary', () => {
  test('holds for the real attempt module', async () => {
    expect(existsSync(ATTEMPT)).toBe(true);
    expect(RULES).toHaveLength(1);

    expect(describeViolations(await findViolations(REPOSITORY_ROOT, RULES))).toEqual([]);
  });

  test.each(['agent', 'repositories', 'run'])('fails when the real attempt module imports %s', async (module) => {
    const root = fixture({
      [`workflows/dev/${module}.ts`]: 'export {};\n',
      'workflows/dev/attempt.ts': `${readFileSync(ATTEMPT, 'utf8')}\nimport './${module}.ts';\n`,
    });

    expect(describeViolations(await findViolations(root, RULES))).toEqual([
      `ticket-orchestration-no-agent-workflow: workflows/dev/attempt.ts -> workflows/dev/${module}.ts`,
    ]);
  });

  test('fails when the real attempt module reaches an agent module through the platform adapter', async () => {
    const root = fixture({
      'workflows/dev/agent.ts': 'export {};\n',
      'workflows/dev/attempt.ts': readFileSync(ATTEMPT, 'utf8'),
      'workflows/dev/platforms.ts': "import './agent.ts';\nexport {};\n",
    });

    expect(describeViolations(await findViolations(root, RULES))).toEqual([
      'ticket-orchestration-no-agent-workflow: workflows/dev/attempt.ts -> workflows/dev/agent.ts',
    ]);
  });
});
