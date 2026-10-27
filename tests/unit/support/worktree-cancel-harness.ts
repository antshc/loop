// Child entrypoint for the worktree cancellation tests: a real git worktree scope whose body blocks on a child process.
import { appendFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { BranchStrategy, GitCli, GitOptions, GitRuntime, RunFinishedLoopHook, WorktreeRemovingLoopHook } from '../../../src/loop/index.ts';
import { runEntrypoint } from '../../../src/loop/run/entrypoint.ts';
import { createProcessRunner } from '../../../src/loop/run/process.ts';
import { ProcessSupervisor } from '../../../src/loop/run/supervisor.ts';

const [workdir = '.', repository = '.', mode = 'clean'] = process.argv.slice(2);
const log = join(workdir, 'log');
const mark = (line: string): void => {
  appendFileSync(log, `${line}\n`);
};

const supervisor = new ProcessSupervisor({ graceMs: 300 });
const runner = createProcessRunner(supervisor);
const options = new GitOptions({
  loopHooks: [new WorktreeRemovingLoopHook(`echo REMOVING >> '${log}'`), new RunFinishedLoopHook(`echo FINISHED >> '${log}'`)],
  rootPath: join(workdir, 'trees'),
  strategy: new BranchStrategy('review'),
});

await runEntrypoint(async () => {
  await new GitRuntime(new GitCli(runner)).open(repository, options, async (path) => {
    if (mode === 'dirty') {
      writeFileSync(join(path, 'dirty.txt'), 'unfinished');
    }
    const child = runner.start(['sleep', '300']);
    mark(`READY ${path}`);
    await child.wait();
  });
  mark('COMPLETED');
});
