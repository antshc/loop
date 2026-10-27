/** Dev-owned phase declarations; the dev modules own their execution. */
import {
  Kind,
  Role,
  type SpanObserver,
  type VisualizeOptions,
  type Workflow,
  WorkflowBuilder,
  type WorkflowLogger,
} from '../../src/loop/index.ts';

/** Names the actors exported as Aspire resources. */
export const Owner = {
  CHORE: 'chore',
  CODE: Role.CODE,
  DEVELOPER: Role.DEVELOPER,
  ENGINEER: Role.ENGINEER,
  PLANNER: Role.PLANNER,
  REVIEWER: Role.REVIEWER,
  VERIFIER: 'verifier',
} as const;

export interface DevWorkflowOptions {
  /** Receives the high-level phase records; without one they are dropped. */
  logger?: WorkflowLogger;
  /** Shares one span-capable observer with the Agent Runs. */
  observer?: null | SpanObserver;
  /** Shows the live phase table; an options object chooses its stream and clock. */
  visualize?: boolean | VisualizeOptions;
}

/** Builds high-level dev reporting with optional terminal and OTLP views. */
export function devWorkflow(options: DevWorkflowOptions = {}): Workflow {
  const builder = new WorkflowBuilder('dev')
    .phase('spec', 'Spec', Kind.ENGINEER, Owner.ENGINEER)
    .phase('preflight', 'Prepare repository', Kind.CODE, Owner.CODE)
    .phase('plan', 'Plan tickets', Kind.AGENT, Owner.PLANNER)
    .phase('create-tickets', 'Create tickets', Kind.CODE, Owner.CODE)
    .phase('deliver', 'Deliver tickets', Kind.CODE, Owner.CODE)
    .phase('ticket', 'Implement ticket', Kind.AGENT, Owner.DEVELOPER)
    .phase('validate', 'Validate ticket commit', Kind.CODE, Owner.CODE, { visible: false })
    .phase('close-ticket', 'Close ticket', Kind.CODE, Owner.CODE, { visible: false })
    .phase('restore', 'Restore rejected changes', Kind.CODE, Owner.CODE, { visible: false })
    .phase('escalate', 'Request human input', Kind.CODE, Owner.CODE)
    .phase('publish', 'Publish draft PR', Kind.CODE, Owner.CODE)
    .phase('verify', 'Verify application', Kind.AGENT, Owner.VERIFIER)
    .phase('commit-fixes', 'Commit verification fixes', Kind.CODE, Owner.CODE, { visible: false })
    .phase('chore', 'Refine changes', Kind.AGENT, Owner.CHORE)
    .phase('commit-chore', 'Commit cleanup', Kind.CODE, Owner.CODE, { visible: false })
    .phase('review', 'Review pull request', Kind.AGENT, Owner.REVIEWER)
    .phase('finalize', 'Check readiness', Kind.CODE, Owner.CODE)
    .console(options.logger);
  const visualize = options.visualize ?? false;
  if (visualize !== false) {
    builder.visualize(visualize === true ? {} : visualize);
  }
  if (options.observer !== undefined && options.observer !== null) {
    builder.otel(options.observer);
  }
  return builder.build();
}
