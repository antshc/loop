/** The dev run: reports through the Workflow, routes Specs, and settles failures into an exit code. */
import { inject, injectable } from 'inversify';

import type { DevOptions } from './args.ts';
import type { GitService } from './checkout.ts';
import type { ExecutionStore } from './journal.ts';
import type { GithubFactory, TicketsTracker } from './platforms.ts';
import type { Repository } from './records.ts';
import type { RepositoryWorkflow } from './repositories.ts';
import type { TracingObserver } from './telemetry.ts';

import {
  type AgentBuilder,
  getLogger,
  type LoopHook,
  type OutputStream,
  type SessionLogReader,
  TelemetryUnavailableError,
  type WorkflowLogger,
} from '../../src/loop/index.ts';
import { type DevDeps, processSpecs } from './backlog.ts';
import { devWorkflow } from './definition.ts';
import { MAX_TICKET_FAILURES } from './dependencies.ts';
import {
  AGENT_FACTORY,
  CLOCK,
  DEV_OPTIONS,
  EXECUTION_STORE,
  GIT_SERVICE,
  GITHUB_FACTORY,
  HARNESS_REPOSITORY,
  LOG_DIRECTORY,
  LOOP_HOOKS,
  OBSERVER_FACTORY,
  REPOSITORIES,
  SESSION_LOG_READERS,
  STDERR,
  TICKETS_TRACKER,
} from './tokens.ts';

const logger = getLogger('workflow.dev');

/** The console shows high-level Workflow records and Workflow-owned warnings; everything else goes to the log file. */
const HIGH_LEVEL = /^\S+ \S+ (?<level>[A-Z]+) (?<name>[\w.]+): /;
const WARNING_AND_ABOVE: ReadonlySet<string> = new Set(['CRITICAL', 'ERROR', 'FATAL', 'WARNING']);

/** Delivers harness Specs through the configured repositories; every collaborator is injected. */
@injectable()
export class DevRunner {
  readonly #agents: () => AgentBuilder;
  readonly #clock: () => number;
  readonly #git: GitService;
  readonly #githubFactory: GithubFactory | null;
  readonly #harness: Repository;
  readonly #logDir: string;
  readonly #loopHooks: readonly LoopHook[];
  readonly #newObserver: (endpoint: string) => Promise<TracingObserver> | TracingObserver;
  readonly #options: DevOptions;
  readonly #readers: null | readonly SessionLogReader[];
  readonly #repositories: readonly RepositoryWorkflow[];
  readonly #stderr: OutputStream;
  readonly #store: ExecutionStore;
  readonly #tracker: TicketsTracker;

  constructor(
    @inject(DEV_OPTIONS) options: DevOptions,
    @inject(STDERR) stderr: OutputStream,
    @inject(CLOCK) clock: () => number,
    @inject(GIT_SERVICE) git: GitService,
    @inject(GITHUB_FACTORY) githubFactory: GithubFactory | null,
    @inject(AGENT_FACTORY) agents: () => AgentBuilder,
    @inject(SESSION_LOG_READERS) readers: null | readonly SessionLogReader[],
    @inject(EXECUTION_STORE) store: ExecutionStore,
    @inject(REPOSITORIES) repositories: readonly RepositoryWorkflow[],
    @inject(LOOP_HOOKS) loopHooks: readonly LoopHook[],
    @inject(OBSERVER_FACTORY) newObserver: (endpoint: string) => Promise<TracingObserver> | TracingObserver,
    @inject(HARNESS_REPOSITORY) harness: Repository,
    @inject(LOG_DIRECTORY) logDir: string,
    @inject(TICKETS_TRACKER) tracker: TicketsTracker,
  ) {
    this.#options = options;
    this.#stderr = stderr;
    this.#clock = clock;
    this.#git = git;
    this.#githubFactory = githubFactory;
    this.#agents = agents;
    this.#readers = readers;
    this.#store = store;
    this.#repositories = repositories;
    this.#loopHooks = loopHooks;
    this.#newObserver = newObserver;
    this.#harness = harness;
    this.#logDir = logDir;
    this.#tracker = tracker;
  }

  /** Delivers harness Specs through configured repositories; resolves to the exit code. */
  async run(): Promise<number> {
    const options = this.#options;
    let observer: null | TracingObserver = null;
    try {
      if (options.otlpEndpoint !== null) {
        observer = await this.#newObserver(options.otlpEndpoint);
      }
      const watched = observer;
      const readers = this.#readers ?? undefined;
      const agents =
        watched === null
          ? this.#agents
          : () => this.#agents().withObserver(watched, readers === undefined ? {} : { readers });
      const workflow = devWorkflow({
        logger: workflowLogger(),
        observer,
        visualize: options.visualize ? { clock: this.#clock, stream: this.#stderr } : false,
      });
      return await workflow.run(async (phases) => {
        const deps: DevDeps = {
          git: this.#git,
          githubFactory: this.#githubFactory,
          logDir: this.#logDir,
          loopHooks: this.#loopHooks,
          maxTicketFailures: MAX_TICKET_FAILURES,
          newAgent: agents,
          phases,
          specNumber: options.spec,
          store: this.#store,
          ticketRepository: this.#harness.ownerName,
          tracker: this.#tracker,
        };
        const code = await processSpecs(this.#repositories, deps);
        if (code !== 0) {
          phases.fail('A Spec failed or needs human input');
        }
        return code;
      });
    } catch (error) {
      return failureCode(error);
    } finally {
      await observer?.shutdown?.();
    }
  }
}

/** The exit code for an error that ended the run; cancellation propagates to the entry point. */
export function failureCode(error: unknown): number {
  if (error instanceof TelemetryUnavailableError) {
    logger.error(`dev unavailable: ${error.message}`);
    return 1;
  }
  // Cancellation by SIGINT or SIGTERM is not a failure of the run: it propagates to the entry point.
  if (error instanceof Error && error.name === 'Cancelled') {
    throw error;
  }
  // Last-resort boundary: log and fail, never crash bare.
  logger.error(`dev failed (${error instanceof Error ? error.name : typeof error}); enable DEBUG for details`);
  logger.debug(`unexpected error: ${error instanceof Error ? (error.stack ?? error.message) : String(error)}`);
  return 1;
}

/** Writes only the records the console shows to `stream`. */
export function highLevelOnly(stream: OutputStream, text: string): void {
  const groups = HIGH_LEVEL.exec(text)?.groups;
  const level = groups?.level ?? '';
  const name = groups?.name ?? '';
  if ((name === 'loop.workflow' && level !== 'DEBUG') || (name === 'workflow.dev' && WARNING_AND_ABOVE.has(level))) {
    stream.write(text);
  }
}

function workflowLogger(): WorkflowLogger {
  const records = getLogger('loop.workflow');
  return {
    info: (message) => {
      records.info(message);
    },
    warn: (message) => {
      records.warning(message);
    },
  };
}
