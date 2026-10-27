/** Settings, dependency wiring, and the dev run. */
import { join, resolve } from 'node:path';

import type { DevOptions } from './args.ts';
import type { GitService } from './checkout.ts';
import type { ExecutionStore } from './journal.ts';
import type { GithubFactory } from './platforms.ts';
import type { RepositoryWorkflow } from './repositories.ts';
import type { TracingObserver } from './telemetry.ts';

import {
  Agent,
  type AgentBuilder,
  configureLogging,
  getLogger,
  type LoopHook,
  otlpObserver,
  type OutputStream,
  type SessionLogReader,
  TelemetryUnavailableError,
  type WorkflowLogger,
} from '../../src/loop/index.ts';
import { type DevDeps, processSpecs } from './backlog.ts';
import { CliGitService } from './checkout.ts';
import { devWorkflow } from './definition.ts';
import { Fakes } from './dry.ts';
import { FileExecutionStore } from './journal.ts';
import { githubFor, TicketsTracker } from './platforms.ts';
import { configuredRepositories, HARNESS } from './repositories.ts';

const logger = getLogger('workflow.dev');

export const LOG_DIR_NAME = '.loop';
export const LOOP_HOOKS: readonly LoopHook[] = [];
export const MAX_TICKET_FAILURES = 2;

/** Each member replaces the shipped implementation of the same name. */
export interface DevDependencies {
  /** Monotonic milliseconds for the visualization's running durations. */
  clock?: () => number;
  /** The directory a relative or default log directory is resolved from. */
  cwd?: () => string;
  /** The environment `--dry` reads the standard OTLP endpoint variable from. */
  env?: Readonly<Record<string, string | undefined>>;
  git?: GitService;
  githubFactory?: GithubFactory;
  loopHooks?: readonly LoopHook[];
  /** A fresh Agent builder per run. */
  newAgent?: () => AgentBuilder;
  /** Builds the observer exporting traces and metrics to the OTLP endpoint; called only when an endpoint is set. */
  newObserver?: (endpoint: string) => Promise<TracingObserver> | TracingObserver;
  /** The session-log readers observing runs; the Copilot reader when absent. */
  readers?: readonly SessionLogReader[];
  repositories?: readonly RepositoryWorkflow[];
  /** Receives the console log, the visualization, and usage for bad input. */
  stderr?: OutputStream;
  /** Receives requested help. */
  stdout?: OutputStream;
  store?: ExecutionStore;
}

/** The console shows high-level Workflow records and Workflow-owned warnings; everything else goes to the log file. */
const HIGH_LEVEL = /^\S+ \S+ (?<level>[A-Z]+) (?<name>[\w.]+): /;
const WARNING_AND_ABOVE: ReadonlySet<string> = new Set(['CRITICAL', 'ERROR', 'FATAL', 'WARNING']);

/** Delivers harness Specs through configured repositories; resolves to the exit code. */
export async function runDev(options: DevOptions, dependencies: DevDependencies, stderr: OutputStream): Promise<number> {
  const fakes = new Fakes(options.fake, { rework: options.rework, speed: options.fakeSpeed });
  const git = dependencies.git ?? fakes.git() ?? new CliGitService();
  const githubFactory = dependencies.githubFactory ?? fakes.githubFactory();
  const newAgent = dependencies.newAgent ?? fakes.newAgent() ?? Agent;
  const readers = dependencies.readers ?? fakes.readers() ?? undefined;

  const cwd = dependencies.cwd?.() ?? process.cwd();
  const logDir = resolve(cwd, options.logDir ?? LOG_DIR_NAME);
  const store = dependencies.store ?? fakes.store() ?? new FileExecutionStore(logDir);
  // The workflow is the application: it owns every logger so its own records land in the same file.
  configureLogging({
    level: options.logLevel,
    logFile: join(logDir, 'dev.log'),
    logger: '',
    stream: { write: (text) => { highLevelOnly(stderr, text); } },
  });

  let observer: null | TracingObserver = null;
  try {
    const github = githubFor(HARNESS, githubFactory);
    const repositories = dependencies.repositories ?? configuredRepositories(HARNESS);
    if (options.otlpEndpoint !== null) {
      observer = await (dependencies.newObserver ?? defaultObserver)(options.otlpEndpoint);
    }
    const watched = observer;
    const agents =
      watched === null ? newAgent : () => newAgent().withObserver(watched, readers === undefined ? {} : { readers });
    const workflow = devWorkflow({
      logger: workflowLogger(),
      observer,
      visualize: options.visualize ? { clock: dependencies.clock, stream: stderr } : false,
    });
    return await workflow.run(async (phases) => {
      const deps: DevDeps = {
        git,
        githubFactory,
        logDir,
        loopHooks: dependencies.loopHooks ?? LOOP_HOOKS,
        maxTicketFailures: MAX_TICKET_FAILURES,
        newAgent: agents,
        phases,
        specNumber: options.spec,
        store,
        ticketRepository: HARNESS.ownerName,
        tracker: new TicketsTracker(HARNESS.ownerName.owner, HARNESS.ownerName.repo, (args) => github.run(args)),
      };
      const code = await processSpecs(repositories, deps);
      if (code !== 0) {
        phases.fail('A Spec failed or needs human input');
      }
      return code;
    });
  } catch (error) {
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
  } finally {
    await observer?.shutdown?.();
  }
}

function defaultObserver(endpoint: string): Promise<TracingObserver> {
  return otlpObserver({ endpoint, resourcePerAgent: true, serviceName: 'dev' });
}

function highLevelOnly(stream: OutputStream, text: string): void {
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
