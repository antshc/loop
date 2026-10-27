import { randomBytes } from 'node:crypto';
import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  writeSync,
} from 'node:fs';
import { dirname, join } from 'node:path';

import { FileLockContendedError, withFileLock } from '../file-lock.ts';
import { compareCodePoints, encodeJson, type Json, type JsonObject } from './json.ts';

/** A PR needs intervention before its maintenance can continue. */
export class Blocked extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'Blocked';
  }
}

export const PR_STATUSES = ['queued', 'ignored', 'running', 'blocked', 'completed', 'interrupted'] as const;
export type PrStatus = (typeof PR_STATUSES)[number];

const ITEM_STATUSES: readonly string[] = ['pending', 'handled', 'superseded'];

/** Fingerprint of a PR whose worker died without an outcome; only an explicit retry resumes it. */
export const WORKER_CRASH_FINGERPRINT = 'worker-crash';

/** A PR as discovered on GitHub; only the fields enrollment needs. */
export interface DiscoveredPr {
  readonly html_url: string;
  readonly number: number;
}

/** One PR's persisted state. Other keys (`head`, `reason`, `fingerprint`, `execution`, ...) are added by the Workflow. */
export interface PrStateData extends JsonObject {
  batch: JsonObject | null;
  copilot: JsonObject;
  items: JsonObject;
  number: number;
  repo: string;
  retries: JsonObject;
  status: PrStatus;
  version: 1;
}

export interface RegistryData extends JsonObject {
  author: string;
  initialized: boolean;
  prs: Record<string, RegistryRecord>;
  repositories: string[];
  version: 1;
}

export interface RegistryRecord extends JsonObject {
  number: number;
  repo: string;
  url: string;
}

/** Filesystem steps of a durable replace, injectable so an interrupted write can be simulated. */
export interface WriteOperations {
  fsync: (descriptor: number) => void;
  rename: (from: string, to: string) => void;
}

const DEFAULT_OPERATIONS: WriteOperations = { fsync: fsyncSync, rename: renameSync };

const isRecord = (value: unknown): value is JsonObject =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Persist one PR's pending work and publication checkpoints. */
export class PrState {
  readonly data: PrStateData;
  readonly path: string;

  /** The worker died without an outcome, so only an explicit `--retry` for this PR resumes it. */
  get awaitsExplicitRetry(): boolean {
    return this.data.status === 'blocked' && this.data.fingerprint === WORKER_CRASH_FINGERPRINT;
  }

  /** The per-PR process lock path: the state path with a `.lock` suffix. */
  get lockPath(): string {
    return this.path.replace(/\.json$/, '.lock');
  }

  constructor(root: string, repo: string, number: number) {
    this.path = join(root, 'prs', `${repo.replaceAll('/', '--')}--${String(number)}.json`);
    const data = readJson(this.path, {
      batch: null,
      copilot: {},
      items: {},
      number,
      repo,
      retries: {},
      status: 'queued',
      version: 1,
    });
    if (data.repo !== repo || data.number !== number) throw new Blocked(`State identity mismatch: ${this.path}`);
    if (!isValidPrState(data)) throw new Blocked(`Invalid PR state: ${this.path}`);
    this.data = data;
  }

  /** Block a PR whose worker exited without recording an outcome. */
  markWorkerCrash(exitCode: null | number): void {
    this.status('blocked', {
      fingerprint: WORKER_CRASH_FINGERPRINT,
      reason: `Worker exited ${String(exitCode)}; explicit retry required`,
    });
  }

  /** Checkpoint the current transaction. */
  save(): void {
    writeJson(this.path, this.data);
  }

  /** Record execution outcome and recovery information. */
  status(status: PrStatus, values: JsonObject = {}): void {
    Object.assign(this.data, values, { status });
    this.save();
  }
}

/** Remember the discovery baseline and enroll each PR exactly once. */
export class Registry {
  readonly author: string;
  readonly data: RegistryData;
  readonly path: string;
  readonly repositories: string[];
  readonly root: string;

