/** The stage after the worktree closes: review the pushed pull request, then decide readiness. */
import type { GithubFactory, TicketsTracker } from './platforms.ts';
import type { Spec } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';
import type { Delivery, ReviewDeps } from './review.ts';

import { githubFor, PullRequestClient } from './platforms.ts';
import { finalize } from './readiness.ts';
import { reviewPullRequest, SKIPPED_VERIFICATION } from './review.ts';

/** The collaborators of the review stage; `DevDeps` satisfies it. */
export interface ReviewStageDeps extends ReviewDeps {
  readonly githubFactory: GithubFactory | null;
  readonly tracker: TicketsTracker;
}

/**
 * Runs `review` and `finalize` for a delivered Spec; call it only after the implementation worktree is closed,
 * so the reviewer never sees implementation state. Resolves to whether the Spec's delivery succeeded: the pull
 * request was marked ready, or, without a reviewer, it stays a draft that app verification did not reject.
 */
export async function reviewAndFinalize(
  spec: Spec,
  delivery: Delivery,
  workflow: Pick<RepositoryWorkflow, 'repository' | 'runReview'>,
  deps: ReviewStageDeps,
): Promise<boolean> {
  const { repository, runReview } = workflow;
  if (runReview === undefined) {
    await deps.phases.skip('review', 'No reviewer configured');
    await deps.phases.skip('finalize', 'PR remains a draft without review');
    return !(delivery.verification ?? SKIPPED_VERIFICATION).blocksReady;
  }
  const outcome = await reviewPullRequest(spec, delivery, repository, runReview, deps);
  return finalize(spec, delivery, outcome, repository, {
    git: deps.git,
    phases: deps.phases,
    pulls: new PullRequestClient(githubFor(repository, deps.githubFactory)),
    tracker: deps.tracker,
  });
}
