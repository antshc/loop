export type CancelSignal = 'SIGINT' | 'SIGTERM';

export const CANCEL_SIGNALS: readonly CancelSignal[] = ['SIGINT', 'SIGTERM'];

const EXIT_CODES: Readonly<Record<CancelSignal, number>> = { SIGINT: 130, SIGTERM: 143 };

/** Library-internal: raised when SIGINT or SIGTERM interrupts a running child. */
export class Cancelled extends Error {
  readonly signal: CancelSignal;

  get exitCode(): number {
    return exitCodeFor(this.signal);
  }

  constructor(signal: CancelSignal) {
    super(`cancelled by ${signal}`);
    this.name = 'Cancelled';
    this.signal = signal;
  }
}

export function exitCodeFor(signal: CancelSignal): number {
  return EXIT_CODES[signal];
}
