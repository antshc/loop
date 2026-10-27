import type { Json, JsonObject } from '../infrastructure/json.ts';
import type { PrState } from '../infrastructure/state.ts';

export interface AddressBatch extends Batch {
  issues: Issue[];
  kind: 'address';
  pushed: boolean;
  unclear: boolean;
}

/** The unit of recoverable work on one recovery branch; CI batches carry only these fields. */
export interface Batch extends JsonObject {
  base: string;
  branch: string;
  head: string;
  index: number;
  inflight: Inflight | null;
  kind: 'address' | 'ci';
  replies: Record<string, string>;
}

/** Recorded review feedback and what became of it. */
export interface FeedbackItem extends NewFeedbackItem {
  status: ItemStatus;
}

/** A commit that was about to be created when the worker stopped. */
export interface Inflight extends JsonObject {
  message: string;
  parent: string;
}

/** One underlying concern the Address agent grouped feedback into. */
export interface Issue extends JsonObject {
  id: string;
  keys: string[];
  statement: string;
}

export type ItemStatus = 'handled' | 'pending' | 'superseded';

/** Review feedback as first observed, before it is recorded; threads and PR-level comments share these fields. */
export interface NewFeedbackItem extends JsonObject {
  key: string;
  kind: string;
}

/** The batch in flight, if any; the persisted object is shared, so edits are checkpointed by `state.save()`. */
export function batchOf(state: PrState): Batch | null {
  return state.data.batch as Batch | null;
}

export function itemOf(state: PrState, key: string): FeedbackItem {
  return state.data.items[key] as FeedbackItem;
}

/** Python-style `dict.get` over the free-form state fields. */
export function recordOf(value: Json | undefined): Record<string, Json> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value : {};
}

/** The batch a recovery step requires. */
export function requireBatch(state: PrState): Batch {
  const batch = batchOf(state);
  if (batch === null) throw new Error('No batch is in flight');
  return batch;
}
