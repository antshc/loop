import { AsyncLocalStorage } from 'node:async_hooks';

import type { TracingObserver } from '../../telemetry.ts';

import {
  type AgentRunRecord,
  configureLogging,
  type PhaseSpan,
  type SpanAttributeValue,
  type WorktreeEvent,
} from '../../../../src/loop/index.ts';

export interface RecordedSpan {
  readonly attributes: Readonly<Record<string, SpanAttributeValue>>;
  readonly name: string;
  readonly parent: null | RecordedSpan;
  readonly role: string;
}

/** Stands in for the OTLP observer: keeps what the library reports and the Workflow spans, and notes its shutdown. */
export class RecordingObserver implements TracingObserver {
  readonly runs: AgentRunRecord[] = [];
  shutdowns = 0;
  readonly spans: RecordedSpan[] = [];
  readonly worktrees: string[] = [];
  readonly #current = new AsyncLocalStorage<RecordedSpan>();

  constructor(readonly endpoint: string) {}

  agentRun(record: AgentRunRecord): void {
    this.runs.push(record);
  }

  shutdown(): Promise<void> {
    this.shutdowns += 1;
    return Promise.resolve();
  }

  span(name: string, attributes: Readonly<Record<string, SpanAttributeValue>>, role: string): PhaseSpan {
    const record: RecordedSpan = { attributes, name, parent: this.#current.getStore() ?? null, role };
    this.spans.push(record);
    return {
      end: () => undefined,
      run: (body) => this.#current.run(record, body),
    };
  }

  worktree(event: WorktreeEvent): void {
    this.worktrees.push(event.point);
  }
}

/** Mutes the process-wide log sink a Workflow's `configureLogging` installed. */
export function silenceLogging(): void {
  configureLogging({ level: 'critical', logFile: null, logger: '', stream: { write: () => undefined } });
}
