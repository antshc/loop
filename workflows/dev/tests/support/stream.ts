import type { OutputStream } from '../../../../src/loop/index.ts';

/** Collects what a Workflow writes, as a terminal (`isTTY`) or as a redirected stream. */
export class Stream implements OutputStream {
  readonly writes: string[] = [];

  get text(): string {
    return this.writes.join('');
  }

  constructor(readonly isTTY = false) {}

  write(text: string): void {
    this.writes.push(text);
  }
}
