import { Cancelled, type CancelSignal } from '../../../src/loop/index.ts';

const SIGNALS: readonly CancelSignal[] = ['SIGINT', 'SIGTERM'];
const EXIT_CODES: Readonly<Record<CancelSignal, number>> = { SIGINT: 130, SIGTERM: 143 };

export interface SignalSource {
  off: (signal: CancelSignal, listener: () => void) => unknown;
  on: (signal: CancelSignal, listener: () => void) => unknown;
}

export interface StopRequestOptions {
  /** Ends the process at once; defaults to `process.exit`. */
  exit?: (code: number) => void;
  signals?: SignalSource;
}

/**
 * pr_babysit's own signal handling, alive for the whole run: the first SIGINT or SIGTERM asks for a graceful stop,
 * and a second ends the process at once. Loop's process supervisor owns the child process groups and kills the
 * remaining ones on that second signal; this listener is the backstop that exits when no group was being supervised.
 */
export class StopRequest {
  get requested(): boolean {
    return this.#signal !== undefined;
  }

  /** The first signal received, if any. */
  get signal(): CancelSignal | undefined {
    return this.#signal;
  }

  readonly #exit: (code: number) => void;
  readonly #listeners = new Map<CancelSignal, () => void>();
  #received = 0;
  #signal: CancelSignal | undefined;
  readonly #signals: SignalSource;
  readonly #waiters = new Set<() => void>();

  constructor(options: StopRequestOptions = {}) {
    this.#exit = options.exit ?? ((code) => {
      process.exit(code);
    });
    this.#signals = options.signals ?? {
      off: (signal, listener) => process.off(signal, listener),
      on: (signal, listener) => process.on(signal, listener),
    };
    for (const signal of SIGNALS) {
      const listener = (): void => {
        this.#receive(signal);
      };
      this.#listeners.set(signal, listener);
      this.#signals.on(signal, listener);
    }
  }

  /** Remove the listeners; the request state stays readable. */
  dispose(): void {
    for (const [signal, listener] of this.#listeners) this.#signals.off(signal, listener);
    this.#listeners.clear();
  }

  /** Resolves after `seconds`, or at once when a stop is requested. */
  sleep(seconds: number): Promise<void> {
    if (this.requested) return Promise.resolve();
    return new Promise((resolve) => {
      const wake = (): void => {
        clearTimeout(timer);
        this.#waiters.delete(wake);
        resolve();
      };
      const timer = setTimeout(wake, seconds * 1000);
      this.#waiters.add(wake);
    });
  }

  /** Raise `Cancelled` for the first signal received, so the run records `interrupted` and exits 130 or 143. */
  throwIfRequested(): void {
    if (this.#signal !== undefined) throw new Cancelled(this.#signal);
  }

  #receive(signal: CancelSignal): void {
    this.#received += 1;
    if (this.#received === 1) {
      this.#signal = signal;
      for (const wake of [...this.#waiters]) wake();
      return;
    }
    // Deferred so the supervisor's force listener, registered later for the same signal, kills the groups first.
    setImmediate(() => {
      this.#exit(EXIT_CODES[signal]);
    });
  }
}
