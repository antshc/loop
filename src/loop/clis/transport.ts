/** Per-CLI stdout transports: decode a CLI's stdout into the assistant text, and encode it back for dry run. */

import { dumps, ordered } from '../json.ts';
import { splitLines } from '../text.ts';

/** Adapter for one CLI's stdout shape; pure and total, so dry run can round-trip through it. */
export interface Transport {
  readonly args: readonly string[];
  /** Unreadable lines are skipped. */
  decode: (stdout: string) => Transcript;
  encode: (text: string, handleValue: null | string) => string;
}

type JsonObject = Record<string, unknown>;

/** `codex exec --json`: one JSON event per line; Codex assigns its own thread id via `thread.started`. */
export class CodexJson implements Transport {
  readonly args: readonly string[] = Object.freeze(['--json']);

  decode(stdout: string): Transcript {
    let threadId: null | string = null;
    let message = '';
    for (const event of events(stdout)) {
      const item = asObject(event.item);
      if (event.type === 'thread.started') {
        threadId = asString(event.thread_id);
      } else if (event.type === 'item.completed' && item?.type === 'agent_message') {
        message = asString(item.text) ?? '';
      }
    }
    return new Transcript(message, threadId, null);
  }

  encode(text: string, handleValue: null | string): string {
    const started = ordered([
      ['type', 'thread.started'],
      ['thread_id', handleValue],
    ]);
    const completed = ordered([
      ['type', 'item.completed'],
      ['item', ordered([['type', 'agent_message'], ['text', text]])],
    ]);
    return [started, completed].map((event) => dumps(event)).join('\n');
  }
}

/** `copilot --output-format json`: one JSON event per line, per copilot-cli-json-output-events.md. */
export class CopilotJson implements Transport {
  readonly args: readonly string[] = Object.freeze(['--output-format', 'json']);

  decode(stdout: string): Transcript {
    const parts: string[] = [];
    let sessionId: null | string = null;
    let exitCode: null | number = null;
    for (const event of events(stdout)) {
      if (event.type === 'assistant.message_delta') {
        parts.push(asString(asObject(event.data)?.deltaContent) ?? '');
      } else if (event.type === 'result') {
        sessionId = asString(event.sessionId);
        exitCode = typeof event.exitCode === 'number' ? event.exitCode : null;
      }
    }
    return new Transcript(parts.join(''), sessionId, exitCode);
  }

  encode(text: string, handleValue: null | string): string {
    const delta = ordered([
      ['type', 'assistant.message_delta'],
      ['data', ordered([['messageId', 'dry-run'], ['deltaContent', text]])],
    ]);
    const result = ordered([
      ['type', 'result'],
      ['sessionId', handleValue],
      ['exitCode', 0],
    ]);
    return [delta, result].map((event) => dumps(event)).join('\n');
  }
}

/** `copilot --output-format text`: stdout is already the assistant's text. */
export class CopilotText implements Transport {
  readonly args: readonly string[] = Object.freeze(['--output-format', 'text']);

  decode(stdout: string): Transcript {
    return new Transcript(stdout, null, null);
  }

  encode(text: string): string {
    return text;
  }
}

/** One decoded turn: the assistant's text, the handle the stream carried (if any), and its reported exit code. */
export class Transcript {
  readonly exitCode: null | number;
  readonly handleValue: null | string;
  readonly text: string;

  constructor(text: string, handleValue: null | string, exitCode: null | number) {
    this.text = text;
    this.handleValue = handleValue;
    this.exitCode = exitCode;
    Object.freeze(this);
  }
}

function asObject(value: unknown): JsonObject | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as JsonObject) : null;
}

function asString(value: unknown): null | string {
  return typeof value === 'string' ? value : null;
}

/** The JSON object on every line that holds one; other lines are skipped. */
function* events(stdout: string): Generator<JsonObject> {
  for (const line of splitLines(stdout)) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }
    const event = asObject(parsed);
    if (event !== null) {
      yield event;
    }
  }
}
