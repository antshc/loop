import { Cancelled } from './cancelled.ts';

export interface EntrypointEnvironment {
  exit: (code: number) => void;
}

/** Runs a Workflow's main and maps cancellation to exit code 130 (SIGINT) or 143 (SIGTERM). */
export async function runEntrypoint(
  main: () => Promise<void>,
  environment: EntrypointEnvironment = defaultEnvironment(),
): Promise<void> {
  try {
    await main();
  } catch (error) {
    if (error instanceof Cancelled) {
      environment.exit(error.exitCode);
      return;
    }
    throw error;
  }
}

function defaultEnvironment(): EntrypointEnvironment {
  return {
    exit: (code) => {
      process.exit(code);
    },
  };
}
