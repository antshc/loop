import { describe, expect, test } from 'bun:test';
import { join } from 'node:path';

import { describeViolations, findViolations } from './contracts.ts';

const REPOSITORY_ROOT = join(import.meta.dir, '..', '..');

describe('architecture contracts hold for the repository', () => {
  test('no Loop or Workflow module violates a contract', async () => {
    expect(describeViolations(await findViolations(REPOSITORY_ROOT))).toEqual([]);
  });
});
