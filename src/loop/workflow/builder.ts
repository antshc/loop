import type { VisualizeOptions } from './adapters/terminal.ts';
import type { WorkflowLogger } from './logger.ts';
import type { Kind } from './phases.ts';
import type { PhaseDefinition } from './workflow.ts';

import { isSpanObserver, type SpanObserver } from '../observe/spans.ts';
import { ValueError } from '../value-error.ts';
import { Workflow } from './workflow.ts';

/** Builds an optional reporting definition for ordinary Workflow scripts. */
export class WorkflowBuilder {
  #console = false;
  #definitions = new Map<string, PhaseDefinition>();
  #logger: undefined | WorkflowLogger;
  readonly #name: string;
  #observer: null | SpanObserver = null;
  #visualize: null | VisualizeOptions = null;

  constructor(name: string) {
    if (name.trim() === '') {
      throw new ValueError('Workflow name must not be empty');
    }
    this.#name = name;
  }

  /** Snapshots declarations without executing phases or configuring logging. */
  build(): Workflow {
    return new Workflow({
      console: this.#console,
      definitions: [...this.#definitions.values()],
      logger: this.#logger,
      name: this.#name,
      observer: this.#observer,
      visualize: this.#visualize,
    });
  }

  /** Enables high-level records through the host's logger; without one they are dropped. */
  console(logger?: WorkflowLogger): this {
    this.#console = true;
    this.#logger = logger ?? this.#logger;
    return this;
  }

  /** Shares an existing span-capable observer with phase reporting. */
  otel(observer: unknown): this {
    if (!isSpanObserver(observer)) {
      throw new TypeError('Workflow telemetry needs an observer with span()');
    }
    this.#observer = observer;
    return this;
  }

  /** Declares a unique phase, optionally hidden from console and terminal. */
  phase(name: string, label: string, kind: Kind, owner: string, options: { visible?: boolean } = {}): this {
    if ([name, label, kind, owner].some((value) => value.trim() === '')) {
      throw new ValueError('Phase name, label, kind, and owner must not be empty');
    }
    if (this.#definitions.has(name)) {
      throw new ValueError(`Duplicate phase: ${name}`);
    }
    this.#definitions.set(name, { kind, label, name, owner, visible: options.visible ?? true });
    return this;
  }

  /** Enables the live table on stderr or the given stream: redrawn in place on a terminal, printed once when redirected. */
  visualize(options: VisualizeOptions = {}): this {
    this.#visualize = options;
    return this;
  }
}
