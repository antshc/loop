/** Configured repositories and their agent delegates. */
import { basename, join, resolve } from 'node:path';

import {
  type AgentWorkflow,
  type ChoreWorkflow,
  type PlanWorkflow,
  type Repository,
  RepositoryId,
  type ReviewWorkflow,
  TicketWorkflowError,
  type VerifyWorkflow,
} from './records.ts';

const ROOT = resolve(import.meta.dir, '..', '..');

export const HARNESS: Repository = {
  checkout: ROOT,
  ownerName: new RepositoryId('antshc', 'loop'),
  worktrees: join(ROOT, 'workspace', `${basename(ROOT)}.worktrees`),
};

/** One target and its developer, planner, verifier, cleanup, and reviewer delegates. */
export interface RepositoryWorkflow {
  readonly repository: Repository;
  readonly runAgents: AgentWorkflow;
  readonly runChore?: ChoreWorkflow;
  readonly runPlan?: PlanWorkflow;
  readonly runReview?: ReviewWorkflow;
  readonly runVerify?: VerifyWorkflow;
}

/**
 * Configures all eShop-style phases on the harness by default.
 * Extension point: #140 supplies `runAgents`, `runChore`, `runPlan` and `runVerify` and #141 `runReview`,
 * each bound to its named prompt; until then `runAgents` refuses to run.
 */
export function configuredRepositories(harness: Repository): readonly RepositoryWorkflow[] {
  return [
    {
      repository: harness,
      runAgents: () => Promise.reject(new TicketWorkflowError('Ticket delivery is not ported yet')),
    },
  ];
}
