/** The optional capability an observer offers to export Workflow-owned phase spans. */

/** One open phase span. */
export interface PhaseSpan {
  /** Closes the span with the phase's final facts. */
  end: (final: PhaseSpanEnd) => void;
  /** Runs the body with this span current, so Agent Run and Worktree spans started inside it nest under it. */
  run: <T>(body: () => Promise<T>) => Promise<T>;
}

/** The facts a phase span is closed with. */
export interface PhaseSpanEnd {
  attributes: Readonly<Record<string, SpanAttributeValue>>;
  /** Set when the phase failed or was cancelled; the description of why. */
  error: null | string;
  gates: readonly PhaseSpanGate[];
}

/** One recorded check of a phase, exported as a span event. */
export interface PhaseSpanGate {
  check: string;
  note: string;
  ok: boolean;
}

export type SpanAttributeValue = boolean | number | string;

/** An observer that can open a span exported as resource `role`. */
export interface SpanObserver {
  span: (name: string, attributes: Readonly<Record<string, SpanAttributeValue>>, role: string) => PhaseSpan;
}

/** Whether a value returned by `span()` can scope a body and end. */
export function isPhaseSpan(value: unknown): value is PhaseSpan {
  return (
    typeof value === 'object' &&
    value !== null &&
    'run' in value &&
    typeof value.run === 'function' &&
    'end' in value &&
    typeof value.end === 'function'
  );
}

/** Whether the observer offers `span()`; observers without it still receive Agent Run and Worktree facts. */
export function isSpanObserver(observer: unknown): observer is SpanObserver {
  return (
    (typeof observer === 'object' || typeof observer === 'function') &&
    observer !== null &&
    'span' in observer &&
    typeof observer.span === 'function'
  );
}
