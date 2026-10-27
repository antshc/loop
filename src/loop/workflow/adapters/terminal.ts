import type { Phase, Reporter, ReportPoint } from '../phases.ts';
import type { PhaseDefinition } from '../workflow.ts';

import { label } from './label.ts';

/** Where the table is written; `isTTY` decides between redrawing in place and one final table. */
export interface OutputStream {
  readonly isTTY?: boolean;
  write: (text: string) => unknown;
}

export interface VisualizeOptions {
  /** Monotonic milliseconds, for running durations. */
  clock?: () => number;
  /** Defaults to stderr. */
  stream?: OutputStream;
  /** Calls `tick` periodically so running durations advance; returns the function that stops it. */
  ticker?: (tick: () => void) => () => void;
}

const COLUMNS = ['Workflow name', 'Phase', 'Owner', 'Status', 'Time'] as const;
const REFRESH_MS = 1000;

/** Renders live phase occurrences on terminals and one final table when redirected. */
export class TerminalReporter implements Reporter {
  readonly #clock: () => number;
  readonly #definitions: readonly PhaseDefinition[];
  #drawn = 0;
  #live = false;
  readonly #name: string;
  readonly #rows: Phase[] = [];
  #stopTicker: (() => void) | null = null;
  readonly #stream: OutputStream;
  readonly #ticker: (tick: () => void) => () => void;

  constructor(name: string, definitions: readonly PhaseDefinition[], options: VisualizeOptions = {}) {
    this.#name = name;
    this.#definitions = definitions;
    this.#stream = options.stream ?? process.stderr;
    this.#clock = options.clock ?? (() => performance.now());
    this.#ticker = options.ticker ?? intervalTicker;
  }

  emit(point: ReportPoint, phase: Phase): void {
    if (point === 'started') {
      this.#rows.push(phase);
    }
    const root = phase.parentId === null;
    if (root && point === 'started' && this.#stream.isTTY === true) {
      this.#live = true;
      this.#stopTicker = this.#ticker(() => {
        try {
          this.#redraw();
        } catch {
          // A closed stream cannot fail the run from a timer.
        }
      });
    }
    try {
      if (this.#live) {
        this.#redraw();
      } else if (root && point === 'finished') {
        this.#stream.write(`${this.#table()}\n`);
      }
    } finally {
      if (root && point === 'finished' && this.#live) {
        this.#stopTicker?.();
        this.#stopTicker = null;
        this.#live = false;
      }
    }
  }

  #redraw(): void {
    const table = this.#table();
    const erase = this.#drawn > 0 ? `\u001B[${String(this.#drawn)}A\u001B[J` : '';
    this.#stream.write(`${erase}${table}\n`);
    this.#drawn = table.split('\n').length;
  }

  #rowsOf(): string[][] {
    const now = this.#clock();
    const seen = new Set<string>();
    const rows: string[][] = [];
    for (const phase of this.#rows) {
      seen.add(phase.name);
      if (phase.visible) {
        const seconds = phase.status === 'running' ? (now - phase.startedAt) / 1000 : phase.seconds;
        rows.push([this.#name, label(phase), phase.owner, phase.status, `${seconds.toFixed(1)}s`]);
      }
    }
    for (const definition of this.#definitions) {
      if (definition.visible && !seen.has(definition.name)) {
        rows.push([this.#name, definition.label, definition.owner, 'pending', '']);
      }
    }
    return rows;
  }

  #table(): string {
    return renderTable(COLUMNS, this.#rowsOf());
  }
}

/** A box-drawn table with a heavy header, wide enough for its widest cell in each column. */
export function renderTable(header: readonly string[], rows: readonly (readonly string[])[]): string {
  const widths = header.map((title, column) => Math.max(width(title), ...rows.map((row) => width(row[column] ?? ''))));
  const rule = (left: string, fill: string, middle: string, right: string): string =>
    left + widths.map((size) => fill.repeat(size + 2)).join(middle) + right;
  const line = (cells: readonly string[], edge: string): string =>
    edge + widths.map((size, column) => ` ${pad(cells[column] ?? '', size)} `).join(edge) + edge;
  return [
    rule('┏', '━', '┳', '┓'),
    line(header, '┃'),
    rule('┡', '━', '╇', '┩'),
    ...rows.map((row) => line(row, '│')),
    rule('└', '─', '┴', '┘'),
  ].join('\n');
}

function intervalTicker(tick: () => void): () => void {
  const handle = setInterval(tick, REFRESH_MS);
  handle.unref();
  return () => {
    clearInterval(handle);
  };
}

function pad(cell: string, size: number): string {
  return cell + ' '.repeat(size - width(cell));
}

function width(cell: string): number {
  return Array.from(cell).length;
}
