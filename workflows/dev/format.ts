/** Text the Python Workflow produced: `repr`, `json.dumps(indent=2)`, and the Ticket and Comment dictionaries of `asdict`. */
import type { Comment, Ticket } from './records.ts';

export function commentJson(comment: Comment): Record<string, unknown> {
  return ordered([
    ['author', comment.author],
    ['body', comment.body],
    ['created_at', comment.createdAt],
  ]);
}

/** `json.dumps(value, indent=2)`: two-space indent, non-ASCII escaped by UTF-16 code unit, keys in insertion order. */
export function indentedJson(value: unknown): string {
  const text = JSON.stringify(value, null, 2);
  return text.replaceAll(/[\u007f-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

/** An object whose keys keep the order given, which sorted object literals cannot promise. */
export function ordered(entries: readonly (readonly [string, unknown])[]): Record<string, unknown> {
  return Object.fromEntries(entries);
}

/** Python's `repr` of a JSON-like value; `undefined` reads as `None`. */
export function pythonRepr(value: unknown): string {
  if (value === null || value === undefined) {
    return 'None';
  }
  if (typeof value === 'string') {
    return quoted(value);
  }
  if (typeof value === 'boolean') {
    return value ? 'True' : 'False';
  }
  if (typeof value === 'number' || typeof value === 'bigint') {
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(pythonRepr).join(', ')}]`;
  }
  if (typeof value === 'object') {
    return `{${Object.entries(value).map(([key, item]) => `${quoted(key)}: ${pythonRepr(item)}`).join(', ')}}`;
  }
  return `<${typeof value}>`;
}

/** `dataclasses.asdict(ticket)`: the prompt and verifier JSON keep Python's field order and `created_at` key. */
export function ticketJson(ticket: Ticket): Record<string, unknown> {
  return ordered([
    ['number', ticket.number],
    ['title', ticket.title],
    ['state', ticket.state],
    ['labels', [...ticket.labels]],
    ['url', ticket.url],
    ['body', ticket.body],
    ['comments', ticket.comments.map(commentJson)],
  ]);
}

function quoted(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  const escaped = text
    .replaceAll('\\', '\\\\')
    .replaceAll('\n', '\\n')
    .replaceAll('\r', '\\r')
    .replaceAll('\t', '\\t')
    .replaceAll(quote, `\\${quote}`);
  return `${quote}${escaped}${quote}`;
}
