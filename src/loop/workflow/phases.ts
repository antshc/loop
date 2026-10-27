import { AsyncLocalStorage } from 'node:async_hooks';

import type { PhaseSpan } from '../observe/spans.ts';
import type { WorkflowLogger } from './logger.ts';

import { Cancelled } from '../run/cancelled.ts';
import { reportingFailed, SILENT_LOGGER } from './logger.ts';

/** Distinguishes engineer, deterministic code, and agent phases. */
export const Kind = {
  AGENT: 'agent',
  CODE: 'code',
  ENGINEER: 'engineer',
} as const;
/** One claim a phase made about its result. */
export interface Gate {
  check: string;
  note: string;
  ok: boolean;
}

export type Kind = (typeof Kind)[keyof typeof Kind];

export type PhaseAttributes = Readonly<Record<string, unknown>>;

export type PhaseBody<T, P = Phase> = (phase: P) => Promise<T> | T;

export interface PhaseInit {
  attributes: PhaseAttributes;
  kind: Kind;
  label: string;
  name: string;
  owner: string;
  parentId: null | string;
  startedAt: number;
  visible: boolean;
}

export interface PhaseOptions {
  attributes?: PhaseAttributes;
  kind: Kind;
  owner: string;
}

export interface PhasesOptions {
  /** Monotonic milliseconds. */
  clock?: () => number;
  logger?: WorkflowLogger;
  reporters?: readonly Reporter[];
  telemetry?: null | PhaseTelemetry;
}

export interface PhaseSpec {
  attributes: PhaseAttributes;
  kind: Kind;
  label: string;
  name: string;
  owner: string;
  visible?: boolean;
}

export type PhaseStatus = 'cancelled' | 'completed' | 'failed' | 'running' | 'skipped';

/** Keeps an observer's span current through a phase and exports its final facts. */
export interface PhaseTelemetry {
  close: (span: null | PhaseSpan, phase: Phase) => void;
  open: (phase: Phase) => null | PhaseSpan;
}

/** Observes phase transitions; a failure never changes execution. */
export interface Reporter {
  emit: (point: ReportPoint, phase: Phase) => void;
}

export type ReportPoint = 'finished' | 'started';

/** Records one phase occurrence and its explicit business outcome. */
export class Phase {
  readonly attributes: Record<string, unknown>;
  readonly gates: Gate[] = [];
  readonly id: string = crypto.randomUUID().replaceAll('-', '');
  readonly kind: Kind;
  readonly label: string;
  readonly name: string;
  readonly owner: string;
  readonly parentId: null | string;
  reason: null | string = null;
  seconds = 0;
  /** The clock reading when the phase began, for live durations. */
  readonly startedAt: number;
  status: PhaseStatus = 'running';
  readonly visible: boolean;

  constructor(init: PhaseInit) {
    this.name = init.name;
    this.kind = init.kind;
    this.owner = init.owner;
    this.attributes = { ...init.attributes };
    this.label = init.label;
    this.visible = init.visible;
    this.parentId = init.parentId;
    this.startedAt = init.startedAt;
  }

  /** Marks failure without allowing later success to erase it. */
  fail(reason: string): void {
    if (this.status !== 'failed') {
      this.reason = reason;
    }
    this.status = 'failed';
  }

  /** Records a claim and fails the phase when it does not hold. */
  gate(check: string, ok: boolean, note = ''): boolean {
    this.gates.push({ check, note, ok });
    if (!ok) {
      this.fail(note || check);
    }
    return ok;
  }

  /** Adds business results without interpreting them as success. */
  set(attributes: PhaseAttributes): void {
    Object.assign(this.attributes, attributes);
  }

  /** Marks an unnecessary phase skipped without clearing failure. */
  skip(reason: string): void {
    if (this.status === 'running') {
      this.status = 'skipped';
      this.reason = reason;
    }
  }

  /** Completes a running phase without overriding another outcome. */
  succeed(): void {
    if (this.status === 'running') {
      this.status = 'completed';
    }
  }
}

/** Tracks phases directly without a builder or output dependencies. */
export class Phases {
  /** The phase occurrences in start order. */
  get executions(): readonly Phase[] {
    return [...this.#executions];
  }
  protected readonly clock: () => number;
  protected readonly logger: WorkflowLogger;
  readonly #executions: Phase[] = [];
  readonly #parent = new AsyncLocalStorage<Phase>();
  readonly #reporters: readonly Reporter[];

  readonly #telemetry: null | PhaseTelemetry;

  constructor(options: PhasesOptions = {}) {
    this.clock = options.clock ?? (() => performance.now());
    this.logger = options.logger ?? SILENT_LOGGER;
    this.#reporters = options.reporters ?? [];
    this.#telemetry = options.telemetry ?? null;
  }

  /** Runs the body as a phase: `completed` when it succeeds, `cancelled` on `Cancelled`, else `failed`; errors are rethrown unchanged. */
  phase<T>(name: string, options: PhaseOptions, body: PhaseBody<T>): Promise<T> {
    return this.open(
      { attributes: options.attributes ?? {}, kind: options.kind, label: name, name, owner: options.owner },
      body,
    );
  }

  /** Opens a phase whose parent is the phase enclosing the caller, across awaits. */
  protected async open<T>(spec: PhaseSpec, body: PhaseBody<T>): Promise<T> {
    const parent = this.#parent.getStore();
    const phase = new Phase({
      attributes: { ...parent?.attributes, ...spec.attributes },
      kind: spec.kind,
      label: spec.label,
      name: spec.name,
      owner: spec.owner,
      parentId: parent?.id ?? null,
      startedAt: this.clock(),
      visible: spec.visible ?? true,
    });
    this.#executions.push(phase);
    const span = this.#openSpan(phase);
    this.#notify('started', phase);
    try {
      const scoped = (): Promise<T> => this.#parent.run(phase, async () => body(phase));
      const result = await (span === null ? scoped() : span.run(scoped));
      phase.succeed();
      return result;
    } catch (error) {
      if (error instanceof Cancelled) {
        phase.status = 'cancelled';
        phase.reason = error.name;
      } else {
        phase.fail(error instanceof Error ? error.name : typeof error);
      }
      throw error;
    } finally {
      phase.seconds = (this.clock() - phase.startedAt) / 1000;
      this.#notify('finished', phase);
      this.#closeSpan(span, phase);
    }
  }

  #closeSpan(span: null | PhaseSpan, phase: Phase): void {
    if (this.#telemetry === null) {
      return;
    }
    try {
      this.#telemetry.close(span, phase);
    } catch (error) {
      reportingFailed(this.logger, 'telemetry', error);
    }
  }

  #notify(point: ReportPoint, phase: Phase): void {
    for (const reporter of this.#reporters) {
      try {
        reporter.emit(point, phase);
      } catch (error) {
        // Reporting is observational and must not change execution.
        reportingFailed(this.logger, 'reporting', error);
      }
    }
  }

  #openSpan(phase: Phase): null | PhaseSpan {
    if (this.#telemetry === null) {
      return null;
    }
    try {
      return this.#telemetry.open(phase);
    } catch (error) {
      reportingFailed(this.logger, 'telemetry', error);
      return null;
    }
  }
}
