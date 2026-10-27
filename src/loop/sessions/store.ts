import type { NativeHandle, SessionName } from './values.ts';

/** The injectable seam saved session handles live behind. */
export interface SessionStore {
  get: (name: SessionName, cli: string) => Promise<NativeHandle | null>;
  save: (name: SessionName, handle: NativeHandle) => Promise<void>;
}

/** Process-local session store; sessions are not persisted across runs of the process. */
export class MemorySessionStore implements SessionStore {
  readonly #sessions = new Map<string, NativeHandle>();

  get(name: SessionName, cli: string): Promise<NativeHandle | null> {
    return Promise.resolve(this.#sessions.get(key(name, cli)) ?? null);
  }

  save(name: SessionName, handle: NativeHandle): Promise<void> {
    this.#sessions.set(key(name, handle.cli), handle);
    return Promise.resolve();
  }
}

function key(name: SessionName, cli: string): string {
  return JSON.stringify([name.value, cli]);
}
