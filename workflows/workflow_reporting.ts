// A fake e-commerce delivery that demonstrates optional phase reporting.
// Run from the harness root through a user alias: `bun workflows/workflow_reporting.ts [--visualize]`.
import { Command, CommanderError } from 'commander';
import { Container, inject, injectable } from 'inversify';
import 'reflect-metadata';

import {
  enforceRuntime,
  Kind,
  type OtlpObserver,
  otlpObserver,
  type OutputStream,
  Role,
  runEntrypoint,
  type SpanObserver,
  WorkflowBuilder,
  type WorkflowLogger,
} from '../src/loop/index.ts';

type ShutdownObserver = SpanObserver & { shutdown: () => Promise<void> };

const DESCRIPTION = 'A fake e-commerce delivery that demonstrates optional phase reporting.';

/** Monotonic milliseconds for phase durations and the visualization. */
type Clock = () => number;
/** Builds the observer that exports phase spans to an OTLP collector at `endpoint`. */
type OtlpObserverFactory = (endpoint: string) => Promise<ShutdownObserver> | Promise<SpanObserver> | SpanObserver;
/** Parses the command line into options, or into the exit code to return for help or bad input. */
interface ReportingCommandLine {
  parse: (argv: readonly string[]) => number | Options;
}

/** Wall-clock time stamped on every console record. */
type WallClock = () => Date;

/** The seams of this Workflow; the composition root is the only place that binds implementations. */
export const TOKENS = {
  clock: Symbol.for('workflow_reporting.Clock'),
  commandLine: Symbol.for('workflow_reporting.ReportingCommandLine'),
  now: Symbol.for('workflow_reporting.WallClock'),
  otlpObserver: Symbol.for('workflow_reporting.OtlpObserverFactory'),
  stderr: Symbol.for('workflow_reporting.ErrorStream'),
  stdout: Symbol.for('workflow_reporting.OutputStream'),
  workflow: Symbol.for('workflow_reporting.ReportingWorkflow'),
} as const;

/** Each member rebinds the token of the shipped implementation of the same name. */
export interface WorkflowReportingDependencies {
  clock?: Clock;
  now?: WallClock;
  otlpObserver?: OtlpObserverFactory;
  /** Receives console records, the visualization, and usage for bad input. */
  stderr?: OutputStream;
  /** Receives requested help. */
  stdout?: OutputStream;
}

interface Options {
  failVerification: boolean;
  otelEndpoint?: string;
  visualize: boolean;
}

@injectable()
class CommanderReportingCommandLine implements ReportingCommandLine {
  constructor(
    @inject(TOKENS.stdout) private readonly stdout: OutputStream,
    @inject(TOKENS.stderr) private readonly stderr: OutputStream,
  ) {}

  parse(argv: readonly string[]): number | Options {
    const program = new Command('workflow_reporting')
      .description(DESCRIPTION)
      .option('--visualize', 'show the live phase table', false)
      .option('--fail-verification', 'make the verification phase fail', false)
      .option('--otel-endpoint <endpoint>', 'export phase spans to this OTLP endpoint')
      .configureOutput({
        writeErr: (text) => this.stderr.write(text),
        writeOut: (text) => this.stdout.write(text),
      })
      .showHelpAfterError()
      .exitOverride();
    try {
      program.parse([...argv], { from: 'user' });
    } catch (error) {
      if (error instanceof CommanderError) {
        return error.exitCode === 0 ? 0 : 2;
      }
      throw error;
    }
    return program.opts<Options>();
  }
}

@injectable()
class ReportingWorkflow {
  constructor(
    @inject(TOKENS.commandLine) private readonly commandLine: ReportingCommandLine,
    @inject(TOKENS.clock) private readonly clock: Clock,
    @inject(TOKENS.now) private readonly now: WallClock,
    @inject(TOKENS.otlpObserver) private readonly otlpObserver: OtlpObserverFactory,
    @inject(TOKENS.stderr) private readonly stderr: OutputStream,
  ) {}

  async run(argv: readonly string[]): Promise<number> {
    const options = this.commandLine.parse(argv);
    if (typeof options === 'number') {
      return options;
    }

    const builder = new WorkflowBuilder('eShop development')
      .phase('prepare', 'Prepare repository', Kind.CODE, Role.CODE)
      .phase('implement', 'Implement ticket', Kind.AGENT, Role.DEVELOPER)
      .phase('publish', 'Publish draft PR', Kind.CODE, Role.CODE)
      .phase('verify', 'Verify application', Kind.AGENT, 'verifier')
      .phase('chore', 'Refine changes', Kind.AGENT, 'chore')
      .phase('review', 'Review pull request', Kind.AGENT, Role.REVIEWER)
      .phase('finalize', 'Check readiness', Kind.CODE, Role.CODE)
      .console(consoleLogger(this.stderr, this.now));
    if (options.visualize) {
      builder.visualize({ clock: this.clock, stream: this.stderr });
    }
    const observer = options.otelEndpoint === undefined ? undefined : await this.otlpObserver(options.otelEndpoint);
    if (observer !== undefined) {
      builder.otel(observer);
    }

    try {
      return await deliver(builder, options);
    } finally {
      if (observer !== undefined && isShutdownObserver(observer)) {
        await observer.shutdown();
      }
    }
  }
}

