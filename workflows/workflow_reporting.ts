// A fake e-commerce delivery that demonstrates optional phase reporting.
// Run from the harness root through a user alias: `bun workflows/workflow_reporting.ts [--visualize]`.
import { Command, CommanderError } from 'commander';

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

/** Each member replaces the shipped implementation of the same name. */
export interface WorkflowReportingDependencies {
  /** Monotonic milliseconds for phase durations and the visualization. */
  clock?: () => number;
  /** Wall-clock time stamped on every console record. */
  now?: () => Date;
  /** Builds the observer that exports phase spans to an OTLP collector at `endpoint`. */
  otlpObserver?: (endpoint: string) => Promise<ShutdownObserver> | Promise<SpanObserver> | SpanObserver;
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

/** Runs a fake delivery without agents, Git, or GitHub calls; resolves 0 on success, 1 on failure, or 2 on bad input. */
export async function main(argv: readonly string[], dependencies: WorkflowReportingDependencies = {}): Promise<number> {
  const {
    clock = () => performance.now(),
    now = () => new Date(),
    otlpObserver = defaultOtlpObserver,
    stderr = process.stderr,
    stdout = process.stdout,
  } = dependencies;
  const options = parseCommandLine(argv, stdout, stderr);
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
    .console(consoleLogger(stderr, now));
  if (options.visualize) {
    builder.visualize({ clock, stream: stderr });
  }
  const observer = options.otelEndpoint === undefined ? undefined : await otlpObserver(options.otelEndpoint);
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

function parseCommandLine(argv: readonly string[], stdout: OutputStream, stderr: OutputStream): number | Options {
  const program = new Command('workflow_reporting')
    .description(DESCRIPTION)
    .option('--visualize', 'show the live phase table', false)
    .option('--fail-verification', 'make the verification phase fail', false)
    .option('--otel-endpoint <endpoint>', 'export phase spans to this OTLP endpoint')
    .configureOutput({
      writeErr: (text) => stderr.write(text),
      writeOut: (text) => stdout.write(text),
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

