import type { ActionsRun, Check, CommitStatus, ReviewThread, Snapshot } from '../infrastructure/github.ts';
import type { Json } from '../infrastructure/json.ts';
import type { PrState } from '../infrastructure/state.ts';

import { digest, remoteFingerprint, trusted } from '../infrastructure/github.ts';
import { encodeSortedCompact } from '../infrastructure/json.ts';
import { type FeedbackItem, itemOf, type NewFeedbackItem } from './records.ts';

export { remoteFingerprint as fingerprint };

const FAILED = new Set(['action_required', 'cancelled', 'failure', 'stale', 'startup_failure', 'timed_out']);

export interface CiState {
  readonly failed: boolean;
  readonly green: boolean;
  readonly pending: boolean;
  /** The completed runs of the latest attempts that failed. */
  readonly runs: readonly ActionsRun[];
}

/** Separate pending, failed, and green signals on the current PR head. */
export function ciState(snapshot: Pick<Snapshot, 'checks' | 'runs' | 'statuses'>): CiState {
  const checks = latest(snapshot.checks, (check: Check) => encodeSortedCompact([check.name, check.app?.id ?? null]));
  const statuses = latest(snapshot.statuses, (status: CommitStatus) => status.context);
  const runs = latest(snapshot.runs, (run: ActionsRun) => encodeSortedCompact([run.workflow_id, run.event ?? null]));
  const pending =
    checks.some((check) => check.status !== 'completed') ||
    statuses.some((status) => status.state === 'pending') ||
    runs.some((run) => run.status !== 'completed');
  const failedRuns = runs.filter((run) => run.status === 'completed' && FAILED.has(run.conclusion ?? ''));
  const failed =
    failedRuns.length > 0 ||
    checks.some((check) => FAILED.has(check.conclusion ?? '')) ||
    statuses.some((status) => status.state === 'failure' || status.state === 'error');
  return { failed, green: !pending && !failed, pending, runs: failedRuns };
}

/** Persist newly observed work independently of successful handling. */
export function observe(
  snapshot: Pick<Snapshot, 'other' | 'threads'>,
  state: PrState,
  login: string,
): FeedbackItem[] {
  const pending = pendingThreads(snapshot.threads, login);
  const active = new Set(pending.map((item) => item.key));
  for (const item of snapshot.other) {
    const author = item.author?.login;
    if (!trusted(item, login) || (author === login && !item.body.toLowerCase().includes('fix!:'))) continue;
    pending.push({ ...item, key: `${item.kind}:${item.id}:${digest(item.body)}` } as unknown as NewFeedbackItem);
  }
  for (const [key, value] of Object.entries(state.data.items)) {
    const item = value as FeedbackItem;
    if (item.kind === 'thread' && !active.has(key) && item.status === 'pending') item.status = 'superseded';
  }
  for (const item of pending) {
    if (!Object.hasOwn(state.data.items, item.key)) state.data.items[item.key] = { ...item, status: 'pending' };
  }
  state.save();
  return Object.keys(state.data.items)
    .map((key) => itemOf(state, key))
    .filter((item) => item.status === 'pending');
}

/** Apply the Address gate before exposing comment bodies to an agent. */
export function pendingThreads(threads: readonly ReviewThread[], login: string): NewFeedbackItem[] {
  const pending: NewFeedbackItem[] = [];
  for (const thread of threads) {
    const comments = thread.comments;
    const first = comments[0];
    const latestComment = comments.at(-1);
    if (thread.isResolved || first === undefined || latestComment === undefined || !trusted(first, login)) continue;
    const own = latestComment.author?.login === login;
    const selfNote = comments.length === 1 && own;
    if (own && !selfNote && !latestComment.body.toLowerCase().includes('fix!:')) continue;
    const lastReply = comments.slice(0, -1).findLastIndex((comment) => comment.author?.login === login);
    pending.push({
      comments: comments.slice(lastReply + 1) as unknown as Json,
      key: `thread:${thread.id}:${latestComment.id}`,
      kind: 'thread',
      line: thread.line ?? thread.originalLine ?? null,
      outdated: thread.isOutdated ?? false,
      path: thread.path,
      previous_reply: lastReply >= 0 ? (comments[lastReply] as unknown as Json) : null,
      thread_id: thread.id,
    });
  }
  return pending;
}

/** Select the latest occurrence of each check or workflow. */
function latest<T extends { id: number }>(items: readonly T[], key: (item: T) => string): T[] {
  const result = new Map<string, T>();
  for (const item of [...items].sort((left, right) => right.id - left.id)) {
    if (!result.has(key(item))) result.set(key(item), item);
  }
  return [...result.values()];
}
