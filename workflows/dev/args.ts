/** The dev command line: every flag, default and choice, generated help, and exit code 2 with usage on bad input. */
import { Command, CommanderError, InvalidArgumentError, Option } from 'commander';

import type { OutputStream } from '../../src/loop/index.ts';

import { type Seam, SEAMS } from './dry.ts';

export const LOG_LEVEL = 'INFO';
export const DRY_OTLP_ENDPOINT = 'http://localhost:4318';

/** What the command line asks for, after `--dry` and `--no-otel` have been applied. */
export interface DevOptions {
  readonly fake: readonly Seam[];
  readonly fakeSpeed: number;
  readonly logDir: string | undefined;
  readonly logLevel: string;
  readonly otlpEndpoint: null | string;
  readonly rework: boolean;
  readonly spec: null | number;
  readonly visualize: boolean;
}

export interface ParseEnvironment {
  /** Where `--dry` finds the standard OTLP endpoint variable. */
  env: Readonly<Record<string, string | undefined>>;
  stderr: OutputStream;
  stdout: OutputStream;
}

interface RawOptions {
  dry?: true;
  fake?: Seam[];
  fakeSpeed: number;
  logDir?: string;
  logLevel: string;
  otel: boolean;
  otlpEndpoint?: string;
  rework: boolean;
  spec?: number;
  visualize?: true;
}

/** The options, or the exit code to return when the command line is help or bad input. */
export function parseCommandLine(argv: readonly string[], environment: ParseEnvironment): DevOptions | number {
  const program = new Command('dev')
    .description('Delivers harness Specs through configured repositories, defaulting to implementation on the harness.')
    .option('--log-dir <path>', 'folder for dev.log and the execution logs (default: ./.loop)')
    .option('--log-level <level>', 'console log level', LOG_LEVEL)
    .option('--visualize', 'show the phase table')
    .addOption(new Option('--spec <number>', 'deliver only this Spec number').argParser(parseInteger))
    .option('--no-otel', 'disable telemetry, including for --dry')
    .option(
      '--otlp-endpoint <url>',
      'export traces and metrics over OTLP/HTTP to this base URL, e.g. http://localhost:4318 (needs the OpenTelemetry packages)',
    )
    .addOption(
      new Option(
        '--fake <seam>',
        'replace this external dependency with a fake, repeatable; agent needs no CLI, git no repository, github no network, store no log files',
      )
        .choices(SEAMS)
        .argParser(collectSeam),
    )
    .option(
      '--dry',
      `fake every dependency and publish telemetry to the dashboard (OTLP endpoint defaults to ${DRY_OTLP_ENDPOINT})`,
    )
    .addOption(
      new Option('--fake-speed <factor>', 'delay multiplier for the fake agent; 0 is instant')
        .argParser(parseNumber)
        .default(1, '1.0'),
    )
    .option('--no-rework', 'the fake agent never fails a first attempt')
    .configureOutput({
      writeErr: (text) => environment.stderr.write(text),
      writeOut: (text) => environment.stdout.write(text),
    })
    .showHelpAfterError()
    .exitOverride();
  let raw: RawOptions;
  try {
    program.parse([...argv], { from: 'user' });
    raw = program.opts<RawOptions>();
    const fake = raw.fake ?? [];
    if (fake.includes('git') && !fake.includes('agent') && raw.dry !== true) {
      program.error('error: --fake git needs --fake agent: a real agent would commit where the fake git cannot see', {
        exitCode: 2,
      });
    }
  } catch (error) {
    if (error instanceof CommanderError) {
      return error.exitCode === 0 ? 0 : 2;
    }
    throw error;
  }
  const endpoint =
    raw.dry === true
      ? ((raw.otlpEndpoint ?? '') || (environment.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? '') || DRY_OTLP_ENDPOINT)
      : (raw.otlpEndpoint ?? null);
  return {
    fake: raw.dry === true ? [...SEAMS] : (raw.fake ?? []),
    fakeSpeed: raw.fakeSpeed,
    logDir: raw.logDir,
    logLevel: raw.logLevel,
    otlpEndpoint: raw.otel ? endpoint : null,
    rework: raw.rework,
    spec: raw.spec ?? null,
    visualize: raw.visualize === true,
  };
}

function collectSeam(value: string, previous: readonly Seam[] = []): Seam[] {
  const seam = SEAMS.find((candidate) => candidate === value);
  if (seam === undefined) {
    throw new InvalidArgumentError(`Allowed choices are ${SEAMS.join(', ')}.`);
  }
  return [...previous, seam];
}

function parseInteger(value: string): number {
  if (!/^\s*[+-]?\d+\s*$/.test(value)) {
    throw new InvalidArgumentError('not an integer.');
  }
  return Number(value);
}

function parseNumber(value: string): number {
  const number = Number(value);
  if (value.trim() === '' || Number.isNaN(number)) {
    throw new InvalidArgumentError('not a number.');
  }
  return number;
}
