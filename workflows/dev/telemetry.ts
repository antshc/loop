/** The span-capable observer accepted by the dev composition root. */
import type { RunObserver, SpanObserver } from '../../src/loop/index.ts';

/** A RunObserver that also opens Workflow-owned spans on its own trace provider; `shutdown` flushes its exporters. */
export type TracingObserver = RunObserver & SpanObserver & { shutdown?: () => Promise<void> };
