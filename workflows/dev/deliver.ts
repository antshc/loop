/** Delivers a prepared Spec's Tickets on a worktree, then verifies, cleans up and publishes the result. */
import { join } from 'node:path';

import type { DevDeps } from './backlog.ts';
import type { ChoreReport } from './chore.ts';
import type { Delivery } from './delivery.ts';
import type { Spec } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';

import { BranchStrategy, GitOptions } from '../../src/loop/index.ts';
import { deliverTickets } from './attempt.ts';
import { runChore } from './chore.ts';
import { githubFor } from './platforms.ts';
import { publishPullRequest } from './publish.ts';
import { TicketWorkflowError } from './records.ts';
import { SKIPPED, verifyApp } from './verify.ts';

export interface WorktreeDelivery {
  /** Every actionable Ticket landed. */
  readonly delivered: boolean;
  /** Present when the Tickets landed and a pull request exists; steps after the worktree phase start from it. */
  readonly delivery: Delivery | null;
}

/** Opens the Spec's worktree, delivers its Tickets in order and, when all landed, announces, verifies and cleans up the pull request. */
export async function deliverOnWorktree(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps): Promise<WorktreeDelivery> {
  const { baseBranch, featureBranch } = spec;
  if (featureBranch === null) {
    throw new TicketWorkflowError(`cannot resolve feature branch for Spec ${spec.url}`);
  }
  const { repository } = workflow;
  const options = new GitOptions({
    loopHooks: deps.loopHooks,
    repositoryPath: repository.checkout,
    rootPath: repository.worktrees,
    strategy: new BranchStrategy(featureBranch, `origin/${baseBranch ?? 'None'}`),
  });
  let delivery: Delivery | null = null;
  let delivered = false;
  const published: string[] = [];
  const publish = async (worktreePath: string): Promise<void> => {
    const url = await publishPullRequest(spec, repository, worktreePath, deps);
    if (url !== null) {
      published.push(url);
    }
  };
  await deps.phases.phase('deliver', { attributes: { spec: spec.number } }, (phase) =>
    deps.newAgent().withGit(options).open(async (worktree) => {
      delivered = await deliverTickets(spec, worktree, workflow.runAgents, {
        git: deps.git,
        maxFailures: deps.maxTicketFailures,
        phases: deps.phases,
        publish: () => publish(worktree.path),
        store: deps.store,
        ticketRepository: deps.ticketRepository,
        tracker: deps.tracker,
      });
      if (!delivered) {
        phase.fail('Ticket delivery exhausted its retries');
      }
      await publish(worktree.path);
      // Tickets are published as they land, so the last publish may have found nothing new to push.
      const pullRequestUrl = published.at(-1);
      if (!delivered || pullRequestUrl === undefined) {
        return;
      }
      spec.announceDelivered(pullRequestUrl);
      await deps.tracker.updateSpec(spec);
      let verification = SKIPPED;
      if (workflow.runVerify === undefined) {
        await deps.phases.skip('verify', 'No verifier configured');
      } else {
        verification = await verifyApp(spec, pullRequestUrl, worktree, workflow.runVerify, {
          evidenceRoot: join(deps.logDir, 'evidence'),
          git: deps.git,
          phases: deps.phases,
          tracker: deps.tracker,
        });
      }
      let chore: ChoreReport | null = null;
      if (workflow.runChore === undefined) {
        await deps.phases.skip('chore', 'No cleanup configured');
      } else {
        chore = await runChore(spec, pullRequestUrl, worktree, workflow.runChore, {
          git: deps.git,
          github: githubFor(repository, deps.githubFactory),
          phases: deps.phases,
        });
      }
      delivery = {
        chore,
        commits: (await deps.git.branchCommits(worktree.path, baseBranch ?? '')).map(
          (commit) => `${commit.sha.slice(0, 7)} ${commit.subject}`,
        ),
        delivered,
        headSha: (await deps.git.head(worktree.path)).sha,
        pullRequestUrl,
        verification,
      };
    }),
  );
  return { delivered, delivery };
}
