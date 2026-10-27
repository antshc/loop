/** Ticket delivery, retries, and validation of the agent's commit. */
import type { AgentWorkspace, Phase, WorkflowRun } from '../../src/loop/index.ts';
import type { GitService } from './checkout.ts';
import type { ExecutionStore } from './journal.ts';
import type { TicketsTracker } from './platforms.ts';
import type { AgentWorkflow, Commit, RepositoryId, Spec, Ticket, TicketContext, TicketResult } from './records.ts';

import { getLogger, ProcessFailedError } from '../../src/loop/index.ts';
import { pythonRepr } from './format.ts';
import { AgentCommitSubject, TicketWorkflowError } from './records.ts';

const logger = getLogger('workflow.tickets');

export interface DeliverTicketsOptions {
  readonly git: GitService;
  /** Failed attempts after which a Ticket is escalated to a human; at least one. */
  readonly maxFailures?: number;
  readonly phases: WorkflowRun;
  /** Called after each Ticket lands, so its commit is pushed before the next one starts. */
  readonly publish?: () => Promise<unknown>;
  readonly store: ExecutionStore;
  readonly ticketRepository: RepositoryId;
  readonly tracker: TicketsTracker;
}

/** Delivers actionable Tickets in order, retrying failed attempts and stopping at the failure cap. */
export async function deliverTickets(
  spec: Spec,
  worktree: AgentWorkspace,
  runAgents: AgentWorkflow,
  options: DeliverTicketsOptions,
): Promise<boolean> {
  const { git, phases, store, tracker } = options;
  const maxFailures = options.maxFailures ?? 2;
  if (maxFailures < 1) {
    throw new TicketWorkflowError('max_failures must be at least one');
  }
  const publish = options.publish ?? (() => Promise.resolve());
  for (const ticket of spec.tickets) {
    if (!ticket.actionable) {
      continue;
    }
    let attempts = 0;
    for (;;) {
      attempts += 1;
      const attempt = attempts;
      const headBefore = await git.head(worktree.path);
      const context = await ticketContext(spec, ticket, worktree.path, git);
      const result = await phases.phase('ticket', { attributes: { attempt, ticket: ticket.number } }, (phase) =>
        attemptTicket(context, worktree, runAgents, git, headBefore, phases, phase),
      );
      if (typeof result !== 'string') {
        await phases.phase('close-ticket', { attributes: { ticket: ticket.number } }, async () => {
          spec.closeTicket(
            ticket.number,
            `Delivered in ${result.commit ?? ''}.\n\n${result.summary ?? ''}\n\n${result.verification ?? ''}`,
          );
          await tracker.updateSpec(spec);
          await store.reset(ticket.url);
        });
        await publish();
        break;
      }
      logger.debug(`ticket ${ticket.url} failed: ${result}`);
      await phases.phase('restore', { attributes: { ticket: ticket.number } }, () =>
        git.restore(worktree.path, headBefore.sha),
      );
      if ((await recordFailure(store, options.ticketRepository, ticket)) >= maxFailures) {
        await phases.phase('escalate', { attributes: { ticket: ticket.number } }, async (escalation) => {
          spec.escalate(ticket.number, result);
          await tracker.updateSpec(spec);
          escalation.set({ verdict: 'human input required' });
        });
        return false;
      }
    }
  }
  return true;
}

/** Runs the agent for one Ticket and validates its result and commit; resolves to the result, or why it was rejected. */
async function attemptTicket(
  context: TicketContext,
  worktree: AgentWorkspace,
  runAgents: AgentWorkflow,
  git: GitService,
  headBefore: Commit,
  phases: WorkflowRun,
  phase: Phase,
): Promise<string | TicketResult> {
  let result: TicketResult;
  try {
    result = await runAgents(context, worktree);
  } catch (error) {
    if (error instanceof ProcessFailedError) {
      phase.fail('Agent process failed');
      return 'agent process did not exit successfully';
    }
    if (error instanceof TicketWorkflowError) {
      phase.fail('Agent response was rejected');
      return error.message;
    }
    throw error;
  }
  const violation = await phases.phase('validate', { attributes: { ticket: context.ticket.number } }, async (validation) => {
    const found =
      resultViolation(context.identifier, result) ??
      (await commitViolation(git, worktree.path, headBefore, context.identifier, result));
    validation.gate('Ticket result and commit are valid', found === null, found ?? '');
    return found;
  });
  if (violation !== null) {
    phase.fail('Ticket result or commit validation failed');
    return violation;
  }
  return result;
}

async function commitViolation(
  git: GitService,
  path: string,
  headBefore: Commit,
  identifier: AgentCommitSubject,
  result: TicketResult,
): Promise<null | string> {
  const head = await git.head(path);
  if (head.sha === headBefore.sha) {
    return 'HEAD did not change: the agent made no commit';
  }
  const newCommits = await git.commitsSince(path, headBefore.sha);
  if (newCommits.length !== 1) {
    return `expected exactly one commit since ${headBefore.sha}, found ${String(newCommits.length)}`;
  }
  const prefix = identifier.toSubject();
  if (!head.subject.startsWith(prefix)) {
    return `HEAD subject ${pythonRepr(head.subject)} does not start with ${pythonRepr(prefix)}`;
  }
  if (!(await git.isClean(path))) {
    return 'the worktree has uncommitted changes';
  }
  if (result.commit !== head.sha) {
    return `result.commit ${pythonRepr(result.commit)} does not equal HEAD ${pythonRepr(head.sha)}`;
  }
  return null;
}

async function recordFailure(store: ExecutionStore, repository: RepositoryId, ticket: Ticket): Promise<number> {
  await store.recordFailure(ticket.url, {
    items: [ticket.number],
    owner: repository.owner,
    repo: repository.repo,
    taskId: String(ticket.number),
    title: ticket.title,
  });
  return store.failedAttempts(ticket.url);
}

function resultViolation(identifier: AgentCommitSubject, result: TicketResult): null | string {
  if (result.identifier !== identifier.toString()) {
    return `response identifier ${pythonRepr(result.identifier)} does not match task ${pythonRepr(identifier.toString())}`;
  }
  if (result.status === 'failed') {
    return typeof result.reason === 'string' ? result.reason : 'failed result is missing reason';
  }
  if (result.status !== 'completed') {
    return `unknown result status: ${pythonRepr(result.status)}`;
  }
  if ([result.commit, result.summary, result.verification].some((value) => typeof value !== 'string')) {
    return 'completed result is missing a required commit, summary, or verification field';
  }
  return null;
}

async function ticketContext(spec: Spec, ticket: Ticket, path: string, git: GitService): Promise<TicketContext> {
  const { baseBranch, featureBranch } = spec;
  if (baseBranch === null || featureBranch === null) {
    throw new TicketWorkflowError(`cannot resolve branches for Spec ${spec.url}`);
  }
  const identifier = new AgentCommitSubject(spec.initiative, ticket.number);
  const commits = await git.initiativeCommits(path, baseBranch, identifier.initiativeCommitPrefix);
  return {
    baseBranch,
    featureBranch,
    identifier,
    initiativeCommits: commits.map((commit) => `${commit.sha.slice(0, 7)} ${commit.subject}`),
    ticket,
  };
}
