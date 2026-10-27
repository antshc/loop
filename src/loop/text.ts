/** Text helpers that reproduce the exact string behavior persisted formats depend on. */

const LINE_BREAKS: ReadonlySet<number> = new Set([0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029]);

/** The error as `Name('message')`, the form logged and recorded for a failure. */
export function describeError(error: unknown): string {
  if (error instanceof Error) {
    return `${error.name}(${JSON.stringify(error.message)})`;
  }
  return String(error);
}

/** Splits on every line boundary and drops the empty tail, so `a\n` is `['a']`. */
export function splitLines(text: string): string[] {
  const lines: string[] = [];
  let start = 0;
  for (let index = 0; index < text.length; index++) {
    if (!LINE_BREAKS.has(text.charCodeAt(index))) {
      continue;
    }
    lines.push(text.slice(start, index));
    if (text.charCodeAt(index) === 0x0d && text.charCodeAt(index + 1) === 0x0a) {
      index++;
    }
    start = index + 1;
  }
  if (start < text.length) {
    lines.push(text.slice(start));
  }
  return lines;
}
