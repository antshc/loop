/** What the worktree phase produced, and the step that follows it: review and readiness. */
import type { DevDeps } from './backlog.ts';
import type { ChoreReport } from './chore.ts';
import type { Spec } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';
import type { VerifyReport } from './verify.ts';

/** The published pull request at its final head, and how each step of the worktree phase ended. */
export interface Delivery {
  readonly chore: ChoreReport | null;
  readonly commits: readonly string[];
  readonly delivered: boolean;
  readonly headSha: string;
  readonly pullRequestUrl: string;
  readonly verification: VerifyReport;
}

/**
 * Extension point for the review, reply and readiness phases (#141): runs after the worktree is closed (pushed and
 * clean), so a reviewer never sees implementation state, and resolves to whether the Spec's delivery is ready.
 * Without a configured reviewer the pull request stays a draft: both phases are skipped and only a verification
 * verdict that blocks readiness fails the delivery.
 */
export async function reviewTicket(
  _spec: Spec,
  delivery: Delivery,
  _workflow: RepositoryWorkflow,
  deps: DevDeps,
): Promise<boolean> {
  await deps.phases.skip('review', 'No reviewer configured');
  await deps.phases.skip('finalize', 'PR remains a draft without review');
  return !delivery.verification.blocksReady;
}
