// Self-contained dev Workflow: delivers harness Specs through configured repositories and publishes draft pull requests.
// Run from the harness root through a user alias: `bun workflows/dev/main.ts [--dry | --fake <seam>] [--spec <number>]`.
import { enforceRuntime, runEntrypoint } from '../../src/loop/index.ts';
import { parseCommandLine } from './args.ts';
import { type DevDependencies, runDev } from './run.ts';

export type { DevDependencies } from './run.ts';

/**
 * Delivers harness Specs through configured repositories, defaulting to implementation on the harness;
 * `--fake <seam>` or `--dry` replaces external dependencies with fakes.
 * Resolves to the exit code: 0 on success or `--help`, 1 when a Spec failed or needs human input, 2 for bad input.
 */
export async function main(argv: readonly string[], dependencies: DevDependencies = {}): Promise<number> {
  const { env = process.env, stderr = process.stderr, stdout = process.stdout } = dependencies;
  const options = parseCommandLine(argv, { env, stderr, stdout });
  if (typeof options === 'number') {
    return options;
  }
  return runDev(options, dependencies, stderr);
}

if (import.meta.main) {
  enforceRuntime();
  await runEntrypoint(async () => {
    process.exitCode = await main(process.argv.slice(2));
  });
}
