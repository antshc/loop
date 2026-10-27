import { describe, expect, test } from 'bun:test';

import { type DevOptions, DRY_OTLP_ENDPOINT, parseCommandLine } from '../args.ts';
import { SEAMS } from '../dry.ts';
import { Stream } from './support/stream.ts';

function parse(argv: readonly string[], env: Record<string, string | undefined> = {}): DevOptions | number {
  return parseCommandLine(argv, { env, stderr: new Stream(), stdout: new Stream() });
}

describe('the command line defaults', () => {
  test('are the Python Workflow defaults', () => {
    expect(parse([])).toEqual({
      fake: [],
      fakeSpeed: 1,
      logDir: undefined,
      logLevel: 'INFO',
      otlpEndpoint: null,
      rework: true,
      spec: null,
      visualize: false,
    });
  });
});

describe('the command line flags', () => {
  test('parse every value', () => {
    expect(
      parse([
        '--log-dir',
        'logs',
        '--log-level',
        'DEBUG',
        '--visualize',
        '--spec',
        '12',
        '--otlp-endpoint',
        'http://collector:4318',
        '--fake',
        'agent',
        '--fake',
        'git',
        '--fake-speed',
        '0.5',
        '--no-rework',
      ]),
    ).toEqual({
      fake: ['agent', 'git'],
      fakeSpeed: 0.5,
      logDir: 'logs',
      logLevel: 'DEBUG',
      otlpEndpoint: 'http://collector:4318',
      rework: false,
      spec: 12,
      visualize: true,
    });
  });

  test('--dry fakes every seam and defaults the endpoint to the local dashboard', () => {
    expect(parse(['--dry'])).toMatchObject({ fake: [...SEAMS], otlpEndpoint: DRY_OTLP_ENDPOINT });
    expect(DRY_OTLP_ENDPOINT).toBe('http://localhost:4318');
  });

  test('--dry prefers the explicit endpoint, then the standard OTLP variable', () => {
    const env = { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://env:4318' };

    expect(parse(['--dry'], env)).toMatchObject({ otlpEndpoint: 'http://env:4318' });
    expect(parse(['--dry', '--otlp-endpoint', 'http://flag:4318'], env)).toMatchObject({
      otlpEndpoint: 'http://flag:4318',
    });
  });

  test('--no-otel clears the endpoint, including for --dry', () => {
    expect(parse(['--dry', '--no-otel'])).toMatchObject({ otlpEndpoint: null });
    expect(parse(['--otlp-endpoint', 'http://flag:4318', '--no-otel'])).toMatchObject({ otlpEndpoint: null });
  });

  test('--dry overrides the faked seams and so allows a faked git', () => {
    expect(parse(['--dry', '--fake', 'git'])).toMatchObject({ fake: [...SEAMS] });
  });

  test('--help is not bad input', () => {
    expect(parse(['--help'])).toBe(0);
  });
});

describe('bad input', () => {
  test.each([
    ['an unknown flag', ['--nope']],
    ['an unknown seam', ['--fake', 'docker']],
    ['a non-integer --spec', ['--spec', 'x']],
    ['a fractional --spec', ['--spec', '1.5']],
    ['a non-numeric --fake-speed', ['--fake-speed', 'fast']],
    ['a missing value', ['--log-dir']],
    ['a stray positional argument', ['extra']],
    ['a faked git without a faked agent', ['--fake', 'git']],
    ['a faked git next to other seams but no agent', ['--fake', 'git', '--fake', 'github']],
  ])('exits 2 with usage for %s', (_name, argv) => {
    const stderr = new Stream();

    const result = parseCommandLine(argv, { env: {}, stderr, stdout: new Stream() });

    expect(result).toBe(2);
    expect(stderr.text).toContain('Usage: dev [options]');
    expect(stderr.text).toContain('error:');
  });

  test('explains a faked git without a faked agent', () => {
    const stderr = new Stream();

    parseCommandLine(['--fake', 'git'], { env: {}, stderr, stdout: new Stream() });

    expect(stderr.text).toContain('--fake git needs --fake agent');
  });
});

describe('the generated help', () => {
  test('lists every flag with its choices, to stdout', () => {
    const stdout = new Stream();
    const stderr = new Stream();

    expect(parseCommandLine(['--help'], { env: {}, stderr, stdout })).toBe(0);

    for (const flag of [
      '--log-dir',
      '--log-level',
      '--visualize',
      '--spec',
      '--no-otel',
      '--otlp-endpoint',
      '--fake',
      '--dry',
      '--fake-speed',
      '--no-rework',
    ]) {
      expect(stdout.text).toContain(flag);
    }
    expect(stdout.text).toContain('agent');
    expect(stdout.text).toContain('store');
    expect(stderr.text).toBe('');
  });
});
