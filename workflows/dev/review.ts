/** The review phase: a read-only reviewer runs `code-review` on the pushed pull request. */
import type { GitService } from './checkout.ts';

import { type AgentBuilder, Kind, ProcessFailedError, type WorkflowRun } from '../../src/loop/index.ts';
import { Owner } from './definition.ts';
import {
  loopSessionName,
  type Repository,
  type ReviewContext,
  type ReviewResult,
  type ReviewWorkflow,
  type Spec,
  TicketWorkflowError,
} from './records.ts';

/** What the delivery produced: the published pull request at its final head, and how each step ended. */
export interface Delivery {
  readonly chore?: DeliveryChore | null;
  readonly commits?: readonly string[];
  readonly delivered: boolean;
  readonly headSha: string;
  readonly pullRequestUrl: string;
  readonly verification?: DeliveryVerification;
}

/** How the chore step ended: `committed`, `nothing` to change, or `discarded` (with the violation as `note`). */
export interface DeliveryChore {
  readonly note?: string;
  readonly outOfScope?: readonly string[];
  readonly status: string;
}

/** The settled outcome of app verification; `blocksReady` is true for any verdict but `pass` or `skipped`. */
export interface DeliveryVerification {
  readonly blocksReady: boolean;
  readonly evidenceDir?: null | string;
  readonly verdict: string;
}

/** The collaborators of the review phase. */
export interface ReviewDeps {
  readonly git: GitService;
  readonly newAgent: () => AgentBuilder;
  readonly phases: WorkflowRun;
}

/** The review result, or why there is none. */
export interface ReviewOutcome {
  readonly error?: string;
  readonly result?: null | ReviewResult;
}

export const SKIPPED_VERIFICATION: DeliveryVerification = { blocksReady: false, evidenceDir: null, verdict: 'skipped' };

/** The violation when a read-only agent moved HEAD or changed the working tree compared with `statusBefore`. */
export async function readonlyViolation(
  git: GitService,
  path: string,
  headSha: string,
  statusBefore: string,
): Promise<null | string> {
  const head = await git.head(path);
  if (head.sha !== headSha) {
    return `the agent committed (${head.sha}); only the harness commits`;
  }
  const status = await git.status(path);
  return status === statusBefore ? null : 'the read-only agent changed the working tree';
}

/** Runs the reviewer from the checkout once the implementation worktree is gone; a read-only gate guards the checkout. */
export function reviewPullRequest(
  spec: Spec,
  delivery: Delivery,
  repository: Repository,
  runReview: ReviewWorkflow,
  deps: ReviewDeps,
): Promise<ReviewOutcome> {
  const checkout = repository.checkout;
  return deps.phases.phase(
    'review',
    { attributes: { head: delivery.headSha, spec: spec.number }, kind: Kind.AGENT, owner: Owner.REVIEWER },
    async (phase): Promise<ReviewOutcome> => {
      const head = await deps.git.head(checkout);
      const status = await deps.git.status(checkout);
      const context: ReviewContext = {
        headSha: delivery.headSha,
        initiative: spec.initiative,
        pullRequestUrl: delivery.pullRequestUrl,
        sessionName: loopSessionName(spec.number, spec.title),
        sourceRoot: checkout,
      };
      let result: ReviewResult;
      try {
        result = await deps.newAgent().open((workspace) => runReview(context, workspace));
      } catch (error) {
        if (error instanceof ProcessFailedError || error instanceof TicketWorkflowError) {
          phase.fail('Reviewer could not complete');
          return { error: `the reviewer failed: ${error.message}` };
        }
        throw error;
      }
      const violation = await readonlyViolation(deps.git, checkout, head.sha, status);
      if (!phase.gate('reviewer left the checkout unchanged', violation === null, violation ?? '')) {
        return { error: violation ?? '' };
      }
      phase.gate('every axis completed', result.completed, 'an axis did not complete');
      phase.gate('reviewed the current head', result.headSha === delivery.headSha, `reviewed ${pythonRepr(result.headSha)}`);
      phase.set({
        blocking_findings: result.blockingFindings.length,
        findings: result.findings.length,
        posted: result.posted.length,
      });
      return { result };
    },
  );
}

/** The text as Python's `repr` quotes a string, so messages persisted by the Python Workflow keep their form. */
function pythonRepr(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  const escaped = text
    .replaceAll('\\', '\\\\')
    .replaceAll('\n', '\\n')
    .replaceAll('\r', '\\r')
    .replaceAll('\t', '\\t')
    .replaceAll(quote, `\\${quote}`);
  return `${quote}${escaped}${quote}`;
}
