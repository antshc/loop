import type { PhaseSpan, SpanAttributeValue } from '../../observe/spans.ts';
import type { WorkflowLogger } from '../logger.ts';
import type { Phase, PhaseTelemetry } from '../phases.ts';

import { isPhaseSpan, type SpanObserver } from '../../observe/spans.ts';
import { reportingFailed } from '../logger.ts';
import { text } from './label.ts';

/** Keeps observer spans current through each phase and exports its final facts. */
export class SpanReporter implements PhaseTelemetry {
  readonly #logger: WorkflowLogger;
  readonly #observer: SpanObserver;

  constructor(observer: SpanObserver, logger: WorkflowLogger) {
    this.#observer = observer;
    this.#logger = logger;
  }

  close(span: null | PhaseSpan, phase: Phase): void {
    if (span === null) {
      return;
    }
    try {
      span.end({
        attributes: { ...attributes(phase), 'loop.phase.status': phase.status },
        error:
          phase.status === 'failed' || phase.status === 'cancelled' ? (phase.reason ?? '') || phase.status : null,
        gates: phase.gates,
      });
    } catch (error) {
      reportingFailed(this.#logger, 'telemetry cleanup', error);
    }
  }

  open(phase: Phase): null | PhaseSpan {
    try {
      const span: unknown = this.#observer.span(phase.label, attributes(phase), phase.owner);
      if (!isPhaseSpan(span)) {
        throw new TypeError('Observer span() must return a span that can run and end');
      }
      return span;
    } catch (error) {
      reportingFailed(this.#logger, 'telemetry', error);
      return null;
    }
  }
}

function attributes(phase: Phase): Record<string, SpanAttributeValue> {
  const result: Record<string, SpanAttributeValue> = {};
  for (const [key, value] of Object.entries(phase.attributes)) {
    if (value !== null && value !== undefined) {
      result[`loop.${key}`] =
        typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? value : text(value);
    }
  }
  result['loop.phase.id'] = phase.id;
  result['loop.phase.kind'] = phase.kind;
  result['loop.phase.owner'] = phase.owner;
  if (phase.parentId !== null) {
    result['loop.phase.parent_id'] = phase.parentId;
  }
  return result;
}
