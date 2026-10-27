/** The plan stage: a read-only planner proposes Tickets for a Spec without sub-issues; the harness validates the plan and creates them. */
import type { AgentBuilder, Phase, WorkflowRun } from '../../src/loop/index.ts';
import type { GitService } from './checkout.ts';
import type { TicketsTracker } from './platforms.ts';
import type { PlanResult, PlanWorkflow, Spec } from './records.ts';

import { getLogger, ProcessFailedError } from '../../src/loop/index.ts';
import { pythonRepr } from './format.ts';
import { readonlyViolation } from './gates.ts';
import { loopSessionName, TicketWorkflowError } from './records.ts';

const logger = getLogger('workflow.plan');

export const MAX_PLANNED_TICKETS = 15;

export interface PlanOptions {
  readonly git: GitService;
  readonly newAgent: () => AgentBuilder;
  readonly phases: WorkflowRun;
  readonly tracker: TicketsTracker;
}

/** Plans a Spec with no sub-issues; resolves to whether it now has Tickets, else hands the Spec to a human with the reason. */
export async function planSpec(spec: Spec, checkout: string, runPlan: PlanWorkflow, options: PlanOptions): Promise<boolean> {
  const { git, phases, tracker } = options;
  const baseBranch = spec.baseBranch;
  if (baseBranch === null) {
    throw new TicketWorkflowError(`cannot resolve base branch for Spec ${spec.url}`);
  }
  const planned = await phases.phase('plan', { attributes: { spec: spec.number } }, async (phase) => {
    const head = await git.head(checkout);
    const status = await git.status(checkout);
    let result: PlanResult;
    try {
      result = await options.newAgent().open((workspace) =>
        runPlan(
          { baseBranch, sessionName: loopSessionName(spec.number, spec.title), sourceRoot: checkout, spec },
          workspace,
        ),
      );
    } catch (error) {
      if (error instanceof ProcessFailedError || error instanceof TicketWorkflowError) {
        return reject(spec, tracker, phase, `the planner failed: ${error.message}`);
      }
      throw error;
    }
    let violation = await readonlyViolation(git, checkout, head.sha, status);
    if (!phase.gate('planner left the checkout unchanged', violation === null, violation ?? '')) {
      return reject(spec, tracker, phase, violation ?? '');
    }
    violation = planViolation(result);
    if (!phase.gate('plan is valid', violation === null, violation ?? '')) {
      return reject(spec, tracker, phase, violation ?? '');
    }
    phase.set({ planned: result.tickets?.length ?? 0 });
    return result;
  });
  if (planned === false) {
    return false;
  }
  const created = await phases.phase('create-tickets', { attributes: { spec: spec.number } }, async (phase) => {
    try {
      const tickets = await tracker.createTickets(spec, planned.tickets ?? []);
      phase.set({ tickets: tickets.map((ticket) => String(ticket.number)).join(',') });
      return tickets;
    } catch (error) {
      if (error instanceof TicketWorkflowError) {
        return reject(spec, tracker, phase, error.message);
      }
      throw error;
    }
  });
  if (created === false) {
    return false;
  }
  spec.comment(`dev: planned ${created.map((ticket) => `#${String(ticket.number)}`).join(', ')}`);
  await tracker.updateSpec(spec);
  return true;
}

/** Why a planner's answer cannot become Tickets, or null when it can. */
export function planViolation(result: PlanResult): null | string {
  const tickets = result.tickets ?? [];
  if (result.status === 'failed') {
    return (result.reason ?? '') || 'the planner could not plan the Spec';
  }
  if (result.status !== 'completed') {
    return `unknown plan status: ${pythonRepr(result.status)}`;
  }
  if (tickets.length === 0) {
    return 'the planner returned no Tickets';
  }
  if (tickets.length > MAX_PLANNED_TICKETS) {
    return `the planner returned ${String(tickets.length)} Tickets; the limit is ${String(MAX_PLANNED_TICKETS)}`;
  }
  for (const [index, ticket] of tickets.entries()) {
    if (ticket.title.trim() === '') {
      return `planned Ticket ${String(index)} has no title`;
    }
    const blockedBy = ticket.blockedBy ?? [];
    if (blockedBy.some((blocker) => blocker < 0 || blocker >= index)) {
      return `planned Ticket ${String(index)} is blocked by a Ticket that does not come before it: [${blockedBy.join(', ')}]`;
    }
  }
  return null;
}

async function reject(spec: Spec, tracker: TicketsTracker, phase: Phase, reason: string): Promise<false> {
  logger.warning(`spec #${String(spec.number)} not planned: ${reason}`);
  phase.fail('Planning failed; human input required');
  spec.handToHuman(`dev: cannot plan this Spec: ${reason}`);
  await tracker.updateSpec(spec);
  return false;
}
