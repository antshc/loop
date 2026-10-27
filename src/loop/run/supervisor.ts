import { CANCEL_SIGNALS, type CancelSignal, exitCodeFor } from './cancelled.ts';

export interface ProcessLease {
  /** The signal that cancelled this group, if any. */
  readonly cancelledBy: CancelSignal | undefined;
  release: () => void;
}

export interface SignalSource {
  off: (signal: CancelSignal, listener: () => void) => unknown;
  on: (signal: CancelSignal, listener: () => void) => unknown;
}

export interface SupervisorOptions {
  exit?: (code: number) => void;
  /** Time a group gets after SIGTERM before SIGKILL. */
  graceMs?: number;
  signals?: SignalSource;
}

export const DEFAULT_GRACE_MS = 5000;

const KILL_WAIT_MS = 2000;
const POLL_MS = 10;

class Lease implements ProcessLease {
  cancelledBy: CancelSignal | undefined;
  readonly pgid: number;
  readonly #onRelease: () => void;
  #released = false;

  constructor(pgid: number, onRelease: () => void) {
    this.pgid = pgid;
    this.#onRelease = onRelease;
  }

  cancel(signal: CancelSignal): void {
    this.cancelledBy ??= signal;
  }

  release(): void {
    if (!this.#released) {
      this.#released = true;
      this.#onRelease();
    }
  }
}

/**
 * Owns every child process group Loop spawns. Signal listeners exist only while
 * groups are tracked; after the first signal a force-exit listener stays until exit.
 */
export class ProcessSupervisor {
  readonly graceMs: number;
  #cancelledBy: CancelSignal | undefined;
  readonly #exit: (code: number) => void;
  #firstInstalled = false;
  readonly #firstListeners = new Map<CancelSignal, () => void>();
  readonly #forceListeners = new Map<CancelSignal, () => void>();
  readonly #leases = new Set<Lease>();
  readonly #pending = new Set<number>();
  readonly #signals: SignalSource;
  readonly #terminations = new Map<number, Promise<void>>();

  constructor(options: SupervisorOptions = {}) {
    this.graceMs = options.graceMs ?? DEFAULT_GRACE_MS;
    this.#exit = options.exit ?? ((code) => {
      process.exit(code);
    });
    this.#signals = options.signals ?? processSignals();
    for (const signal of CANCEL_SIGNALS) {
      this.#firstListeners.set(signal, () => {
        this.#onFirstSignal(signal);
      });
      this.#forceListeners.set(signal, () => {
        this.#onForceSignal(signal);
      });
    }
  }

  /** SIGTERM to the group, SIGKILL after the grace period; resolves once the group is gone. Idempotent. */
  terminate(pgid: number): Promise<void> {
    const existing = this.#terminations.get(pgid);
    if (existing) {
      return existing;
    }
    const termination = this.#escalate(pgid).finally(() => {
      this.#pending.delete(pgid);
      this.#terminations.delete(pgid);
    });
    this.#pending.add(pgid);
    this.#terminations.set(pgid, termination);
    return termination;
  }

  /** Resolves once any termination under way for the group has finished. */
  terminating(pgid: number): Promise<void> {
    return this.#terminations.get(pgid) ?? Promise.resolve();
  }

  /** Starts protecting a freshly spawned group (its pgid equals the leader pid). */
  track(pgid: number): ProcessLease {
    const lease = new Lease(pgid, () => {
      this.#leases.delete(lease);
      this.#syncListeners();
    });
    this.#leases.add(lease);
    this.#syncListeners();
    return lease;
  }

  async #escalate(pgid: number): Promise<void> {
    if (!signalGroup(pgid, 'SIGTERM')) {
      return;
    }
    if (await waitForGroupGone(pgid, this.graceMs)) {
      return;
    }
    signalGroup(pgid, 'SIGKILL');
    await waitForGroupGone(pgid, KILL_WAIT_MS);
  }

  #install(listeners: Map<CancelSignal, () => void>): void {
    for (const [signal, listener] of listeners) {
      this.#signals.on(signal, listener);
    }
  }

  #onFirstSignal(signal: CancelSignal): void {
    this.#cancelledBy = signal;
    this.#uninstall(this.#firstListeners);
    this.#firstInstalled = false;
    this.#install(this.#forceListeners);
    for (const lease of this.#leases) {
      lease.cancel(signal);
      void this.terminate(lease.pgid);
    }
  }

  #onForceSignal(signal: CancelSignal): void {
    for (const lease of this.#leases) {
      signalGroup(lease.pgid, 'SIGKILL');
    }
    for (const pgid of this.#pending) {
      signalGroup(pgid, 'SIGKILL');
    }
    this.#exit(exitCodeFor(signal));
  }

  #syncListeners(): void {
    if (this.#cancelledBy !== undefined) {
      return;
    }
    if (this.#leases.size > 0 && !this.#firstInstalled) {
      this.#install(this.#firstListeners);
      this.#firstInstalled = true;
    } else if (this.#leases.size === 0 && this.#firstInstalled) {
      this.#uninstall(this.#firstListeners);
      this.#firstInstalled = false;
    }
  }

  #uninstall(listeners: Map<CancelSignal, () => void>): void {
    for (const [signal, listener] of listeners) {
      this.#signals.off(signal, listener);
    }
  }
}

export function groupAlive(pgid: number): boolean {
  try {
    process.kill(-pgid, 0);
    return true;
  } catch (error) {
    return !(error instanceof Error && 'code' in error && error.code === 'ESRCH');
  }
}

export function signalGroup(pgid: number, signal: 'SIGKILL' | 'SIGTERM'): boolean {
  try {
    process.kill(-pgid, signal);
    return true;
  } catch {
    return false;
  }
}

function processSignals(): SignalSource {
  return {
    off: (signal, listener) => process.off(signal, listener),
    on: (signal, listener) => process.on(signal, listener),
  };
}

async function waitForGroupGone(pgid: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (groupAlive(pgid)) {
    if (Date.now() >= deadline) {
      return false;
    }
    await Bun.sleep(POLL_MS);
  }
  return true;
}
