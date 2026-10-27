/** What the worktree phase produced, and the step that follows it: review and readiness. */
import type { DevDeps } from './backlog.ts';
import type { ChoreReport } from './chore.ts';
import type { Spec } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';
import type { VerifyReport } from './verify.ts';

import { reviewAndFinalize } from './review-stage.ts';

/** The published pull request at its final head, and how each step of the worktree phase ended. */
export interface Delivery {
  readonly chore: ChoreReport | null;
  readonly commits: readonly string[];
  readonly delivered: boolean;
  readonly headSha: string;
  readonly pullRequestUrl: string;
  readonly verification: VerifyReport;
}

/** Runs after the worktree is closed (pushed and clean), so a reviewer never sees implementation state. */
export async function reviewTicket(
  spec: Spec,
  delivery: Delivery,
  workflow: RepositoryWorkflow,
  deps: DevDeps,
): Promise<boolean> {
  return reviewAndFinalize(spec, delivery, workflow, deps);
}
