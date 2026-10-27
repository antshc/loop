import type { GitService } from './checkout.ts';
import type { ExecutionStore } from './journal.ts';
import type { GithubFactory, TicketsTracker } from './platforms.ts';
import type { RepositoryId, Spec } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';

/** Route harness Specs, deliver their work, and publish the resulting branches. */
import {
  type AgentBuilder,
  getLogger,
  Kind,
  type LoopHook,
  LoopHookError,
  ProcessFailedError,
  type WorkflowRun,
} from '../../src/loop/index.ts';
import { CommandError } from './commands.ts';
import { Owner } from './definition.ts';
import { TicketWorkflowError as DevError } from './records.ts';

const logger = getLogger('workflow.dev');

/** Shared dependencies for routing and delivering harness Specs. */
export interface DevDeps {
  readonly git: GitService;
  readonly githubFactory: GithubFactory | null;
  /** Where agents' evidence is kept, outside every worktree. */
  readonly logDir: string;
  readonly loopHooks: readonly LoopHook[];
  readonly maxTicketFailures: number;
  readonly newAgent: () => AgentBuilder;
  readonly phases: WorkflowRun;
  readonly specNumber: null | number;
  readonly store: ExecutionStore;
  readonly ticketRepository: RepositoryId;
  readonly tracker: TicketsTracker;
}

export const Outcome = { FAILED: 'failed', SKIPPED: 'skipped', SUCCESS: 'success' } as const;
export type Outcome = (typeof Outcome)[keyof typeof Outcome];

/**
 * Extension point for the Ticket delivery (#140) and review/readiness (#141) phases: prepares the checkout, plans,
 * delivers every Ticket on a worktree, publishes the pull request and finalizes it, for a Spec routed to `workflow`.
 * It runs inside the Spec phase; throwing `CommandError`, `LoopHookError` or `ProcessFailedError` fails the Spec
 * with a comment. This placeholder delivers nothing and skips the Spec.
 */
export function deliverSpec(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps): Promise<Outcome> {
  logger.warning(
    `Spec #${String(spec.number)} of ${deps.ticketRepository.toString()} was not delivered to ${workflow.repository.ownerName.toString()}: Ticket delivery is not ported yet`,
  );
  return Promise.resolve(Outcome.SKIPPED);
}

/** Routes every non-human-blocked Spec (only `deps.specNumber` when set) to its configured repository and resolves to the exit code. */
export async function processSpecs(repositories: readonly RepositoryWorkflow[], deps: DevDeps): Promise<number> {
  const targets = new Map<string, RepositoryWorkflow>();
  for (const workflow of repositories) {
    const target = workflow.repository.ownerName.toString().toLowerCase();
    if (targets.has(target)) {
      throw new DevError(`duplicate repository workflow: ${target}`);
    }
    targets.set(target, workflow);
  }
  let failed = false;
  const specs = (await deps.tracker.specs()).filter((spec) => deps.specNumber === null || deps.specNumber === spec.number);
  if (deps.specNumber !== null && specs.length === 0) {
    logger.error(`no open Spec #${String(deps.specNumber)} on ${deps.ticketRepository.toString()}`);
    return 1;
  }
  for (const spec of specs) {
    if (!spec.awaitingHuman) {
      failed = (await processSpec(spec, targets, deps)) === Outcome.FAILED || failed;
    }
  }
  return failed ? 1 : 0;
}

function describe(error: unknown): string {
  if (error instanceof ProcessFailedError) {
    return `${error.message}\n${error.stderr}`.trim();
  }
  return error instanceof Error ? error.message : String(error);
}

function processSpec(spec: Spec, targets: ReadonlyMap<string, RepositoryWorkflow>, deps: DevDeps): Promise<Outcome> {
  // The engineer's request is the root of the run's trace.
  return deps.phases.phase(
    'spec',
    { attributes: { initiative: spec.initiative, spec: spec.number }, kind: Kind.ENGINEER, owner: Owner.ENGINEER },
    async (phase) => {
      const outcome = await routeSpec(spec, targets, deps);
      if (outcome === Outcome.FAILED) {
        phase.fail('Spec delivery failed');
      } else if (outcome === Outcome.SKIPPED) {
        phase.skip('Spec needs no delivery or is blocked');
      }
      return outcome;
    },
  );
}

async function routeSpec(spec: Spec, targets: ReadonlyMap<string, RepositoryWorkflow>, deps: DevDeps): Promise<Outcome> {
  const target = spec.target;
  if (target === null || spec.baseBranch === null) {
    spec.block(`dev: cannot resolve repo:target/repo:base labels on ${spec.url}`);
    await deps.tracker.updateSpec(spec);
    return Outcome.SKIPPED;
  }
  const workflow = targets.get(target.toLowerCase());
  if (workflow === undefined) {
    spec.block(`dev: repo:target:${target} is not configured for harness ${deps.ticketRepository.toString()}`);
    await deps.tracker.updateSpec(spec);
    return Outcome.SKIPPED;
  }
  try {
    return await deliverSpec(spec, workflow, deps);
  } catch (error) {
    if (!(error instanceof CommandError || error instanceof LoopHookError || error instanceof ProcessFailedError)) {
      throw error;
    }
    logger.error(`Spec #${String(spec.number)} failed (${error.name})`);
    logger.debug(`Spec failure details: ${error.message}`);
    spec.comment(`dev: ${describe(error)}`);
    await deps.tracker.updateSpec(spec);
    return Outcome.FAILED;
  }
}
