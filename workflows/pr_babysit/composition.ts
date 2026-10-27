import 'reflect-metadata';
import { Container } from 'inversify';

import {
  Agent,
  type AgentBuilder,
  createProcessRunner,
  processRunner,
  type ProcessRunner,
  ProcessSupervisor,
} from '../../src/loop/index.ts';
import { type DiscoveryService, PrDiscovery, type ServeOptions } from './discovery.ts';
import { assertFileLockAvailable } from './file-lock.ts';
import { Git, type WorkflowGit } from './infrastructure/git.ts';
import { Github, type GithubService } from './infrastructure/github.ts';
import { StopRequest } from './infrastructure/signals.ts';
import { createVerification, type Verify } from './infrastructure/verification.ts';
import { type Settings } from './settings.ts';
import { TOKENS } from './tokens.ts';
import { PrWorker, refusingAfter, type WorkerService } from './worker.ts';

/** How long a scheduler child gets after SIGTERM before it is killed. */
export const SCHEDULER_CHILD_WAIT_MS = 10_000;

/**
 * Everything `main` touches outside its own parsing. Each member rebinds the token of the same seam;
 * tests and the entry point supply doubles or leave the shipped implementation in place.
 */
export interface MainDeps {
  /** Replaces the Agent builder created per workspace. */
  agentFactory?: () => AgentBuilder;
  /** Operator-facing diagnostics, one message per call. */
  error?: (message: string) => void;
  /** Replaces the check that no other Workflow run holds the file lock. */
  fileLockGuard?: () => void;
  git?: WorkflowGit;
  github?: GithubService;
  /** Replaces the runner behind GitHub, Git, verification and the scheduler's worker children. */
  processRunner?: ProcessRunner;
  /** Maintain one explicit PR; resolves to the process exit code. */
  run?: (settings: Settings, repo: string, number: number) => Promise<number>;
  /** Discover PRs continuously; resolves to the process exit code. */
  serve?: (settings: Settings, config: string, options: ServeOptions) => Promise<number>;
  stderr?: (text: string) => void;
  stdout?: (text: string) => void;
  /** Replaces the signal-driven stop request and its interruptible clock. */
  stop?: StopRequest;
  verification?: Verify;
}

/**
 * The only place that binds implementations to tokens. Shared services are singletons; the stateless
 * adapters over the process runner are transient so each one gets the runner of its own composition.
 */
export function createContainer(overrides: MainDeps = {}): Container {
  const container = new Container({ defaultScope: 'Singleton' });
  bindDefaults(container);
  applyOverrides(container, overrides);
  return container;
}

function applyOverrides(container: Container, overrides: MainDeps): void {
  const { run, serve } = overrides;
  rebindTo(container, TOKENS.AgentFactory, overrides.agentFactory);
  rebindTo(container, TOKENS.ErrorReporter, overrides.error);
  rebindTo(container, TOKENS.FileLockGuard, overrides.fileLockGuard);
  rebindTo(container, TOKENS.WorkflowGit, overrides.git);
  rebindTo(container, TOKENS.GithubService, overrides.github);
  rebindTo(container, TOKENS.ProcessRunner, overrides.processRunner);
  rebindTo(container, TOKENS.StderrWriter, overrides.stderr);
  rebindTo(container, TOKENS.StdoutWriter, overrides.stdout);
  rebindTo(container, TOKENS.StopRequest, overrides.stop);
  rebindTo(container, TOKENS.Verification, overrides.verification);
  if (run !== undefined) rebindTo(container, TOKENS.WorkerService, { run });
  if (serve !== undefined) rebindTo(container, TOKENS.DiscoveryService, { serve });
}

function bindDefaults(container: Container): void {
  container.bind<(message: string) => void>(TOKENS.ErrorReporter).toConstantValue((message) => {
    process.stderr.write(`PR maintenance stopped: ${message}\n`);
  });
  container.bind<(text: string) => void>(TOKENS.StderrWriter).toConstantValue((text) => process.stderr.write(text));
  container.bind<(text: string) => void>(TOKENS.StdoutWriter).toConstantValue((text) => process.stdout.write(text));
  container.bind<() => void>(TOKENS.FileLockGuard).toConstantValue(assertFileLockAvailable);
  container.bind<() => AgentBuilder>(TOKENS.AgentFactory).toConstantValue(() => Agent());
  container.bind<StopRequest>(TOKENS.StopRequest).toDynamicValue(() => new StopRequest());

  // A worker refuses to start children once stopped; the scheduler's children get a grace period before SIGKILL.
  container
    .bind<ProcessRunner>(TOKENS.ProcessRunner)
    .toDynamicValue((context) => refusingAfter(context.get<StopRequest>(TOKENS.StopRequest), processRunner))
    .whenNoAncestorIs(TOKENS.DiscoveryService);
  container
    .bind<ProcessRunner>(TOKENS.ProcessRunner)
    .toDynamicValue(() => createProcessRunner(new ProcessSupervisor({ graceMs: SCHEDULER_CHILD_WAIT_MS })))
    .whenAnyAncestorIs(TOKENS.DiscoveryService);

  container.bind<GithubService>(TOKENS.GithubService).to(Github).inTransientScope();
  container.bind<WorkflowGit>(TOKENS.WorkflowGit).to(Git).inTransientScope();
  container
    .bind<Verify>(TOKENS.Verification)
    .toResolvedValue((runner: ProcessRunner) => createVerification(runner), [TOKENS.ProcessRunner])
    .inTransientScope();
  container.bind<WorkerService>(TOKENS.WorkerService).to(PrWorker);
  container.bind<DiscoveryService>(TOKENS.DiscoveryService).to(PrDiscovery);
}

function rebindTo(container: Container, token: symbol, value: unknown): void {
  if (value !== undefined) container.rebind(token).toConstantValue(value);
}