/** The composition root: binds the shipped implementations, then rebinds each token that has an override. */
export function compose(overrides: WorkflowReportingDependencies = {}): Container {
  const container = new Container();
  container.bind<Clock>(TOKENS.clock).toConstantValue(() => performance.now());
  container.bind<ReportingCommandLine>(TOKENS.commandLine).to(CommanderReportingCommandLine).inSingletonScope();
  container.bind<WallClock>(TOKENS.now).toConstantValue(() => new Date());
  container.bind<OtlpObserverFactory>(TOKENS.otlpObserver).toConstantValue(defaultOtlpObserver);
  container.bind<OutputStream>(TOKENS.stderr).toConstantValue(process.stderr);
  container.bind<OutputStream>(TOKENS.stdout).toConstantValue(process.stdout);
  container.bind<ReportingWorkflow>(TOKENS.workflow).to(ReportingWorkflow).inSingletonScope();

  for (const [name, override] of Object.entries(overrides) as [keyof WorkflowReportingDependencies, unknown][]) {
    if (override !== undefined) {
      container.rebind(TOKENS[name]).toConstantValue(override);
    }
  }
  return container;
}

/** Runs a fake delivery without agents, Git, or GitHub calls; resolves 0 on success, 1 on failure, or 2 on bad input. */
export async function main(argv: readonly string[], dependencies: WorkflowReportingDependencies = {}): Promise<number> {
  return await compose(dependencies).get<ReportingWorkflow>(TOKENS.workflow).run(argv);
}

/** Console records as `<time> <LEVEL> loop.workflow: <message>`, one per line. */
function consoleLogger(stream: OutputStream, now: () => Date): WorkflowLogger {
  const write = (level: string, message: string): void => {
    stream.write(`${timestamp(now())} ${level} loop.workflow: ${message}\n`);
  };
  return {
    info: (message) => {
      write('INFO', message);
    },
    warn: (message) => {
      write('WARNING', message);
    },
  };
}

function defaultOtlpObserver(endpoint: string): Promise<OtlpObserver> {
  return otlpObserver({ endpoint, serviceName: 'workflow_reporting' });
}

function deliver(builder: WorkflowBuilder, options: Options): Promise<number> {
  return builder.build().run({ spec: 42 }, async (run) => {
    await run.phase('prepare', () => undefined);
    const basket: string[] = [];

    for (const [ticket, product] of [
      [43, 'coffee'],
      [44, 'tea'],
    ] as const) {
      let recovered = false;
      for (let attempt = 1; attempt <= 2 && !recovered; attempt++) {
        recovered = await run.phase('implement', { attributes: { attempt, ticket } }, (phase) => {
          // One deliberate failure demonstrates a recovered retry.
          const changed = !(ticket === 43 && attempt === 1);
          if (phase.gate('product added', changed, 'No changes produced')) {
            basket.push(product);
          }
          return phase.status === 'running';
        });
      }
      if (!recovered) {
        run.fail(`Ticket #${String(ticket)} exhausted its retries`);
        return 1;
      }
    }

    await run.phase('publish', (phase) => {
      phase.set({ pull_request: 'fake://eshop/pull/57' });
    });

    const verified = await run.phase('verify', (phase) => {
      const passed = basket.join() === 'coffee,tea' && !options.failVerification;
      phase.set({ verdict: passed ? 'pass' : 'fail' });
      phase.gate('basket contains the expected products', passed, 'Basket verification failed');
      return passed;
    });

    await run.skip('chore', 'No cleanup configured');
    if (!verified) {
      await run.skip('review', 'Verification failed');
      await run.skip('finalize', 'PR remains a draft');
      run.set({ ready: false, verdict: 'fail' });
      run.fail('Application verification failed; PR remains a draft');
      return 1;
    }

    // A real workflow closes its implementation worktree before review.
    await run.phase('review', (phase) => {
      phase.set({ blocking_findings: 0 });
    });
    await run.phase('finalize', (phase) => {
      phase.set({ ready: true });
    });
    run.set({ ready: true, verdict: 'pass' });
    return 0;
  });
}

function isShutdownObserver(observer: SpanObserver): observer is ShutdownObserver {
  return typeof (observer as Partial<ShutdownObserver>).shutdown === 'function';
}

/** Python-logging style: `2026-01-02 03:04:05,006` in local time. */
function timestamp(date: Date): string {
  const two = (value: number): string => String(value).padStart(2, '0');
  const day = `${String(date.getFullYear())}-${two(date.getMonth() + 1)}-${two(date.getDate())}`;
  const time = `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
  return `${day} ${time},${String(date.getMilliseconds()).padStart(3, '0')}`;
}

if (import.meta.main) {
  enforceRuntime();
  await runEntrypoint(async () => {
    process.exitCode = await main(process.argv.slice(2));
  });
}

