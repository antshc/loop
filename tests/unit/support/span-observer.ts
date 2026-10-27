import { AsyncLocalStorage } from 'node:async_hooks';

import type { PhaseSpan, PhaseSpanEnd, SpanAttributeValue, SpanObserver } from '../../../src/loop/index.ts';

export interface RecordedSpan {
  readonly attributes: Readonly<Record<string, SpanAttributeValue>>;
  end: null | PhaseSpanEnd;
  readonly name: string;
  readonly parent: null | RecordedSpan;
  readonly role: string;
}

/** A span-capable observer that records spans and keeps the current one across awaits, as a real exporter does. */
export class SpanRecorder implements SpanObserver {
  readonly spans: RecordedSpan[] = [];
  /** The span an Agent Run or Worktree fact would nest under right now. */
  get current(): RecordedSpan | undefined {
    return this.#current.getStore();
  }

  readonly #current = new AsyncLocalStorage<RecordedSpan>();

  span(name: string, attributes: Readonly<Record<string, SpanAttributeValue>>, role: string): PhaseSpan {
    const record: RecordedSpan = { attributes, end: null, name, parent: this.current ?? null, role };
    this.spans.push(record);
    return {
      end: (final) => {
        record.end = final;
      },
      run: (body) => this.#current.run(record, body),
    };
  }
}
