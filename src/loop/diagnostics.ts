import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

import { dumps, type Json, ordered } from './json.ts';

export const DEFAULT_LOG_FILE = 'loop.log';

const SEVERITY: Readonly<Record<LogLevel, number>> = { debug: 10, error: 40, info: 20, warning: 30 };
const NAMED_LEVELS: Readonly<Record<string, number>> = {
  CRITICAL: 50,
  DEBUG: 10,
  ERROR: 40,
  FATAL: 50,
  INFO: 20,
  WARN: 30,
  WARNING: 30,
};

/** Structured fields recorded next to a message. */
export type LogExtra = Readonly<Record<string, Json>>;

/** A named logger; library code logs through it and stays silent until a sink is installed. */
export interface Logger {
  debug: (message: string, extra?: LogExtra) => void;
  error: (message: string, extra?: LogExtra) => void;
  info: (message: string, extra?: LogExtra) => void;
  warning: (message: string, extra?: LogExtra) => void;
}

export interface LoggingOptions {
  /** `debug` and above also write JSON lines to `logFile`; unknown names fall back to `info`. Default `info`. */
  level?: string;
  /** Where the JSON lines go at `debug`; null disables the file. Default `loop.log`. */
  logFile?: null | string;
  /** The logger subtree configured; `''` covers every logger of the application. Default `loop`. */
  logger?: string;
  /** Console destination. Default stderr. */
  stream?: LogStream;
}

export type LogLevel = 'debug' | 'error' | 'info' | 'warning';

export interface LogRecord {
  readonly extra: LogExtra;
  readonly level: LogLevel;
  readonly logger: string;
  readonly message: string;
}

export type LogSink = (record: LogRecord) => void;

/** Where console lines are written. */
export interface LogStream {
  write: (text: string) => unknown;
}

let sink: LogSink | null = null;

/**
 * Logs to the console at `level`, and at `debug` also writes JSON lines to the file.
 * Replaces any earlier configuration, so repeated calls never duplicate output.
 */
export function configureLogging(options: LoggingOptions = {}): void {
  const threshold = thresholdOf(options.level ?? 'info');
  const scope = options.logger ?? 'loop';
  const stream = options.stream ?? process.stderr;
  const file =
    threshold <= SEVERITY.debug ? (options.logFile === undefined ? DEFAULT_LOG_FILE : options.logFile) : null;
  if (file !== null) {
    mkdirSync(dirname(file), { recursive: true });
    appendFileSync(file, '');
  }
  setLogSink((record) => {
    if (SEVERITY[record.level] < threshold || !inScope(record.logger, scope)) {
      return;
    }
    stream.write(consoleLine(record));
    if (file !== null) {
      appendFileSync(file, `${jsonLine(record)}\n`);
    }
  });
}

/** The logger for one library module, such as `loop.git.strategies`. */
export function getLogger(name: string): Logger {
  const emit = (level: LogLevel, message: string, extra: LogExtra = {}): void => {
    try {
      sink?.({ extra, level, logger: name, message });
    } catch {
      // Logging never fails the run.
    }
  };
  return {
    debug: (message, extra) => {
      emit('debug', message, extra);
    },
    error: (message, extra) => {
      emit('error', message, extra);
    },
    info: (message, extra) => {
      emit('info', message, extra);
    },
    warning: (message, extra) => {
      emit('warning', message, extra);
    },
  };
}

/** One log record as a JSON line: `{"message", ...extras}` with no level, time or name fields. */
export function jsonLine(record: LogRecord): string {
  const extras = Object.entries(record.extra).filter(([key]) => key !== 'message');
  return dumps(ordered([['message', record.message], ...extras]));
}

/** Installs the process-wide sink (null restores silence) and returns the previous one. */
export function setLogSink(next: LogSink | null): LogSink | null {
  const previous = sink;
  sink = next;
  return previous;
}

function consoleLine(record: LogRecord): string {
  const now = new Date();
  const pad = (value: number, width = 2): string => String(value).padStart(width, '0');
  const date = `${String(now.getFullYear())}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())},${pad(now.getMilliseconds(), 3)}`;
  return `${date} ${time} ${record.level.toUpperCase()} ${record.logger}: ${record.message}\n`;
}

// A name is in scope when it is the configured logger or below it, as in dotted logger hierarchies.
function inScope(name: string, scope: string): boolean {
  return scope === '' || name === scope || name.startsWith(`${scope}.`);
}

function thresholdOf(level: string): number {
  return NAMED_LEVELS[level.toUpperCase()] ?? SEVERITY.info;
}
