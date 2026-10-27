/** Configured repositories and their agent delegates. */
import { basename, join, resolve } from 'node:path';

import { choreChangeSet, implementTicket, loadPrompt, planTickets, verifyIntegratedApp } from './agent.ts';
import {
  type AgentWorkflow,
  type ChoreWorkflow,
  type PlanWorkflow,
  type Repository,
  RepositoryId,
  type ReviewWorkflow,
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
 * Extension point: #141 supplies `runReview`, bound to its named prompt, alongside the delegates below.
 */
export function configuredRepositories(harness: Repository): readonly RepositoryWorkflow[] {
  const implement = loadPrompt('implement');
  const chore = loadPrompt('chore');
  const plan = loadPrompt('plan');
  const verify = loadPrompt('verify');
  return [
    {
      repository: harness,
      runAgents: (context, workspace) => implementTicket(context, workspace, implement),
      runChore: (context, workspace) => choreChangeSet(context, workspace, chore),
      runPlan: (context, workspace) => planTickets(context, workspace, plan),
      runVerify: (context, workspace) => verifyIntegratedApp(context, workspace, verify),
    },
  ];
}
