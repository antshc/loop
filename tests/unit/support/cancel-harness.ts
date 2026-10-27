// Child entrypoint for the cancellation tests: runs a process tree through the helper, then simulates cleanup.
import { appendFileSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { runEntrypoint } from '../../../src/loop/run/entrypoint.ts';
import { createProcessRunner } from '../../../src/loop/run/process.ts';
import { ProcessSupervisor } from '../../../src/loop/run/supervisor.ts';

const [kind = 'direct', workdir = '.', cleanup = 'quick'] = process.argv.slice(2);
const log = join(workdir, 'log');
const pids = join(workdir, 'pids');
const mark = (line: string): void => {
  appendFileSync(log, `${line}\n`);
};

const supervisor = new ProcessSupervisor({ graceMs: Number(process.env.LOOP_TEST_GRACE_MS ?? '300') });
const runner = createProcessRunner(supervisor);

const record = `echo $$ >> '${pids}'; sleep 300 & echo $! >> '${pids}'; echo READY >> '${log}'; wait`;
const commands: Record<string, string[]> = {
  direct: ['sh', '-c', `exec sleep 300`],
  grandchild: ['sh', '-c', record],
  stubborn: ['sh', '-c', `trap '' TERM; ${record}`],
};

async function main(): Promise<void> {
  const worktree = join(workdir, 'worktree');
  mkdirSync(worktree, { recursive: true });
  writeFileSync(join(worktree, 'file'), 'x');
  const command = commands[kind] ?? [];
  mark('START');
  try {
    if (kind === 'direct') {
      const handle = runner.start(command);
      writeFileSync(pids, `${String(handle.pid)}\n`);
      mark('READY');
      await handle.wait();
    } else {
      await runner.run(command);
    }
  } finally {
    mark('CLEANUP_START');
    if (cleanup === 'slow') {
      const handle = runner.start(['sleep', '300']);
      appendFileSync(pids, `${String(handle.pid)}\n`);
      mark('CLEANUP_CHILD');
      await handle.wait();
    } else {
      await runner.run(['true'], { check: true });
    }
    rmSync(worktree, { force: true, recursive: true });
    mark('CLEANUP_DONE');
  }
}

await runEntrypoint(main);
