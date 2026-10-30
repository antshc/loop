import { describe, expect, test } from 'bun:test';

import {
  checkRuntime,
  compareVersions,
  enforceRuntime,
  MINIMUM_BUN_VERSION,
  type RuntimeGuardEnvironment,
  type RuntimeVersions,
} from '../../src/loop/runtime-guard.ts';

interface Recorded {
  environment: RuntimeGuardEnvironment;
  exitCodes: number[];
  messages: string[];
}

function record(versions: RuntimeVersions): Recorded {
  const exitCodes: number[] = [];
  const messages: string[] = [];
  return {
    environment: {
      exit: (code) => exitCodes.push(code),
      stderr: (message) => messages.push(message),
      versions,
    },
    exitCodes,
    messages,
  };
}

describe('checkRuntime', () => {
  test('refuses Node.js when Bun is absent', () => {
    const result = checkRuntime({ node: '22.0.0' });
    expect(result).toEqual({ message: expect.stringContaining('Node.js is unsupported') as string, ok: false });
  });

  test('reports the required version when Bun is older than the minimum', () => {
    const result = checkRuntime({ bun: '1.4.3' });
    expect(result.ok).toBe(false);
    expect(JSON.stringify(result)).toContain(`>= ${MINIMUM_BUN_VERSION}`);
  });

  test.each(['1.4.4', '1.4.5', '1.10.0', '2.0.0', '1.4.4-canary.1'])('accepts Bun %s', (version) => {
    expect(checkRuntime({ bun: version })).toEqual({ ok: true });
  });

  test('uses the supplied minimum', () => {
    expect(checkRuntime({ bun: '1.4.4' }, '1.5.0').ok).toBe(false);
  });
});

describe('compareVersions', () => {
  test.each([
    ['1.4.4', '1.4.4', 0],
    ['1.4.10', '1.4.9', 1],
    ['1.4', '1.4.1', -1],
    ['2.0.0', '1.99.99', 1],
  ] as const)('compares %s with %s', (left, right, expected) => {
    expect(compareVersions(left, right)).toBe(expected);
  });
});

describe('enforceRuntime', () => {
  test('does nothing under a supported Bun', () => {
    const { environment, exitCodes, messages } = record({ bun: MINIMUM_BUN_VERSION });
    enforceRuntime(environment);
    expect(exitCodes).toEqual([]);
    expect(messages).toEqual([]);
  });

  test('prints the Node.js refusal and exits non-zero', () => {
    const { environment, exitCodes, messages } = record({});
    enforceRuntime(environment);
    expect(exitCodes).toEqual([1]);
    expect(messages.join()).toContain('Node.js is unsupported');
  });

  test('prints the required Bun version for an older Bun and exits non-zero', () => {
    const { environment, exitCodes, messages } = record({ bun: '1.0.0' });
    enforceRuntime(environment);
    expect(exitCodes).toEqual([1]);
    expect(messages.join()).toContain(MINIMUM_BUN_VERSION);
  });
});

describe('package metadata', () => {
  test('declares the same minimum Bun version as the guard', async () => {
    const metadata = (await Bun.file(`${import.meta.dir}/../../package.json`).json()) as {
      engines: { bun: string };
      packageManager: string;
    };
    expect(metadata.engines.bun).toBe(`>=${MINIMUM_BUN_VERSION}`);
    expect(metadata.packageManager).toBe(`bun@${MINIMUM_BUN_VERSION}`);
  });
});

describe('entry guard under Node.js', () => {
  const nodePath = Bun.which('node');
  test.skipIf(nodePath === null)('exits non-zero with an unsupported message', () => {
    const result = Bun.spawnSync([nodePath ?? 'node', `${import.meta.dir}/../../src/loop/require-bun.ts`]);
    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toString()).toContain('Node.js is unsupported');
  });
});