  constructor(root: string, author: string, repositories: readonly string[]) {
    this.root = root;
    this.author = author;
    this.repositories = [...repositories].sort(compareCodePoints);
    this.path = join(root, 'scheduler.json');
    const data = readJson(this.path, {
      author,
      initialized: false,
      prs: {},
      repositories: this.repositories,
      version: 1,
    });
    if (!isRecord(data.prs)) throw new Blocked(`Invalid scheduler registry: ${this.path}`);
    const stored = data.repositories;
    if (
      data.author !== author ||
      !Array.isArray(stored) ||
      stored.length !== this.repositories.length ||
      stored.some((name, index) => name !== this.repositories[index])
    ) {
      throw new Blocked('Discovery configuration changed; choose a new state_dir or migrate its baseline');
    }
    this.data = data as RegistryData;
  }

  /** Commit a complete baseline, then durably enroll future discoveries. */
  enroll(prs: readonly (readonly [string, DiscoveredPr])[], includeExisting: boolean): void {
    for (const [repo, pr] of prs) {
      const key = `${repo}#${String(pr.number)}`;
      if (Object.hasOwn(this.data.prs, key)) continue;
      const status = this.data.initialized || includeExisting ? 'queued' : 'ignored';
      // PR state is created first; replay safely repairs a crash before registry publication.
      const state = new PrState(this.root, repo, pr.number);
      if (!existsSync(state.path)) state.status(status);
      this.data.prs[key] = { number: pr.number, repo, url: pr.html_url };
    }
    this.data.initialized = true;
    writeJson(this.path, this.data);
  }
}

/** Hold an OS lock, released automatically when the process dies; contention is reported as {@link Blocked}. */
export async function processLock<T>(path: string, action: () => Promise<T> | T): Promise<T> {
  try {
    return await withFileLock(path, action);
  } catch (error) {
    if (error instanceof FileLockContendedError) throw new Blocked(error.message, { cause: error });
    throw error;
  }
}

/** Read versioned JSON without treating corrupt state as an empty file. */
export function readJson(path: string, fallback: JsonObject): JsonObject {
  if (!existsSync(path)) return fallback;
  let value: unknown;
  try {
    // Strict decoding mirrors the Python reader: invalid UTF-8 is corrupt state, never replaced text.
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(path)));
  } catch (error) {
    throw new Blocked(`Cannot read state ${path}: ${error instanceof Error ? error.message : String(error)}`, {
      cause: error,
    });
  }
  if (!isRecord(value) || value.version !== 1) throw new Blocked(`Invalid state schema: ${path}`);
  return value;
}

/** The scheduler's single-instance lock for a state directory. */
export function schedulerLockPath(root: string): string {
  return join(root, 'scheduler.lock');
}

/** Durably replace a JSON file without exposing partial writes: temp file, fsync, atomic rename. */
export function writeJson(path: string, value: Json, operations: Partial<WriteOperations> = {}): void {
  const { fsync, rename } = { ...DEFAULT_OPERATIONS, ...operations };
  const directory = dirname(path);
  mkdirSync(directory, { recursive: true });
  const temporary = join(directory, `tmp${randomBytes(6).toString('hex')}`);
  try {
    const descriptor = openSync(temporary, 'wx', 0o600);
    try {
      const bytes = Buffer.from(`${encodeJson(value)}\n`, 'utf8');
      for (let written = 0; written < bytes.length; ) written += writeSync(descriptor, bytes, written);
      fsync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    rename(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function isValidPrState(data: JsonObject): data is PrStateData {
  const { batch, copilot, items, retries, status } = data;
  return (
    typeof status === 'string' &&
    (PR_STATUSES as readonly string[]).includes(status) &&
    isRecord(items) &&
    isRecord(retries) &&
    isRecord(copilot) &&
    (batch === null || batch === undefined || isRecord(batch)) &&
    Object.values(items).every(
      (item) => isRecord(item) && typeof item.status === 'string' && ITEM_STATUSES.includes(item.status),
    )
  );
}
