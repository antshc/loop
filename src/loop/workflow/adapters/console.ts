import type { WorkflowLogger } from '../logger.ts';
import type { Phase, Reporter, ReportPoint } from '../phases.ts';

import { label, text } from './label.ts';

const SUMMARY_KEYS = ['verdict', 'ready', 'blocking_findings', 'planned', 'pull_request'] as const;
const LIMIT = 200;

/** Writes only phase transitions and short failure summaries. */
export class ConsoleReporter implements Reporter {
  readonly #logger: WorkflowLogger;

  constructor(logger: WorkflowLogger) {
    this.#logger = logger;
  }

  emit(point: ReportPoint, phase: Phase): void {
    if (!phase.visible) {
      return;
    }
    if (point === 'started') {
      this.#logger.info(`${label(phase)} started`);
      return;
    }
    let suffix = '';
    for (const key of SUMMARY_KEYS) {
      if (key in phase.attributes) {
        suffix += ` · ${key}=${oneLine(text(phase.attributes[key]))}`;
      }
    }
    if (phase.reason !== null && phase.reason !== '') {
      suffix += ` · ${oneLine(phase.reason)}`;
    }
    const message = `${label(phase)} ${phase.status} · ${phase.seconds.toFixed(1)}s${suffix}`;
    if (phase.status === 'failed' || phase.status === 'cancelled') {
      this.#logger.warn(message);
    } else {
      this.#logger.info(message);
    }
  }
}

function oneLine(value: string): string {
  return Array.from(value.split(/\s+/).filter(Boolean).join(' ')).slice(0, LIMIT).join('');
}
