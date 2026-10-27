/** Content extraction and interpretation of the reviewer's response. */
import { type ReviewAxis, type ReviewFinding, ReviewResult, TicketWorkflowError } from './records.ts';

type JsonObject = Readonly<Record<string, unknown>>;

/** `text` carried no value this decoder recognizes. */
export class ContentDecodeError extends Error {
  readonly output: string;

  constructor(message: string, output: string) {
    super(message);
    this.name = 'ContentDecodeError';
    this.output = output;
  }
}

/** The agent's response is missing, not valid JSON, or missing a required `result` field. */
export class DevResultError extends TicketWorkflowError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'DevResultError';
  }
}

/** The last top-level JSON object in the text satisfying `where`; raises when none does. */
export class LastJsonObject {
  readonly #where: (value: JsonObject) => boolean;

  constructor(where: (value: JsonObject) => boolean = () => true) {
    this.#where = where;
  }

  decode(text: string): JsonObject {
    let found: JsonObject | null = null;
    let index = text.indexOf('{');
    while (index !== -1) {
      const decoded = objectAt(text, index);
      if (decoded !== null && this.#where(decoded.value)) {
        found = decoded.value;
        index = text.indexOf('{', decoded.end);
      } else {
        index = text.indexOf('{', index + 1);
      }
    }
    if (found === null) {
      throw new ContentDecodeError('no matching JSON object in text', text);
    }
    return found;
  }
}

/** Decodes the reviewer's per-axis outcomes, findings, posted comment references and reviewed head. */
export class ReviewResponse {
  decode(text: string): ReviewResult {
    const envelope = envelopeOf(text);
    const result = envelope.result;
    if (envelope.status === 'failed') {
      throw new DevResultError(`review failed: ${failure(result)}`);
    }
    const axes: ReviewAxis[] = [];
    const findings: ReviewFinding[] = [];
    for (const item of itemsOf(result, 'axes')) {
      if (!isObject(item)) {
        throw new DevResultError(`axis must be an object: ${pythonRepr(item)}`);
      }
      const name = textOf(item, 'name', true);
      const outcome = textOf(item, 'outcome', true).toLowerCase();
      axes.push({ name, note: textOf(item, 'note'), outcome });
    }
    for (const item of itemsOf(result, 'findings')) {
      if (!isObject(item)) {
        throw new DevResultError(`finding must be an object: ${pythonRepr(item)}`);
      }
      const kind = textOf(item, 'kind', true).toLowerCase();
      const summary = textOf(item, 'summary', true);
      findings.push({ axis: textOf(item, 'axis'), kind, summary });
    }
    const posted = itemsOf(result, 'posted').map((item) => pythonString(item));
    return new ReviewResult({ axes, findings, headSha: textOf(result, 'head'), posted });
  }
}

/** The last `{"status": ..., "result": {...}}` object in `text`. */
function envelopeOf(text: string): { readonly result: JsonObject; readonly status: string } {
  let envelope: JsonObject;
  try {
    envelope = new LastJsonObject((value) => Object.hasOwn(value, 'status')).decode(text);
  } catch (error) {
    if (error instanceof ContentDecodeError) {
      throw new DevResultError('no response object in agent output', { cause: error });
    }
    throw error;
  }
  const { result, status } = envelope;
  if ((status !== 'completed' && status !== 'failed') || !isObject(result)) {
    throw new DevResultError(`malformed response envelope: ${pythonRepr(envelope)}`);
  }
  return { result, status };
}

function failure(result: JsonObject): string {
  const reason = result.reason;
  if (typeof reason !== 'string') {
    throw new DevResultError(`failed response is missing result.reason: ${pythonRepr(result)}`);
  }
  return reason;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function itemsOf(result: JsonObject, key: string): readonly unknown[] {
  const value = Object.hasOwn(result, key) ? result[key] : [];
  if (!Array.isArray(value)) {
    throw new DevResultError(`result.${key} must be a list: ${pythonRepr(result)}`);
  }
  return value as readonly unknown[];
}

/** The first JSON object starting at `start` and where it ends, or null when the text there is not one. */
function objectAt(text: string, start: number): null | { end: number; value: JsonObject } {
  let depth = 0;
  let inString = false;
  for (let index = start; index < text.length; index++) {
    const character = text.charAt(index);
    if (inString) {
      if (character === '\\') {
        index++;
      } else if (character === '"') {
        inString = false;
      }
    } else if (character === '"') {
      inString = true;
    } else if (character === '{') {
      depth++;
    } else if (character === '}') {
      depth--;
      if (depth === 0) {
        return parseObject(text.slice(start, index + 1), index + 1);
      }
    }
  }
  return null;
}

function parseObject(slice: string, end: number): null | { end: number; value: JsonObject } {
  try {
    const value: unknown = JSON.parse(slice);
    return isObject(value) ? { end, value } : null;
  } catch {
    return null;
  }
}

/** Python's `repr` of a decoded JSON value, so messages persisted by the Python Workflow keep their form. */
function pythonRepr(value: unknown): string {
  if (typeof value === 'string') {
    return quoted(value);
  }
  if (Array.isArray(value)) {
    return `[${(value as readonly unknown[]).map((item) => pythonRepr(item)).join(', ')}]`;
  }
  if (isObject(value)) {
    return `{${Object.entries(value)
      .map(([key, item]) => `${quoted(key)}: ${pythonRepr(item)}`)
      .join(', ')}}`;
  }
  return pythonString(value);
}

/** Python's `str` of a decoded JSON value. */
function pythonString(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (value === null || value === undefined) {
    return 'None';
  }
  if (typeof value === 'boolean') {
    return value ? 'True' : 'False';
  }
  if (typeof value === 'number') {
    return String(value);
  }
  return pythonRepr(value);
}

function quoted(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  let escaped = '';
  for (const character of text) {
    const code = character.codePointAt(0) ?? 0;
    if (character === '\\' || character === quote) {
      escaped += `\\${character}`;
    } else if (character === '\n') {
      escaped += '\\n';
    } else if (character === '\r') {
      escaped += '\\r';
    } else if (character === '\t') {
      escaped += '\\t';
    } else if (code < 0x20 || (code >= 0x7f && code < 0xa0)) {
      escaped += `\\x${code.toString(16).padStart(2, '0')}`;
    } else {
      escaped += character;
    }
  }
  return `${quote}${escaped}${quote}`;
}

function textOf(result: JsonObject, key: string, required = false): string {
  const value = Object.hasOwn(result, key) ? result[key] : '';
  if (typeof value === 'string' && (value.trim() !== '' || !required)) {
    return value;
  }
  throw new DevResultError(`response is missing result.${key}: ${pythonRepr(result)}`);
}
