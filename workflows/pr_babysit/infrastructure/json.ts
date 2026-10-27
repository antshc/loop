export type Json = boolean | Json[] | JsonObject | null | number | string;

export interface JsonObject {
  [key: string]: Json;
}

/** Order strings by code point, as Python does, rather than by UTF-16 code unit. */
export function compareCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (char) => char.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (char) => char.codePointAt(0) ?? 0);
  const shared = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < shared; index++) {
    const difference = (leftPoints[index] ?? 0) - (rightPoints[index] ?? 0);
    if (difference !== 0) return Math.sign(difference);
  }
  return Math.sign(leftPoints.length - rightPoints.length);
}

/**
 * Serialize the way PR state and the registry have always been written: 2-space indent, keys sorted by code point,
 * ASCII-only text and no trailing newline. The compact, insertion-ordered dev execution log is a different encoding.
 */
export function encodeJson(value: Json): string {
  return encode(value, 0);
}

/**
 * Python's `json.dumps(value, sort_keys=True)`: keys sorted by code point, ", " and ": " separators and ASCII-only text.
 * Digests of GitHub inputs are taken over this text, so it stays byte-identical to what the Python version hashed.
 */
export function encodeSortedCompact(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'string') return quote(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return formatNumber(value);
  if (Array.isArray(value)) return `[${value.map((item) => encodeSortedCompact(item)).join(', ')}]`;
  if (typeof value !== 'object') throw new TypeError(`Cannot encode a ${typeof value} as JSON`);
  const entries = Object.entries(value)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => compareCodePoints(left, right))
    .map(([key, item]) => `${quote(key)}: ${encodeSortedCompact(item)}`);
  return `{${entries.join(', ')}}`;
}

function encode(value: Json, depth: number): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return quote(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return formatNumber(value);
  const entries = Array.isArray(value)
    ? value.map((item) => encode(item, depth + 1))
    : Object.entries(value)
        .sort(([left], [right]) => compareCodePoints(left, right))
        .map(([key, item]) => `${quote(key)}: ${encode(item, depth + 1)}`);
  const [open, close] = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
  if (entries.length === 0) return open + close;
  const inner = '\n' + '  '.repeat(depth + 1);
  return open + inner + entries.join(',' + inner) + '\n' + '  '.repeat(depth) + close;
}

/** Integers print as digits; fractions use Python's `repr` layout so files stay byte-identical. */
function formatNumber(value: number): string {
  if (!Number.isFinite(value)) throw new RangeError('JSON cannot hold a non-finite number');
  if (Number.isInteger(value)) return BigInt(value).toString();
  const [mantissa = '', exponentText = '0'] = value.toExponential().split('e');
  const exponent = Number(exponentText);
  const sign = mantissa.startsWith('-') ? '-' : '';
  const digits = mantissa.replace('-', '').replace('.', '');
  if (exponent < -4 || exponent >= 16) {
    const head = digits.length > 1 ? `${digits.slice(0, 1)}.${digits.slice(1)}` : digits;
    return `${sign}${head}e${exponent < 0 ? '-' : '+'}${String(Math.abs(exponent)).padStart(2, '0')}`;
  }
  if (exponent < 0) return `${sign}0.${'0'.repeat(-exponent - 1)}${digits}`;
  const integer = digits.slice(0, exponent + 1).padEnd(exponent + 1, '0');
  return `${sign}${integer}.${digits.slice(exponent + 1) || '0'}`;
}

function quote(text: string): string {
  // JSON.stringify leaves non-ASCII text as is; escape it by UTF-16 code unit like Python does.
  return JSON.stringify(text).replaceAll(
    /[\u007f-\uffff]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`,
  );
}
