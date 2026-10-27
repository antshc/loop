/** Persisted per-Ticket failure history. */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { TicketWorkflowError } from './records.ts';

export type Clock = () => Date;

/** Failure counts per Ticket URL for the current day. */
export interface ExecutionStore {
  /** Today's persisted failure count for a Ticket URL. */
  failedAttempts: (key: string) => Promise<number>;
  /** Increments today's failure count for the given Ticket. */
  recordFailure: (key: string, details: FailureDetails) => Promise<void>;
  /** Clears today's failures for a delivered Ticket. */
  reset: (key: string) => Promise<void>;
}

export interface FailureDetails {
  items: readonly number[];
  owner: string;
  repo: string;
  taskId: string;
  title: string;
}

/** One failure record as persisted: the fields the store reads, plus whatever else the file holds. */
type ExecutionRecord = Readonly<Record<string, unknown>> & { readonly count: number; readonly task: string };

/** The execution log on disk is unreadable. */
export class ExecutionStoreError extends TicketWorkflowError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ExecutionStoreError';
  }
}

/** Daily compact JSON array of failure records at `<directory>/<workflow>-execution-log-<UTC date>.json`. */
export class FileExecutionStore implements ExecutionStore {
  readonly #clock: Clock;
  readonly #directory: string;
  readonly #workflow: string;

  constructor(directory: string, options: { clock?: Clock; workflow?: string } = {}) {
    this.#directory = directory;
    this.#workflow = options.workflow ?? 'dev';
    this.#clock = options.clock ?? (() => new Date());
  }

  async failedAttempts(key: string): Promise<number> {
    return (await this.#load()).find((record) => record.task === key)?.count ?? 0;
  }

  async recordFailure(key: string, details: FailureDetails): Promise<void> {
    const records = await this.#load();
    const index = records.findIndex((record) => record.task === key);
    // Entries, not a literal: the persisted key order is the Python Workflow's and is never sorted.
    const updated = Object.fromEntries([
      ['owner', details.owner],
      ['repo', details.repo],
      ['type', 'spec'],
      ['task_id', details.taskId],
      ['title', details.title],
      ['task', key],
      ['count', (records[index]?.count ?? 0) + 1],
      ['last_run', `${this.#clock().toISOString().slice(0, 19)}Z`],
      ['last_items', [...details.items]],
    ]) as ExecutionRecord;
    if (index < 0) {
      records.push(updated);
    } else {
      records[index] = updated;
    }
    await this.#save(records);
  }

  async reset(key: string): Promise<void> {
    await this.#save((await this.#load()).filter((record) => record.task !== key));
  }

  async #load(): Promise<ExecutionRecord[]> {
    const path = this.#path();
    let text: string;
    try {
      text = await readFile(path, 'utf8');
    } catch (error) {
      if (isMissing(error)) {
        return [];
      }
      throw error;
    }
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch (error) {
      throw new ExecutionStoreError(`execution log is not valid JSON: ${path}`, { cause: error });
    }
    if (!Array.isArray(data)) {
      throw new ExecutionStoreError(`execution log must be a JSON array: ${path}`);
    }
    return data as ExecutionRecord[];
  }

  #path(): string {
    const date = this.#clock().toISOString().slice(0, 10);
    return join(this.#directory, `${this.#workflow}-execution-log-${date}.json`);
  }

  async #save(records: readonly ExecutionRecord[]): Promise<void> {
    await mkdir(this.#directory, { recursive: true });
    await writeFile(this.#path(), compactJson(records));
  }
}

/** Compact JSON without key sorting and with non-ASCII escaped by UTF-16 code unit, the way the Python Workflow wrote it. */
export function compactJson(value: unknown): string {
  return JSON.stringify(value).replaceAll(/[\u007f-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
