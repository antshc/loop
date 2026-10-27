import { constants } from 'node:os';
import { join } from 'node:path';

import type { Repository, Settings } from '../settings.ts';
import type { GithubService, PullRequest } from './github.ts';

import {
  Cancelled,
  getLogger,
  ProcessFailedError,
  type ProcessHandle,
  type ProcessOptions,
  processRunner,
  ProcessTimeoutError,
} from '../../../src/loop/index.ts';
import { Github, remoteFingerprint } from './github.ts';
import { isOsError } from './os-error.ts';
import { Blocked, processLock, PrState, Registry, schedulerLockPath } from './state.ts';

const logger = getLogger('pr_babysit.infrastructure.scheduler');
const TRACE_CONTEXT_VARIABLES: ReadonlySet<string> = new Set(['baggage', 'traceparent', 'tracestate']);
const STDERR_TAIL_CHARS = 2000;

/** The worker entry point, a sibling of this module's directory. */
export const WORKER_ENTRY = join(import.meta.dir, '..', 'main.ts');

/** Each member replaces the shipped implementation of the same name. */
export interface ServeDependencies {
  github?: GithubService;
  /** Starts one worker child in its own process group. */
  spawn?: (argv: readonly string[], options: ProcessOptions) => ProcessHandle;
  /** True once discovery should end; remaining children are then stopped. */
  stop?: () => boolean;
  /** Pauses between discovery scans. */
  wait?: (seconds: number) => Promise<void>;
}

export interface ServeOptions {
  includeExisting?: boolean;
  /** Retry this enrolled blocked PR URL once during discovery. */
  retry?: string;
}

/** Launch an independent babysit execution, with the current runtime executable and no scheduler trace parent. */
export function childCommand(config: string, url: string, entry: string = WORKER_ENTRY): string[] {
  return [process.execPath, entry, '--config', config, '--pr', url];
}

/** Environment overrides for a worker: keep exporter configuration but discard inherited distributed trace context. */
export function childEnvironment(
  environment: Readonly<Record<string, string | undefined>> = process.env,
): Record<string, undefined> {
  return Object.fromEntries(
    Object.keys(environment)
      .filter((name) => TRACE_CONTEXT_VARIABLES.has(name.toLowerCase()))
      .map((name) => [name, undefined]),
  );
}

/** Return a complete discovery scan before advancing the startup baseline. */
export async function discover(
  github: GithubService,
  repositories: readonly Repository[],
  author: string,
): Promise<[string, PullRequest][]> {
  const found: [string, PullRequest][] = [];
  for (const repository of repositories) {
    for (const pr of await github.openPrs(repository.name, author)) found.push([repository.name, pr]);
  }
  return found;
}

/** Scan continuously while child processes own independent babysit executions. */
export async function serve(
  settings: Settings,
  config: string,
  options: ServeOptions = {},
  dependencies: ServeDependencies = {},
): Promise<number> {
  const github = dependencies.github ?? new Github();
  const spawn = dependencies.spawn ?? ((argv, spawnOptions) => processRunner.start(argv, spawnOptions));
  const stop = dependencies.stop ?? (() => false);
  const wait = dependencies.wait ?? ((seconds: number) => Bun.sleep(seconds * 1000));
  const author = settings.author === '' ? await github.login() : settings.author;
  const children = new Map<string, ProcessHandle>();
  let retry = options.retry;
  return processLock(schedulerLockPath(settings.stateDir), async () => {
    const registry = new Registry(settings.stateDir, author, settings.repositories.map((repository) => repository.name));
    try {
      while (!stop()) {
        for (const [key, child] of [...children]) {
          if (child.poll() === null) continue;
          children.delete(key);
          const record = registry.data.prs[key];
          if (record === undefined) continue;
          const state = new PrState(settings.stateDir, record.repo, record.number);
          const { code, stderr } = await settle(child);
          if (code === null || (state.data.status !== 'running' && state.data.status !== 'queued')) continue;
          logger.warning(`Worker for ${key} exited ${String(code)}${stderr === '' ? '' : `: ${stderr}`}`);
          state.markWorkerCrash(code);
        }
        try {
          const prs = await discover(github, settings.repositories, author);
          registry.enroll(prs, options.includeExisting === true);
          const openKeys = new Set(prs.map(([repo, pr]) => `${repo}#${String(pr.number)}`));
          for (const [key, record] of Object.entries(registry.data.prs)) {
            if (children.has(key)) continue;
            const state = new PrState(settings.stateDir, record.repo, record.number);
            const status = state.data.status;
            if (status === 'ignored') continue;
            if (!openKeys.has(key)) {
              if (status !== 'completed') {
                const current = await github.snapshot(record.repo, record.number);
                if (current.pr.state !== 'open') state.status('completed');
              }
              continue;
            }
            const explicitRetry = retry === record.url;
            if (status === 'blocked' && !explicitRetry) {
              const snapshot = await github.snapshot(record.repo, record.number);
              if (state.awaitsExplicitRetry || remoteFingerprint(snapshot) === state.data.fingerprint) continue;
            }
            if (children.size >= settings.concurrency) continue;
            // A surviving child from an interrupted scheduler still owns this PR lock.
            try {
              await processLock(state.lockPath, () => {
                state.status('queued');
              });
            } catch (error) {
              if (error instanceof Blocked) continue;
              throw error;
            }
            logger.info(`Starting babysit for ${key}`);
            children.set(key, spawn(childCommand(config, record.url), { env: childEnvironment() }));
            if (explicitRetry) retry = undefined;
          }
        } catch (error) {
          if (error instanceof Cancelled) break;
          if (!(error instanceof ProcessFailedError || error instanceof ProcessTimeoutError || isOsError(error))) throw error;
          logger.warning(`PR discovery unavailable: ${error.message}`);
        }
        await wait(settings.interval);
      }
    } finally {
      await stopChildren(children);
    }
    return 0;
  });
}

/** The child's exit code as Python reported it (negative for a signal), or null when this scheduler cancelled it. */
async function settle(child: ProcessHandle): Promise<{ code: null | number; stderr: string }> {
  try {
    const result = await child.wait();
    const signals: Readonly<Record<string, number | undefined>> = constants.signals;
    const number = result.signal === null ? undefined : signals[result.signal];
    return { code: number === undefined ? result.exitCode : -number, stderr: result.stderr.trim().slice(-STDERR_TAIL_CHARS) };
  } catch (error) {
    if (error instanceof Cancelled) return { code: null, stderr: '' };
    throw error;
  }
}

/** Ask every running child to end (SIGTERM, then SIGKILL after the supervisor's wait) and wait for all of them. */
async function stopChildren(children: ReadonlyMap<string, ProcessHandle>): Promise<void> {
  for (const child of children.values()) {
    if (child.poll() === null) child.terminate();
  }
  await Promise.allSettled([...children.values()].map((child) => child.wait()));
}
