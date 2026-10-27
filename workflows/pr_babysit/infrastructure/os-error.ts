/** A filesystem or process failure carrying an errno code, the TypeScript counterpart of Python's `OSError`. */
export function isOsError(value: unknown): value is Error {
  return value instanceof Error && 'code' in value && typeof value.code === 'string' && /^E[A-Z0-9]+$/.test(value.code);
}
