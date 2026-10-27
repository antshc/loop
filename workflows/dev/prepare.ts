/** Preflight for a routed Spec: fetch, plan when it has no Tickets, and publish what earlier runs left behind. */
import type { Phase } from '../../src/loop/index.ts';
import type { DevDeps } from './backlog.ts';
import type { Spec } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';

import { pythonRepr } from './format.ts';
import { planSpec } from './plan.ts';
import { publishPullRequest } from './publish.ts';
import { TicketWorkflowError } from './records.ts';

/** Resolves to whether the Spec has Tickets to deliver on a worktree; otherwise the Spec is settled or handed to a human. */
export function prepareSpec(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps): Promise<boolean> {
  const { repository } = workflow;
  return deps.phases.phase('preflight', { attributes: { spec: spec.number } }, async (phase) => {
    await deps.git.fetch(repository.checkout);
    const baseBranch = spec.baseBranch;
    if (baseBranch === null) {
      throw new TicketWorkflowError(`cannot resolve base branch for Spec ${spec.url}`);
    }
    if (spec.needsPlan && workflow.runPlan !== undefined) {
      if (!(await deps.git.remoteBranchExists(repository.checkout, baseBranch))) {
        return missingBase(spec, deps, phase, baseBranch);
      }
      const planned = await planSpec(spec, repository.checkout, workflow.runPlan, {
        git: deps.git,
        newAgent: deps.newAgent,
        phases: deps.phases,
        tracker: deps.tracker,
      });
      if (!planned) {
        phase.fail('Planning failed; human input required');
        return false;
      }
    } else if (workflow.runPlan === undefined) {
      await deps.phases.skip('plan', 'No planner configured');
      await deps.phases.skip('create-tickets', 'No planner configured');
    } else {
      await deps.phases.skip('plan', 'Spec already has Tickets');
      await deps.phases.skip('create-tickets', 'Spec already has Tickets');
    }
    if (!spec.hasWork) {
      await publishPullRequest(spec, repository, repository.checkout, deps);
      return false;
    }
    if (!(await deps.git.remoteBranchExists(repository.checkout, baseBranch))) {
      return missingBase(spec, deps, phase, baseBranch);
    }
    // Publish earlier runs' commits from the checkout before the worktree opens.
    await publishPullRequest(spec, repository, repository.checkout, deps);
    return true;
  });
}

async function missingBase(spec: Spec, deps: DevDeps, phase: Phase, baseBranch: string): Promise<false> {
  spec.handToHuman(`dev: target branch ${pythonRepr(baseBranch)} does not exist on ${spec.target ?? 'None'}`);
  await deps.tracker.updateSpec(spec);
  phase.fail('base branch missing');
  return false;
}
