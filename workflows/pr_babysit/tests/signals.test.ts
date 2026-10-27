import { describe, expect, test } from 'bun:test';

import type { CancelSignal } from '../../../src/loop/index.ts';

import { Cancelled } from '../../../src/loop/index.ts';
import { type SignalSource, StopRequest } from '../infrastructure/signals.ts';

class FakeSignals implements SignalSource {
  readonly #listeners = new Map<CancelSignal, Set<() => void>>();

  count(signal: CancelSignal): number {
    return this.#listeners.get(signal)?.size ?? 0;
  }

  emit(signal: CancelSignal): void {
    for (const listener of [...(this.#listeners.get(signal) ?? [])]) listener();
  }

  off(signal: CancelSignal, listener: () => void): void {
    this.#listeners.get(signal)?.delete(listener);
  }

  on(signal: CancelSignal, listener: () => void): void {
    this.#listeners.set(signal, (this.#listeners.get(signal) ?? new Set()).add(listener));
  }
}

function setup(): { exits: number[]; request: StopRequest; signals: FakeSignals } {
  const signals = new FakeSignals();
  const exits: number[] = [];
  const request = new StopRequest({
    exit: (code) => exits.push(code),
    signals,
  });
  return { exits, request, signals };
}

describe('the first signal', () => {
  test('requests a graceful stop and wakes a sleeping loop at once', async () => {
    const { request, signals } = setup();
    expect(request.requested).toBe(false);

    const started = performance.now();
    const sleeping = request.sleep(30);
    signals.emit('SIGINT');
    await sleeping;

    expect(performance.now() - started).toBeLessThan(5000);
    expect(request.requested).toBe(true);
    expect(request.signal).toBe('SIGINT');
  });

  test('is raised as Cancelled so the run exits 130 for SIGINT and 143 for SIGTERM', () => {
    const interrupted = setup();
    interrupted.signals.emit('SIGINT');
    const terminated = setup();
    terminated.signals.emit('SIGTERM');

    expect(() => {
      interrupted.request.throwIfRequested();
    }).toThrow(Cancelled);
    expect(captured(interrupted.request).exitCode).toBe(130);
    expect(captured(terminated.request).exitCode).toBe(143);
  });

  test('does not stop anything before it arrives', async () => {
    const { request } = setup();

    await request.sleep(0.01);

    expect(() => {
      request.throwIfRequested();
    }).not.toThrow();
  });
});

describe('the second signal', () => {
  test('exits the process immediately with the signal exit code', async () => {
    const { exits, signals } = setup();

    signals.emit('SIGINT');
    expect(exits).toEqual([]);
    signals.emit('SIGTERM');
    await new Promise((resolve) => setImmediate(resolve));

    expect(exits).toEqual([143]);
  });

  test('keeps the first signal as the reason', () => {
    const { request, signals } = setup();

    signals.emit('SIGTERM');
    signals.emit('SIGINT');

    expect(request.signal).toBe('SIGTERM');
  });
});

describe('listeners', () => {
  test('exist from construction until disposal', () => {
    const { request, signals } = setup();
    expect([signals.count('SIGINT'), signals.count('SIGTERM')]).toEqual([1, 1]);

    request.dispose();

    expect([signals.count('SIGINT'), signals.count('SIGTERM')]).toEqual([0, 0]);
  });
});

function captured(request: StopRequest): Cancelled {
  try {
    request.throwIfRequested();
  } catch (error) {
    if (error instanceof Cancelled) return error;
    throw error;
  }
  throw new Error('no stop was requested');
}
