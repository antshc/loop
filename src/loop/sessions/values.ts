import { randomUUID } from 'node:crypto';

import { ValueError } from '../value-error.ts';

export type Turn = Resume | Start;

/** Parsed CLI output plus the handle to resume by. */
export class CliOutcome {
  readonly exitCode: number;
  readonly handle: NativeHandle;
  readonly output: string;

  constructor(output: string, handle: NativeHandle, exitCode: number) {
    this.output = output;
    this.handle = handle;
    this.exitCode = exitCode;
    Object.freeze(this);
  }
}

/** The id one CLI resumes a session by; only adapters read `value`. */
export class NativeHandle {
  readonly cli: string;
  readonly value: string;

  constructor(cli: string, value: string) {
    this.cli = cli;
    this.value = value;
    Object.freeze(this);
  }
}

/** Turn that continues the session behind `handle`. */
export class Resume {
  readonly handle: NativeHandle;
  readonly name: SessionName;

  constructor(name: SessionName, handle: NativeHandle) {
    this.name = name;
    this.handle = handle;
    Object.freeze(this);
  }
}

/** A stored handle belongs to a different CLI than the one running. */
export class SessionCliMismatch extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'SessionCliMismatch';
  }
}

/** The CLI output carried no handle to resume by. */
export class SessionHandleMissing extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'SessionHandleMissing';
  }
}

/** Caller-chosen name of a session; one name is an independent session per CLI. */
export class SessionName {
  readonly value: string;

  constructor(value: string) {
    if (value === '' || Array.from(value).some(isSpace)) {
      throw new ValueError(`invalid session name: ${JSON.stringify(value)}`);
    }
    this.value = value;
    Object.freeze(this);
  }

  static new(): SessionName {
    return new SessionName(`loop-${randomUUID().replaceAll('-', '')}`);
  }
}

/** Turn that starts a session under `name`. */
export class Start {
  readonly name: SessionName;

  constructor(name: SessionName) {
    this.name = name;
    Object.freeze(this);
  }
}

// The characters that Python's str.isspace() accepts.
function isSpace(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return (
    (code >= 0x09 && code <= 0x0d) ||
    (code >= 0x1c && code <= 0x20) ||
    code === 0x85 ||
    code === 0xa0 ||
    code === 0x1680 ||
    (code >= 0x2000 && code <= 0x200a) ||
    code === 0x2028 ||
    code === 0x2029 ||
    code === 0x202f ||
    code === 0x205f ||
    code === 0x3000
  );
}
