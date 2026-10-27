// Child entrypoint for the phase cancellation test: blocks on a child inside a phase and records how the phase ends.
import { appendFileSync } from 'node:fs';
import { join } from 'node:path';

import { Kind, Role, WorkflowBuilder } from '../../../src/loop/index.ts';
import { runEntrypoint } from '../../../src/loop/run/entrypoint.ts';
import { processRunner } from '../../../src/loop/run/process.ts';

const [workdir = '.'] = process.argv.slice(2);
const mark = (line: string): void => {
  appendFileSync(join(workdir, 'log'), `${line}\n`);
};

const workflow = new WorkflowBuilder('Cancelled run')
  .phase('work', 'Work', Kind.CODE, Role.CODE)
  .visualize({ stream: { isTTY: false, write: (text) => { appendFileSync(join(workdir, 'table'), text); } } })
  .build();

await runEntrypoint(async () => {
  await workflow.run(async (run) => {
    try {
      await run.phase('work', async () => {
        const child = processRunner.start(['sleep', '300']);
        mark('READY');
        await child.wait();
      });
    } finally {
      mark(`STATUS ${run.executions.filter((phase) => phase.name === 'work').map((phase) => phase.status).join(',')}`);
    }
  });
});
