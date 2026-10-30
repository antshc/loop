// Dependency-free and erasable-syntax only, so it still loads under Node.js to refuse it.

export const MINIMUM_BUN_VERSION = '1.4.4';

export type RuntimeCheck = { message: string; ok: false } | { ok: true };

export interface RuntimeGuardEnvironment {
  exit: (code: number) => void;
  stderr: (message: string) => void;
  versions: RuntimeVersions;
}

export interface RuntimeVersions {
  readonly bun?: string | undefined;
  readonly node?: string | undefined;
}

export function checkRuntime(
  versions: RuntimeVersions,
  minimumBunVersion: string = MINIMUM_BUN_VERSION,
): RuntimeCheck {
  const bunVersion = versions.bun;
  if (bunVersion === undefined) {
    return {
      message: `Node.js is unsupported: run this Workflow with Bun >= ${minimumBunVersion}.`,
      ok: false,
    };
  }
  if (compareVersions(bunVersion, minimumBunVersion) < 0) {
    return {
      message: `Bun ${bunVersion} is unsupported: Bun >= ${minimumBunVersion} is required.`,
      ok: false,
    };
  }
  return { ok: true };
}

export function compareVersions(left: string, right: string): number {
  const leftParts = parseVersion(left);
  const rightParts = parseVersion(right);
  for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index++) {
    const difference = (leftParts[index] ?? 0) - (rightParts[index] ?? 0);
    if (difference !== 0) {
      return Math.sign(difference);
    }
  }
  return 0;
}

export function enforceRuntime(environment: RuntimeGuardEnvironment = defaultEnvironment()): void {
  const result = checkRuntime(environment.versions);
  if (!result.ok) {
    environment.stderr(result.message);
    environment.exit(1);
  }
}

function defaultEnvironment(): RuntimeGuardEnvironment {
  return {
    exit: (code) => {
      process.exit(code);
    },
    stderr: (message) => {
      process.stderr.write(`${message}\n`);
    },
    versions: process.versions,
  };
}

function parseVersion(version: string): number[] {
  return (version.split('-')[0] ?? '')
    .split('.')
    .map((part) => Number.parseInt(part, 10) || 0);
}
