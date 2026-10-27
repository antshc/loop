import type { SpanObserver } from '../observe/spans.ts';
import type { VisualizeOptions } from './adapters/terminal.ts';
import type { WorkflowLogger } from './logger.ts';
import type { PhaseAttributes, PhaseBody, PhasesOptions, Reporter } from './phases.ts';

import { ValueError } from '../value-error.ts';
import { SILENT_LOGGER } from './logger.ts';
import { Kind, Phase, Phases } from './phases.ts';

/** Options for a declared phase: kind and owner are optional, but must match the declaration when given. */
export interface DeclaredPhaseOptions {
  attributes?: PhaseAttributes;
  kind?: Kind;
  owner?: string;
}

/** Declares the presentation and ownership of a named phase. */
export interface PhaseDefinition {
  readonly kind: Kind;
  readonly label: string;
  readonly name: string;
  readonly owner: string;
  readonly visible: boolean;
}

export interface WorkflowInit {
  readonly console?: boolean;
  readonly definitions: readonly PhaseDefinition[];
  readonly logger?: WorkflowLogger;
  readonly name: string;
  readonly observer?: null | SpanObserver;
  readonly visualize?: null | VisualizeOptions;
}

/** An immutable phase definition that creates independent runs. */
export class Workflow {
  readonly console: boolean;
  readonly definitions: readonly PhaseDefinition[];
  readonly logger: WorkflowLogger;
  readonly name: string;
  readonly observer: null | SpanObserver;
  readonly visualize: null | VisualizeOptions;

  constructor(init: WorkflowInit) {
    this.name = init.name;
    this.definitions = Object.freeze([...init.definitions]);
    this.console = init.console ?? false;
    this.visualize = init.visualize ? Object.freeze({ ...init.visualize }) : null;
    this.observer = init.observer ?? null;
    this.logger = init.logger ?? SILENT_LOGGER;
    Object.freeze(this);
  }

  /** Runs the body as one run whose business outcome is controlled by its caller. */
  run<T>(body: PhaseBody<T, WorkflowRun>): Promise<T>;
  run<T>(attributes: PhaseAttributes, body: PhaseBody<T, WorkflowRun>): Promise<T>;
  async run<T>(first: PhaseAttributes | PhaseBody<T, WorkflowRun>, second?: PhaseBody<T, WorkflowRun>): Promise<T> {
    const [attributes, body] = typeof first === 'function' ? [{}, first] : [first, second];
    if (body === undefined) {
      throw new TypeError('Workflow run needs a body');
    }
    const options = await this.#options();
    return new WorkflowRun(this, options).execute(attributes, body);
  }

  // Reporters load only when configured, so a bare run pulls in no reporting or telemetry code.
  async #options(): Promise<PhasesOptions> {
    const reporters: Reporter[] = [];
    if (this.console) {
      const { ConsoleReporter } = await import('./adapters/console.ts');
      reporters.push(new ConsoleReporter(this.logger));
    }
    if (this.visualize !== null) {
      const { TerminalReporter } = await import('./adapters/terminal.ts');
      reporters.push(new TerminalReporter(this.name, this.definitions, this.visualize));
    }
    if (this.observer === null) {
      return { clock: this.visualize?.clock, logger: this.logger, reporters };
    }
    const { SpanReporter } = await import('./adapters/telemetry.ts');
    return {
      clock: this.visualize?.clock,
      logger: this.logger,
      reporters,
      telemetry: new SpanReporter(this.observer, this.logger),
    };
  }
}

/** Executes declared phase scopes without scheduling their bodies. */
export class WorkflowRun extends Phases {
  /** The root result, including duration and business attributes. */
  get outcome(): Phase {
    if (this.#root === null) {
      throw new Error('Workflow run has not started');
    }
    return this.#root;
  }
  #active = false;
  readonly #definitions: ReadonlyMap<string, PhaseDefinition>;
  #root: null | Phase = null;

  readonly #workflow: Workflow;

  constructor(workflow: Workflow, options: PhasesOptions = {}) {
    super(options);
    this.#workflow = workflow;
    this.#definitions = new Map(workflow.definitions.map((definition) => [definition.name, definition]));
  }

  /** Runs the body inside the root phase; `Workflow.run` is the way to start one. */
  async execute<T>(attributes: PhaseAttributes, body: PhaseBody<T, WorkflowRun>): Promise<T> {
    const spec = { attributes, kind: Kind.CODE, label: this.#workflow.name, name: this.#workflow.name, owner: 'code' };
    try {
      return await this.open(spec, (root) => {
        this.#root = root;
        this.#active = true;
        return body(this);
      });
    } finally {
      this.#active = false;
    }
  }

  /** Fails the run when the caller decides recovery was unsuccessful. */
  fail(reason: string): void {
    this.outcome.fail(reason);
  }

  /** Opens a declared phase with fresh occurrence and attempt identity. */
  override phase<T>(name: string, body: PhaseBody<T>): Promise<T>;
  override phase<T>(name: string, options: DeclaredPhaseOptions, body: PhaseBody<T>): Promise<T>;
  override async phase<T>(
    name: string,
    second: DeclaredPhaseOptions | PhaseBody<T>,
    third?: PhaseBody<T>,
  ): Promise<T> {
    const [options, body] = typeof second === 'function' ? [{}, second] : [second, third];
    if (body === undefined) {
      throw new TypeError('A phase needs a body');
    }
    const definition = this.#declared(name, options);
    return this.open(
      {
        attributes: options.attributes ?? {},
        kind: definition.kind,
        label: definition.label,
        name: definition.name,
        owner: definition.owner,
        visible: definition.visible,
      },
      body,
    );
  }

  /** Records final business results such as verdict or PR readiness. */
  set(attributes: PhaseAttributes): void {
    this.outcome.set(attributes);
  }

  /** Records a declared phase as skipped without running its body. */
  skip(name: string, reason: string, attributes: PhaseAttributes = {}): Promise<void> {
    return this.phase(name, { attributes }, (phase) => {
      phase.skip(reason);
    });
  }

  #declared(name: string, options: DeclaredPhaseOptions): PhaseDefinition {
    if (!this.#active) {
      throw new Error('Workflow run is closed');
    }
    const definition = this.#definitions.get(name);
    if (definition === undefined) {
      throw new ValueError(`Unknown phase: ${name}`);
    }
    if (options.kind !== undefined && options.kind !== definition.kind) {
      throw new ValueError(`Phase '${name}' kind differs from its declaration`);
    }
    if (options.owner !== undefined && options.owner !== definition.owner) {
      throw new ValueError(`Phase '${name}' owner differs from its declaration`);
    }
    return definition;
  }
}

