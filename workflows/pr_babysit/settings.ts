import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';

import type { Json, JsonObject } from './infrastructure/json.ts';

import { ValueError } from '../../src/loop/index.ts';

const REPOSITORY_NAME = /^[\p{L}\p{N}_.-]+\/[\p{L}\p{N}_.-]+$/u;

export interface SettingsInit {
  author?: string;
  concurrency?: number;
  copilotReview?: boolean;
  interval?: number;
  otelEndpoint?: null | string;
  repositories: readonly Repository[];
  retries?: number;
  stateDir: string;
  timeout?: number;
}

/** Pair an explicit GitHub repository with its checkout and verification commands. */
export class Repository {
  readonly build: readonly string[];
  readonly name: string;
  readonly path: string;
  readonly test: readonly string[];

  constructor(name: string, path: string, build: readonly string[], test: readonly string[]) {
    this.name = name;
    this.path = path;
    this.build = build;
    this.test = test;
  }
}

/** Configure discovery and bounded maintenance without library-global defaults. */
export class Settings {
  readonly author: string;
  readonly concurrency: number;
  readonly copilotReview: boolean;
  readonly interval: number;
  readonly otelEndpoint: null | string;
  readonly repositories: readonly Repository[];
  readonly retries: number;
  readonly stateDir: string;
  readonly timeout: number;

  constructor(init: SettingsInit) {
    this.repositories = init.repositories;
    this.stateDir = init.stateDir;
    this.author = init.author ?? '';
    this.interval = init.interval ?? 60;
    this.concurrency = init.concurrency ?? 2;
    this.retries = init.retries ?? 3;
    this.copilotReview = init.copilotReview ?? false;
    this.otelEndpoint = init.otelEndpoint ?? null;
    this.timeout = init.timeout ?? 1800;
  }

  /**
   * Parse the user-authored JSON settings file (snake_case keys, an unchanged contract).
   * Relative paths resolve against the file's directory. Invalid content throws {@link ValueError}.
   */
  static load(path: string): Settings {
    const file = resolvePath(path);
    const value = parseSettingsFile(file);
    const directory = dirname(file);
    const repositories = requireArray(value.repositories, 'repositories').map((item) => {
      const entry = requireObject(item, 'repository');
      const name = entry.name;
      if (typeof name !== 'string' || !REPOSITORY_NAME.test(name)) {
        throw new ValueError('repository name must be owner/repo');
      }
      const [build, test] = [entry.build, entry.test];
      if (!isCommand(build) || !isCommand(test)) {
        throw new ValueError('each repository requires build and test argument arrays');
      }
      return new Repository(name, resolvePath(resolve(directory, requireString(entry.path, 'path'))), build, test);
    });
    const stateDirectory = value.state_dir ?? '.loop/pr-babysit';
    const settings = new Settings({
      author: optionalString(value.author, 'author') ?? '',
      concurrency: toInt(value.concurrency ?? 2),
      copilotReview: truthy(value.copilot_review ?? false),
      interval: toFloat(value.interval ?? 60),
      otelEndpoint: optionalString(value.otel_endpoint, 'otel_endpoint') ?? null,
      repositories,
      retries: toInt(value.retries ?? 3),
      stateDir: resolvePath(resolve(directory, requireString(stateDirectory, 'state_dir'))),
      timeout: toFloat(value.timeout ?? 1800),
    });
    if (repositories.length === 0 || new Set(repositories.map((repo) => repo.name)).size !== repositories.length) {
      throw new ValueError('configure at least one repository, without duplicates');
    }
    if (
      !Number.isFinite(settings.interval) ||
      !Number.isFinite(settings.timeout) ||
      settings.interval <= 0 ||
      settings.concurrency < 1 ||
      settings.retries < 0 ||
      settings.timeout <= 0
    ) {
      throw new ValueError('invalid interval, concurrency, retries, or timeout');
    }
    return settings;
  }

  /** Reject unconfigured repositories before any Git operation. */
  repository(name: string): Repository {
    const found = this.repositories.find((repo) => repo.name === name);
    if (found === undefined) throw new ValueError(`Repository not configured: ${name}`);
    return found;
  }
}

/** Absolute path with symlinks resolved as far as the path exists, like Python's `Path.resolve()`. */
export function resolvePath(path: string): string {
  const absolute = resolve(path);
  const missing: string[] = [];
  let existing = absolute;
  while (!existsSync(existing) && dirname(existing) !== existing) {
    missing.unshift(basename(existing));
    existing = dirname(existing);
  }
  return resolve(realpathSync(existing), ...missing);
}

function isCommand(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.length > 0 && value.every((part) => typeof part === 'string' && part.length > 0)
  );
}

function optionalString(value: unknown, what: string): string | undefined {
  return value === null || value === undefined ? undefined : requireString(value, what);
}

function parseSettingsFile(file: string): JsonObject {
  // Strict decoding: invalid UTF-8 or a BOM is a settings error, as with the Python loader.
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(file));
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new ValueError(error instanceof Error ? error.message : String(error), { cause: error });
  }
  return requireObject(value, 'settings');
}

function requireArray(value: unknown, what: string): Json[] {
  if (!Array.isArray(value)) throw new ValueError(`${what} must be an array`);
  return value as Json[];
}

function requireObject(value: unknown, what: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ValueError(`${what} must be an object`);
  }
  return value as JsonObject;
}

function requireString(value: unknown, what: string): string {
  if (typeof value !== 'string') throw new ValueError(`${what} must be a string`);
  return value;
}

/** Python's `float()` on a JSON value. */
function toFloat(value: Json): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return Number(value);
  if (typeof value === 'string') {
    const text = value.trim();
    if (/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(text)) return Number(text);
    if (/^[+-]?(inf|infinity)$/i.test(text)) return text.startsWith('-') ? -Infinity : Infinity;
    if (/^[+-]?nan$/i.test(text)) return Number.NaN;
    throw new ValueError(`could not convert string to float: '${value}'`);
  }
  throw new ValueError('float() argument must be a string or a real number');
}

/** Python's `int()` on a JSON value: floats truncate toward zero, strings must be integer literals. */
function toInt(value: Json): number {
  if (typeof value === 'boolean') return Number(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new ValueError('cannot convert float to integer');
    return Math.trunc(value);
  }
  if (typeof value === 'string') {
    if (/^[+-]?\d+$/.test(value.trim())) return Number(value.trim());
    throw new ValueError(`invalid literal for int() with base 10: '${value}'`);
  }
  throw new ValueError('int() argument must be a string or a real number');
}

/** Python's `bool()`: every empty or zero value is false. */
function truthy(value: Json): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object' && value !== null) return Object.keys(value).length > 0;
  return Boolean(value);
}
