/** Content extraction and interpretation of dev agent responses. */
import type {
  AgentCommitSubject,
  ChoreResult,
  PlannedTicket,
  PlanResult,
  TicketResult,
  VerifyFinding,
  VerifyResult,
} from './records.ts';

import { pythonRepr } from './format.ts';
import { TicketWorkflowError, VERDICTS } from './records.ts';

type JsonObject = Record<string, unknown>;

/** Decodes the chore agent's response; the step is best effort, so a missing or malformed response is an empty result. */
export class ChoreResponse {
  decode(text: string): Required<ChoreResult> {
    try {
      const envelope = envelopeOf(text);
      if (envelope.status === 'failed') {
        return emptyChore();
      }
      const result = envelope.result;
      return {
        commitMessage: textOf(result, 'commit_message'),
        outOfScope: itemsOf(result, 'out_of_scope').map((item) => (typeof item === 'string' ? item : pythonRepr(item))),
        summary: textOf(result, 'summary'),
      };
    } catch (error) {
      if (error instanceof DevResultError) {
        return emptyChore();
      }
      throw error;
    }
  }
}

/** `text` carried no value this decoder recognizes. */
export class ContentDecodeError extends Error {
  readonly output: string;

  constructor(message: string, output: string) {
    super(message);
    this.name = 'ContentDecodeError';
    this.output = output;
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
      const parsed = parseObjectAt(text, index);
      if (parsed !== null && this.#where(parsed.value)) {
        found = parsed.value;
        index = text.indexOf('{', parsed.end);
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

/** Decodes a run's response envelope for `identifier`; call `decode` directly on `AgentResult.output`. */
export class DevResponse {
  readonly #envelope = new LastJsonObject((value) => 'status' in value);
  readonly #identifier: AgentCommitSubject;

  constructor(identifier: AgentCommitSubject) {
    this.#identifier = identifier;
  }

  /** Decodes the last response object in `text` and checks it answers this task. */
  decode(text: string): TicketResult {
    let envelope: JsonObject;
    try {
      envelope = this.#envelope.decode(text);
    } catch (error) {
      if (error instanceof ContentDecodeError) {
        throw new DevResultError('no response object in agent output', { cause: error });
      }
      throw error;
    }
    if ('identifier' in envelope && envelope.identifier !== this.#identifier.toString()) {
      throw new DevResultError(
        `response identifier ${pythonRepr(envelope.identifier)} does not match task ${identifierRepr(this.#identifier)}`,
      );
    }
    return parseDevResult({ ...envelope, identifier: this.#identifier.toString() });
  }
}

/** The agent's response is missing, not valid JSON, or missing a required `result` field. */
export class DevResultError extends TicketWorkflowError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'DevResultError';
  }
}

/** Decodes the planner's `{"status", "result": {"tickets": [{"title", "body", "blocked_by"}]}}` object. */
export class PlanResponse {
  decode(text: string): PlanResult {
    const envelope = envelopeOf(text);
    if (envelope.status === 'failed') {
      return { reason: failureOf(envelope), status: 'failed', tickets: [] };
    }
    const tickets: PlannedTicket[] = [];
    for (const item of itemsOf(envelope.result, 'tickets')) {
      if (!isObject(item)) {
        throw new DevResultError(`planned ticket must be an object: ${pythonRepr(item)}`);
      }
      const blockers = 'blocked_by' in item ? item.blocked_by : [];
      if (!Array.isArray(blockers) || !blockers.every((blocker) => Number.isInteger(blocker))) {
        throw new DevResultError(`planned ticket blocked_by must be a list of indexes: ${pythonRepr(item)}`);
      }
      tickets.push({
        blockedBy: blockers as number[],
        body: textOf(item, 'body'),
        title: textOf(item, 'title', true).trim(),
      });
    }
    return { reason: null, status: 'completed', tickets };
  }
}

/** Decodes the verifier's verdict, findings and proposed fix-commit message; a failed response is `inconclusive`. */
export class VerifyResponse {
  decode(text: string): Required<VerifyResult> {
    const envelope = envelopeOf(text);
    if (envelope.status === 'failed') {
      return { commitMessage: '', findings: [], summary: failureOf(envelope), verdict: 'inconclusive', verification: '' };
    }
    const result = envelope.result;
    const verdict = textOf(result, 'verdict', true).trim().toLowerCase();
    if (!(VERDICTS as readonly string[]).includes(verdict)) {
      throw new DevResultError(`unknown verdict: ${pythonRepr(verdict)}`);
    }
    const findings: Required<VerifyFinding>[] = [];
    for (const item of itemsOf(result, 'findings')) {
      if (!isObject(item)) {
        throw new DevResultError(`finding must be an object: ${pythonRepr(item)}`);
      }
      findings.push({
        reason: textOf(item, 'reason'),
        status: textOf(item, 'status') || 'unfixed',
        summary: textOf(item, 'summary', true),
      });
    }
    return {
      commitMessage: textOf(result, 'commit_message'),
      findings,
      summary: textOf(result, 'summary'),
      verdict,
      verification: textOf(result, 'verification'),
    };
  }
}

/** The last `{"status": ..., "result": {...}}` object in `text`; shared by every agent's decoder. */
export function envelopeOf(text: string): JsonObject & { result: JsonObject } {
  let envelope: JsonObject;
  try {
    envelope = new LastJsonObject((value) => 'status' in value).decode(text);
  } catch (error) {
    if (error instanceof ContentDecodeError) {
      throw new DevResultError('no response object in agent output', { cause: error });
    }
    throw error;
  }
  const result = envelope.result;
  if ((envelope.status !== 'completed' && envelope.status !== 'failed') || !isObject(result)) {
    throw new DevResultError(`malformed response envelope: ${pythonRepr(envelope)}`);
  }
  return { ...envelope, result };
}

/** The reason a failed envelope carries. */
export function failureOf(envelope: JsonObject & { result: JsonObject }): string {
  const reason = envelope.result.reason;
  if (typeof reason !== 'string') {
    throw new DevResultError(`failed response is missing result.reason: ${pythonRepr(envelope.result)}`);
  }
  return reason;
}

export function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** A list the response may omit; anything else but a list is rejected. */
export function itemsOf(result: JsonObject, key: string): unknown[] {
  const value = key in result ? result[key] : [];
  if (!Array.isArray(value)) {
    throw new DevResultError(`result.${key} must be a list: ${pythonRepr(result)}`);
  }
  return value as unknown[];
}

/** Decodes a run's response envelope for the `completed` and `failed` shapes `dev` understands. */
export function parseDevResult(envelope: JsonObject): TicketResult {
  const { identifier, result, status } = envelope;
  if (typeof identifier !== 'string' || (status !== 'completed' && status !== 'failed') || !isObject(result)) {
    throw new DevResultError(`malformed response envelope: ${pythonRepr(envelope)}`);
  }
  if (status === 'failed') {
    const reason = result.reason;
    if (typeof reason !== 'string') {
      throw new DevResultError(`failed response is missing result.reason: ${pythonRepr(result)}`);
    }
    return { identifier, reason, status: 'failed' };
  }
  const { commit, summary, verification } = result;
  if (typeof commit !== 'string' || typeof summary !== 'string' || typeof verification !== 'string') {
    throw new DevResultError(`completed response is missing a required result field: ${pythonRepr(result)}`);
  }
  return { commit, identifier, status: 'completed', summary, verification };
}

/** A text field; a missing one is empty unless `required`. */
export function textOf(result: JsonObject, key: string, required = false): string {
  const value = key in result ? result[key] : '';
  if (typeof value === 'string' && (value.trim() !== '' || !required)) {
    return value;
  }
  throw new DevResultError(`response is missing result.${key}: ${pythonRepr(result)}`);
}

function emptyChore(): Required<ChoreResult> {
  return { commitMessage: '', outOfScope: [], summary: '' };
}

/** The dataclass `repr` Python put in messages about the asked task. */
function identifierRepr(identifier: AgentCommitSubject): string {
  return `AgentCommitSubject(initiative=${pythonRepr(identifier.initiative)}, ticket_number=${String(identifier.ticketNumber)})`;
}

/** Where a JSON object starting at `start` ends, or null when none starts there. */
function parseObjectAt(text: string, start: number): null | { end: number; value: JsonObject } {
  let depth = 0;
  let inString = false;
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      if (char === '\\') {
        index += 1;
      } else if (char === '"') {
        inString = false;
      }
    } else if (char === '"') {
      inString = true;
    } else if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        try {
          const value = JSON.parse(text.slice(start, index + 1)) as unknown;
          return isObject(value) ? { end: index + 1, value } : null;
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}
