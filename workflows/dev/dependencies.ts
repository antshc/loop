/** What a caller may replace in the dev Workflow, and its fixed settings. */
import type { AgentBuilder, LoopHook, OutputStream, SessionLogReader } from '../../src/loop/index.ts';
import type { GitService } from './checkout.ts';
import type { ExecutionStore } from './journal.ts';
import type { GithubFactory } from './platforms.ts';
import type { RepositoryWorkflow } from './repositories.ts';
import type { TracingObserver } from './telemetry.ts';

export const LOG_DIR_NAME = '.loop';
export const LOOP_HOOKS: readonly LoopHook[] = [];
export const MAX_TICKET_FAILURES = 2;

/** Each member rebinds the token of the shipped implementation of the same name. */
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
