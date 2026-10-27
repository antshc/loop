import type { Phase } from '../phases.ts';

/** The phase's label with its spec, ticket and attempt identity. */
export function label(phase: Phase): string {
  let result = phase.label;
  for (const [name, prefix] of [
    ['spec', 'spec #'],
    ['ticket', 'ticket #'],
    ['attempt', 'attempt '],
  ] as const) {
    if (name in phase.attributes) {
      result += ` · ${prefix}${text(phase.attributes[name])}`;
    }
  }
  return result;
}

/** Python-style text for a business value, so records and tables read as they always have. */
export function text(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (value === null || value === undefined) {
    return 'None';
  }
  if (typeof value === 'boolean') {
    return value ? 'True' : 'False';
  }
  if (typeof value === 'number' || typeof value === 'bigint') {
    return String(value);
  }
  return JSON.stringify(value);
}
