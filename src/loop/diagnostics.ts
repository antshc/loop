/** A named logger; library code logs through it and stays silent until a sink is installed. */
export interface Logger {
  debug: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  warning: (message: string) => void;
}

export type LogLevel = 'debug' | 'error' | 'info' | 'warning';

export interface LogRecord {
  readonly level: LogLevel;
  readonly logger: string;
  readonly message: string;
}

export type LogSink = (record: LogRecord) => void;

let sink: LogSink | null = null;

/** The logger for one library module, such as `loop.git.strategies`. */
export function getLogger(name: string): Logger {
  const emit = (level: LogLevel, message: string): void => {
    try {
      sink?.({ level, logger: name, message });
    } catch {
      // Logging never fails the run.
    }
  };
  return {
    debug: (message) => {
      emit('debug', message);
    },
    error: (message) => {
      emit('error', message);
    },
    info: (message) => {
      emit('info', message);
    },
    warning: (message) => {
      emit('warning', message);
    },
  };
}

/** Installs the process-wide sink (null restores silence) and returns the previous one. */
export function setLogSink(next: LogSink | null): LogSink | null {
  const previous = sink;
  sink = next;
  return previous;
}
