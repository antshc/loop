/** The finalize phase: the harness decides readiness from the delivery and the review, then reports on the Spec. */
import type { GitService } from './checkout.ts';
import type { PullRequestClient, TicketsTracker } from './platforms.ts';
import type { Repository, Spec } from './records.ts';
import type { Delivery, ReviewOutcome } from './review.ts';

import { Kind, type WorkflowRun } from '../../src/loop/index.ts';
import { CommandError } from './commands.ts';
import { Owner } from './definition.ts';
import { HITL_LABEL, TicketWorkflowError } from './records.ts';
import { SKIPPED_VERIFICATION } from './review.ts';

/** The collaborators of the finalize phase. */
export interface FinalizeDeps {
  readonly git: GitService;
  readonly phases: WorkflowRun;
  readonly pulls: Pick<PullRequestClient, 'markReady'>;
  readonly tracker: Pick<TicketsTracker, 'updateSpec'>;
}

/** Marks the PR ready only when every gate passes and comments the summary on the Spec; never merges or closes the Spec. */
export function finalize(
  spec: Spec,
  delivery: Delivery,
  outcome: ReviewOutcome,
  repository: Repository,
  deps: FinalizeDeps,
): Promise<boolean> {
  const featureBranch = spec.featureBranch;
  if (featureBranch === null) {
    return Promise.reject(new TicketWorkflowError(`cannot resolve feature branch for Spec ${spec.url}`));
  }
  return deps.phases.phase(
    'finalize',
    { attributes: { spec: spec.number }, kind: Kind.CODE, owner: Owner.CODE },
    async (phase) => {
      await deps.git.fetch(repository.checkout);
      const currentHead = await deps.git.refSha(repository.checkout, `origin/${featureBranch}`);
      const blockers = readinessBlockers(spec, delivery, outcome, currentHead);
      phase.gate('PR head is unchanged since the review', currentHead === delivery.headSha, currentHead);
      let ready = blockers.length === 0;
      if (ready) {
        try {
          await deps.pulls.markReady(delivery.pullRequestUrl);
        } catch (error) {
          if (!(error instanceof CommandError)) {
            throw error;
          }
          ready = false;
          blockers.push(`marking the PR ready failed: ${error.message}`);
          phase.fail(blockers.at(-1) ?? '');
        }
      }
      phase.set({ ready });
      if (!ready) {
        phase.fail('PR remains a draft: readiness checks did not pass');
      }
      spec.comment(summary(delivery, outcome, ready, blockers));
      await deps.tracker.updateSpec(spec);
      return ready;
    },
  );
}

/** Why the PR must stay a draft; empty when every gate passes. */
export function readinessBlockers(spec: Spec, delivery: Delivery, outcome: ReviewOutcome, currentHead: string): string[] {
  const blockers: string[] = [];
  if (!delivery.delivered) {
    blockers.push('not every Ticket was delivered');
  }
  const openTickets = spec.tickets.filter((ticket) => ticket.state === 'open');
  if (openTickets.length > 0) {
    blockers.push(`open Tickets: ${openTickets.map((ticket) => `#${String(ticket.number)}`).join(', ')}`);
  }
  if (spec.heldTickets.length > 0) {
    blockers.push(`Tickets held for a human: ${spec.heldTickets.map((number) => `#${String(number)}`).join(', ')}`);
  }
  if (spec.awaitingHuman || spec.tickets.some((ticket) => ticket.labels.includes(HITL_LABEL))) {
    blockers.push('a Ticket or the Spec is waiting for a human (`hitl`)');
  }
  const verification = delivery.verification ?? SKIPPED_VERIFICATION;
  if (verification.blocksReady) {
    blockers.push(`app verification is ${verification.verdict}`);
  }
  const result = outcome.result ?? null;
  if (result === null) {
    blockers.push(nonEmpty(outcome.error) ?? 'the review did not run');
  } else {
    if (!result.completed) {
      blockers.push('a review axis did not complete');
    }
    if (result.headSha !== delivery.headSha) {
      blockers.push(`the review covered ${nonEmpty(result.headSha) ?? 'an unknown head'}, not ${delivery.headSha}`);
    }
    if (result.blockingFindings.length > 0) {
      blockers.push(`${String(result.blockingFindings.length)} unresolved bug/initiative finding(s)`);
    }
  }
  if (currentHead !== delivery.headSha) {
    blockers.push(`the PR head moved to ${currentHead} after the review started`);
  }
  return blockers;
}

/** The comment left on the Spec: how the delivery, verification, cleanup and review ended and why a draft stays one. */
export function summary(delivery: Delivery, outcome: ReviewOutcome, ready: boolean, blockers: readonly string[]): string {
  const verification = delivery.verification ?? SKIPPED_VERIFICATION;
  const evidence = verification.evidenceDir === null || verification.evidenceDir === undefined ? '' : ` (evidence \`${verification.evidenceDir}\`)`;
  const lines = [
    `dev: pull request ${delivery.pullRequestUrl} is ${ready ? '**ready for review**' : '**left as a draft**'}`,
    '',
    'Commits:',
    ...(delivery.commits ?? []).map((commit) => `- ${commit}`),
    '',
    `App verification: ${verification.verdict}${evidence}`,
  ];
  const chore = delivery.chore ?? null;
  if (chore !== null) {
    const note = nonEmpty(chore.note);
    lines.push(`Chore: ${chore.status}${note === undefined ? '' : ` (${note})`}`);
    lines.push(...(chore.outOfScope ?? []).map((item) => `- out of scope: ${item}`));
  }
  const result = outcome.result ?? null;
  if (result === null) {
    lines.push(`Review: not completed (${nonEmpty(outcome.error) ?? 'it did not run'})`);
  } else {
    lines.push(`Review: ${result.axes.map((axis) => `${axis.name} ${axis.outcome}`).join(', ')}`);
    lines.push(...result.findings.map((finding) => `- ${finding.kind}: ${finding.summary}`));
  }
  if (blockers.length > 0) {
    lines.push('', 'Kept as a draft because:', ...blockers.map((blocker) => `- ${blocker}`));
  }
  return lines.join('\n');
}

function nonEmpty(text: string | undefined): string | undefined {
  return text === undefined || text === '' ? undefined : text;
}
