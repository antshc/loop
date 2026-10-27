import type { WorkflowGit } from '../infrastructure/git.ts';
import type { GithubService, Snapshot } from '../infrastructure/github.ts';
import type { Verify } from '../infrastructure/verification.ts';
import type { Settings } from '../settings.ts';
import type { Maintenance, Observer } from './maintenance.ts';

import {
  Agent,
  type AgentBuilder,
  Cancelled,
  getLogger,
  otlpObserver,
  ProcessFailedError,
  ProcessTimeoutError,
  ValueError,
  WorktreeError,
} from '../../../src/loop/index.ts';
import { Git } from '../infrastructure/git.ts';
import { Github, remoteFingerprint } from '../infrastructure/github.ts';
import { isOsError } from '../infrastructure/os-error.ts';
import { Blocked, processLock, PrState } from '../infrastructure/state.ts';
import { createVerification } from '../infrastructure/verification.ts';
import { addressReviews, BaselineFailed } from './address.ts';
import { babysitWorkflow } from './definition.ts';
import { maintainCi } from './maintainer.ts';
import { batchOf } from './records.ts';
import { ciState, fingerprint, observe } from './watcher.ts';

const logger = getLogger('pr_babysit.workflow.run');
const COPILOT_REVIEWERS = new Set(['copilot', 'copilot-pull-request-reviewer']);

/** Each member replaces the shipped implementation of the same name. */
export interface RunDependencies {
  /** Throws `Cancelled` when the operator interrupted the worker; checked between steps that spawn no child. */
  checkpoint?: () => void;
  git?: WorkflowGit;
  github?: GithubService;
  /** A fresh Agent builder per workspace. */
  newAgent?: () => AgentBuilder;
  /** Replaces the OTLP observer built from `otel_endpoint`. */
  observer?: null | Observer;
  /** True once monitoring should end; the run then records `interrupted` and returns 130. */
  stop?: () => boolean;
  verification?: Verify;
  /** Pauses between polls. */
  wait?: (seconds: number) => Promise<void>;
}

/** Monitor one PR continuously; quiet polls never create telemetry phases. */
export async function run(
  settings: Settings,
  repo: string,
  number: number,
  dependencies: RunDependencies = {},
): Promise<number> {
  const github = dependencies.github ?? new Github();
  const git = dependencies.git ?? new Git();
  const verify = dependencies.verification ?? createVerification();
  const stop = dependencies.stop ?? (() => false);
  const wait = dependencies.wait ?? ((seconds: number) => Bun.sleep(seconds * 1000));
  const checkpoint = dependencies.checkpoint ?? (() => undefined);
  const newAgent = dependencies.newAgent ?? (() => Agent());
  const repository = settings.repository(repo);
  const state = new PrState(settings.stateDir, repo, number);
  return processLock(state.lockPath, async () => {
    const login = await github.login();
    await git.validate(repository.path, repo);
    const endpoint = settings.otelEndpoint ?? '';
    const created =
      dependencies.observer === undefined || dependencies.observer === null
        ? endpoint === ''
          ? null
          : await otlpObserver({ endpoint, serviceName: 'loop-babysit' })
        : null;
    const observer: null | Observer = dependencies.observer ?? created;
    const execution = crypto.randomUUID().replaceAll('-', '');
    try {
      return await babysitWorkflow({ observer }).run({ execution, pr: number, repository: repo }, async (flow) => {
        const context: Maintenance = { checkpoint, flow, git, github, login, newAgent, observer, repository, settings, state, verify };
        state.status('running', { execution, reason: '' });
        let readyBefore: boolean | null = null;
        let snapshot: Snapshot | undefined;
        try {
          while (!stop()) {
            checkpoint();
            snapshot = await github.snapshot(repo, number);
            const pr = snapshot.pr;
            if (pr.state !== 'open') {
              state.status('completed', { head: pr.head.sha, outcome: pr.merged === true ? 'merged' : 'closed' });
              flow.set({ outcome: state.data.outcome });
              return 0;
            }
            if (
              pr.user.login.toLowerCase() !== (settings.author === '' ? login : settings.author).toLowerCase() ||
              (pr.head.repo?.full_name ?? '').toLowerCase() !== repo.toLowerCase()
            ) {
              throw new Blocked('PR author changed or fork head is not supported by this checkout');
            }
            flow.set({ head: pr.head.sha });
            state.data.head = pr.head.sha;
            const items = observe(snapshot, state, login);
            const batch = batchOf(state);
            if ((batch !== null && batch.kind === 'address') || (batch === null && items.length > 0)) {
              try {
                await addressReviews(context, snapshot, items);
              } catch (error) {
                if (!(error instanceof BaselineFailed) || !ciState(snapshot).failed) throw error;
                await maintainCi(context, snapshot);
                await wait(settings.interval);
              }
              continue;
            }
            if ((batch !== null && batch.kind === 'ci') || ciState(snapshot).failed) {
              await maintainCi(context, snapshot);
              await wait(settings.interval);
              continue;
            }
            const sha = pr.head.sha;
            if (settings.copilotReview && !Object.hasOwn(state.data.copilot, sha)) {
              try {
                await github.requestCopilot(repo, number);
                state.data.copilot[sha] = 'requested';
              } catch (error) {
                if (!(error instanceof ProcessFailedError)) throw error;
                state.data.copilot[sha] = 'unavailable';
              }
              state.save();
              continue;
            }
            const copilotPending = (pr.requested_reviewers ?? []).some((user) =>
              COPILOT_REVIEWERS.has(user.login.toLowerCase().replace(/\[bot\]$/, '')),
            );
            const ready =
              ciState(snapshot).green &&
              !copilotPending &&
              !pr.draft &&
              pr.mergeable === true &&
              pr.mergeable_state === 'clean' &&
              !['CHANGES_REQUESTED', 'REVIEW_REQUIRED'].includes(snapshot.review?.reviewDecision ?? '');
            if (ready !== readyBefore) {
              await flow.phase('ready', { attributes: { head: sha, ready } }, () => undefined);
              readyBefore = ready;
            }
            await wait(settings.interval);
          }
          state.status('interrupted');
          flow.set({ outcome: 'interrupted' });
          return 130;
        } catch (error) {
          if (error instanceof Cancelled) {
            state.status('interrupted');
            throw error;
          }
          if (!isBlockedFailure(error)) throw error;
          const reason = error instanceof Error ? error.message : String(error);
          // Reconcile after our own replies so they cannot immediately retrigger a blocked PR.
          let blockedOn: string;
          try {
            blockedOn = fingerprint(await github.snapshot(repo, number));
          } catch (inner) {
            if (inner instanceof Cancelled) {
              state.status('interrupted');
              throw inner;
            }
            if (!isRecoverableFailure(inner)) throw inner;
            blockedOn = snapshot === undefined ? '' : remoteFingerprint(snapshot);
          }
          state.status('blocked', { fingerprint: blockedOn, reason });
          flow.fail(reason);
          logger.error(`${repo}#${String(number)} blocked: ${reason}`);
          return 1;
        }
      });
    } finally {
      await created?.shutdown();
    }
  });
}

/** Failures reported as a blocked PR rather than a crash. */
function isBlockedFailure(error: unknown): boolean {
  return error instanceof Blocked || error instanceof WorktreeError || isRecoverableFailure(error);
}

/** Process, filesystem and data failures that a later attempt may not repeat. */
function isRecoverableFailure(error: unknown): boolean {
  return (
    error instanceof ProcessFailedError ||
    error instanceof ProcessTimeoutError ||
    error instanceof ValueError ||
    isOsError(error)
  );
}
