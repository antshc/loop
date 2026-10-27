/** Where workflow reporting writes its short records; silent unless the host supplies one. */
export interface WorkflowLogger {
  info: (message: string) => void;
  warn: (message: string) => void;
}

export const SILENT_LOGGER: WorkflowLogger = {
  info: () => undefined,
  warn: () => undefined,
};

/** Reporting is observational: a broken logger or exporter is noted, never raised. */
export function reportingFailed(logger: WorkflowLogger, operation: string, error: unknown): void {
  const kind = error instanceof Error ? error.name : typeof error;
  try {
    logger.warn(`Workflow ${operation} unavailable (${kind})`);
  } catch {
    // A broken host logger cannot become a workflow failure either.
  }
}
