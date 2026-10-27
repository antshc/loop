import { createHash } from 'node:crypto';
import { stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';

import type { SessionLogReader } from './observing.ts';

import { LogReading, ToolCall, Usage } from '../observe/values.ts';
import { type SessionName, Start, type Turn } from '../sessions/values.ts';
import { splitLines } from '../text.ts';

type LogEvent = Record<string, unknown>;

const NANO = 1e9;
// Fixed namespace so a session name always maps to the same Copilot session id.
const SESSION_NAMESPACE = '6f1d2c3a-6c6f-4f70-8c6f-6f6f70000000';
const WINDOW_STARTS: readonly string[] = ['session.start', 'session.resume'];
const FRACTION = /\.(\d+)/g;

/** Pins a new Copilot session to a derivable id and reads its `events.jsonl` after each turn. */
export class CopilotLogReader implements SessionLogReader {
  readonly cli = 'copilot';
  readonly #home: null | string;

  constructor(home: null | string = null) {
    this.#home = home;
  }

  /** This turn's usage and tools; null when there is no log or it holds no per-call usage records. */
  async read(turn: Turn): Promise<LogReading | null> {
    const sessionId = copilotSessionId(turn.name);
    const path = join(this.#home ?? copilotHome(), 'session-state', sessionId, 'events.jsonl');
    const info = await stat(path).catch(() => null);
    if (info?.isFile() !== true) {
      return null;
    }
    return readTurn(sessionId, parseEvents(await Bun.file(path).text()));
  }

  /** `--session-id` on a Start; a Resume reaches the pinned session through the saved name. */
  startArgs(turn: Turn): readonly string[] {
    return turn instanceof Start ? ['--session-id', copilotSessionId(turn.name)] : [];
  }
}

/** The directory holding `session-state/`. */
export function copilotHome(): string {
  const configured = process.env.COPILOT_HOME;
  return configured !== undefined && configured !== '' ? configured : join(homedir(), '.copilot');
}

/** The Copilot session id a Loop session name is pinned to. */
export function copilotSessionId(name: SessionName): string {
  return uuid5(SESSION_NAMESPACE, name.value);
}

/** Reads the last turn of a session log: the events from its final `session.start` or `session.resume`. */
export function readTurn(sessionId: string, events: readonly LogEvent[]): LogReading | null {
  const window = lastTurn(events);
  const records = window.filter((event) => event.type === 'session.usage_record').map((event) => field(field(event, 'data'), 'usage'));
  // CLIs before 1.0.95 write no per-call records; their shutdown totals are cumulative and cannot be sliced.
  if (records.length === 0) {
    return null;
  }
  const messages = window.filter((event) => event.type === 'assistant.message').map((event) => field(event, 'data'));
  const first = window[0];
  const start = first !== undefined && WINDOW_STARTS.includes(String(first.type)) ? field(first, 'data') : {};
  const last = records.at(-1) ?? {};
  return new LogReading({
    model: firstTruthy(messages.at(-1)?.model, last.model, start.selectedModel),
    nativeId: sessionId,
    reasoningEffort: firstTruthy(last.reasoningEffort, start.reasoningEffort),
    tools: tools(window),
    usage: usage(records),
  });
}

/** A required key holding an object; a missing or mistyped one raises, as a malformed log must. */
function field(record: LogEvent, key: string): LogEvent {
  const value = record[key];
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new TypeError(`session log field ${key} is not an object`);
  }
  return value as LogEvent;
}

/** Python's `a or b or c`, as a string or null. */
function firstTruthy(...values: unknown[]): null | string {
  const found = values.find((value) => pyBool(value));
  return typeof found === 'string' ? found : null;
}

function lastTurn(events: readonly LogEvent[]): readonly LogEvent[] {
  const starts = events.flatMap((event, index) => (WINDOW_STARTS.includes(String(event.type)) ? [index] : []));
  const last = starts.at(-1);
  return last === undefined ? events : events.slice(last);
}

function number(record: LogEvent, key: string): number {
  const value = record[key];
  if (typeof value !== 'number') {
    throw new TypeError(`session log field ${key} is not a number`);
  }
  return value;
}

function numberOr(record: LogEvent, key: string, fallback: number): number {
  return record[key] === undefined ? fallback : number(record, key);
}

function parseEvents(text: string): LogEvent[] {
  return splitLines(text)
    .filter((line) => line.trim() !== '')
    .map((line) => field({ line: JSON.parse(line) }, 'line'));
}

/** Python truthiness: null, false, 0, empty text, list and object are false. */
function pyBool(value: unknown): boolean {
  if (typeof value === 'object' && value !== null) {
    return Object.keys(value).length > 0;
  }
  return Boolean(value);
}

/** The quotient of the exact integer product over `batch`, rounded once like integer true division. */
function ratio(count: number, cost: number, batch: number): number {
  if (!Number.isSafeInteger(count) || !Number.isSafeInteger(cost) || !Number.isSafeInteger(batch) || batch === 0) {
    return (count * cost) / batch;
  }
  const negative = count < 0 !== (cost < 0) !== (batch < 0);
  const numerator = BigInt(Math.abs(count)) * BigInt(Math.abs(cost));
  const denominator = BigInt(Math.abs(batch));
  if (numerator === 0n) {
    return 0;
  }
  // Keep 66+ quotient bits and fold the remainder into a sticky bit, so one final rounding is exact.
  const shift = Math.max(0, 66 - (numerator.toString(2).length - denominator.toString(2).length));
  const scaled = numerator << BigInt(shift);
  const quotient = ((scaled / denominator) << 1n) | (scaled % denominator === 0n ? 0n : 1n);
  const magnitude = Number(quotient) * 2 ** -(shift + 1);
  return negative ? -magnitude : magnitude;
}

function sum(records: readonly LogEvent[], key: string, fallback?: number): number {
  return records.reduce((total, record) => total + (fallback === undefined ? number(record, key) : numberOr(record, key, fallback)), 0);
}

function text(record: LogEvent, key: string): string {
  const value = record[key];
  if (typeof value !== 'string') {
    throw new TypeError(`session log field ${key} is not text`);
  }
  return value;
}

function tools(window: readonly LogEvent[]): ToolCall[] {
  const completed = new Map<string, LogEvent>();
  for (const event of window.filter((item) => item.type === 'tool.execution_complete')) {
    completed.set(text(field(event, 'data'), 'toolCallId'), event);
  }
  const calls: ToolCall[] = [];
  for (const start of window.filter((event) => event.type === 'tool.execution_start')) {
    const data = field(start, 'data');
    const done = completed.get(text(data, 'toolCallId'));
    if (done === undefined) {
      continue;
    }
    const doneData = field(done, 'data');
    const result = pyBool(doneData.result) ? field(doneData, 'result') : {};
    const content = result.content ?? '';
    calls.push(
      new ToolCall({
        arguments: data.arguments === undefined ? {} : field(data, 'arguments'),
        callId: text(data, 'toolCallId'),
        endedAt: when(done),
        name: text(data, 'toolName'),
        ok: pyBool(doneData.success),
        result: typeof content === 'string' ? content : JSON.stringify(content),
        startedAt: when(start),
      }),
    );
  }
  return calls;
}

function usage(records: readonly LogEvent[]): Usage {
  const aiu: Record<string, number> = {};
  let premium = 0;
  for (const record of records) {
    // Agent-initiated follow-up calls also report a cost, but only the user's prompt is a premium request.
    if (record.initiator === 'user') {
      premium += numberOr(record, 'cost', 0);
    }
    const detail = record.copilotUsage === undefined ? {} : field(record, 'copilotUsage');
    const items = detail.tokenDetails === undefined ? [] : (detail.tokenDetails as unknown[]);
    for (const raw of items) {
      const item = field({ item: raw }, 'item');
      const batch = ratio(number(item, 'tokenCount'), number(item, 'costPerBatch'), number(item, 'batchSize'));
      const type = text(item, 'tokenType');
      aiu[type] = (aiu[type] ?? 0) + batch / NANO;
    }
  }
  const cacheRead = sum(records, 'cacheReadTokens');
  const cacheWrite = sum(records, 'cacheWriteTokens');
  return new Usage({
    aiu,
    apiCalls: records.length,
    cacheReadTokens: cacheRead,
    cacheWriteTokens: cacheWrite,
    inputTokens: sum(records, 'inputTokens') - cacheRead - cacheWrite,
    outputTokens: sum(records, 'outputTokens'),
    premiumRequests: premium,
    reasoningTokens: sum(records, 'reasoningTokens', 0),
  });
}

/** A name-based (version 5) UUID. */
function uuid5(namespace: string, name: string): string {
  const digest = createHash('sha1')
    .update(Buffer.from(namespace.replaceAll('-', ''), 'hex'))
    .update(name)
    .digest();
  const bytes = digest.subarray(0, 16);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x50;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** The session-log timestamp as an instant, truncated to the millisecond a Date holds. */
function when(event: LogEvent): Date {
  // The CLI sometimes writes 2 fractional digits, which not every parser accepts; normalize to 3.
  const stamp = String(event.timestamp).replaceAll(FRACTION, (_match, digits: string) => `.${digits.padEnd(3, '0').slice(0, 3)}`);
  const instant = new Date(stamp);
  if (Number.isNaN(instant.getTime())) {
    throw new RangeError(`invalid session log timestamp: ${stamp}`);
  }
  return instant;
}
