import { afterEach, beforeEach, expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const harness = join(import.meta.dir, 'support', 'phase-cancel-harness.ts');

let directory = '';

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-phase-cancel-'));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

for (const [signal, code] of [
  ['SIGINT', 130],
  ['SIGTERM', 143],
] as const) {
  test(`${signal} during a phase ends the active phase and the run cancelled, and the table says so`, async () => {
    const child = Bun.spawn(['bun', harness, directory], { stderr: 'inherit', stdout: 'inherit' });
    await waitFor(() => read('log').includes('READY'));

    child.kill(signal);

    expect(await child.exited).toBe(code);
    expect(read('log')).toContain('STATUS cancelled');
    expect(read('table')).toMatch(/Cancelled run\s+│ Cancelled run\s+│ code\s+│ cancelled/);
    expect(read('table')).toMatch(/Work\s+│ code\s+│ cancelled/);
  });
}

function read(name: string): string {
  const path = join(directory, name);
  return existsSync(path) ? readFileSync(path, 'utf8') : '';
}

async function waitFor(condition: () => boolean, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) {
      throw new Error('timed out waiting for the condition');
    }
    await Bun.sleep(20);
  }
}
