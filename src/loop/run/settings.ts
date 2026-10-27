import { ValueError } from '../value-error.ts';

/** Copy and validate environment overrides without retaining mutable caller state. */
export function freezeEnv(env: null | Readonly<Record<string, string>> | undefined): null | Readonly<Record<string, string>> {
  if (env === null || env === undefined) {
    return null;
  }
  for (const [key, value] of Object.entries(env)) {
    if (key === '' || key.includes('=') || key.includes('\0')) {
      throw new ValueError("environment keys must be nonempty strings without '=' or NUL");
    }
    if (typeof value !== 'string' || value.includes('\0')) {
      throw new ValueError('environment values must be strings without NUL');
    }
  }
  return Object.freeze({ ...env });
}

/** Reject process timeouts that cannot bound execution. */
export function validateTimeout(timeout: null | number | undefined): void {
  if (timeout !== null && timeout !== undefined && (!Number.isFinite(timeout) || timeout <= 0)) {
    throw new ValueError('timeoutSec must be a finite positive number');
  }
}
