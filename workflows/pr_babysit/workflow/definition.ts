import type { Observer } from './maintenance.ts';

import { Kind, type Workflow, WorkflowBuilder } from '../../../src/loop/index.ts';

/** Declare only meaningful PR maintenance phases for reporting. */
export function babysitWorkflow(options: { observer?: null | Observer; visualize?: boolean } = {}): Workflow {
  const builder = new WorkflowBuilder('babysit')
    .phase('prepare', 'Prepare repository', Kind.CODE, 'code')
    .phase('address', 'Address review feedback', Kind.AGENT, 'address')
    .phase('diagnose', 'Diagnose CI failure', Kind.AGENT, 'maintainer')
    .phase('fix-ci', 'Fix CI failure', Kind.AGENT, 'maintainer')
    .phase('verify', 'Verify changes', Kind.CODE, 'code')
    .phase('publish', 'Publish changes and replies', Kind.CODE, 'code')
    .phase('ready', 'PR readiness changed', Kind.CODE, 'code')
    .console();
  if (options.visualize === true) builder.visualize();
  if (options.observer !== undefined && options.observer !== null) builder.otel(options.observer);
  return builder.build();
}
