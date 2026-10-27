export type Json = boolean | Json[] | null | number | string | { [key: string]: Json };

/** Serializes the way persisted and emitted JSON always has: ASCII-only, with `, ` and `: ` separators. */
export function dumps(value: Json, indent?: number): string {
  return encode(value, indent, 0);
}

/** An object whose keys serialize in the order given, which sorted object literals cannot promise. */
export function ordered(entries: readonly (readonly [string, Json])[]): Json {
  return Object.fromEntries(entries);
}

function encode(value: Json, indent: number | undefined, depth: number): string {
  if (value === null) {
    return 'null';
  }
  if (typeof value === 'string') {
    return quote(value);
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return JSON.stringify(value);
  }
  const entries = Array.isArray(value)
    ? value.map((item) => encode(item, indent, depth + 1))
    : Object.entries(value).map(([key, item]) => `${quote(key)}: ${encode(item, indent, depth + 1)}`);
  const [open, close] = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
  if (entries.length === 0) {
    return open + close;
  }
  if (indent === undefined) {
    return open + entries.join(', ') + close;
  }
  const inner = '\n' + ' '.repeat(indent * (depth + 1));
  return open + inner + entries.join(',' + inner) + '\n' + ' '.repeat(indent * depth) + close;
}

function quote(text: string): string {
  // JSON.stringify leaves non-ASCII text as is; escape it by UTF-16 code unit like Python does.
  return JSON.stringify(text).replaceAll(/[\u007f-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
}
