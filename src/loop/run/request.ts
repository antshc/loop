import type { SessionName } from '../sessions/values.ts';

/** One agent execution. */
export class AgentRequest {
  /** Display name for a stateless run's new session; a resumable session is named by its SessionName. */
  readonly name: null | string;
  readonly prompt: string;

  constructor(prompt: string, name: null | string = null) {
    this.prompt = prompt;
    this.name = name;
    Object.freeze(this);
  }

  with(changes: { name?: null | string; prompt?: string }): AgentRequest {
    return new AgentRequest(changes.prompt ?? this.prompt, changes.name === undefined ? this.name : changes.name);
  }
}

/** Final outcome of one execution, carrying the session name it ran under. */
export class AgentResult {
  readonly exitCode: number;
  readonly output: string;
  readonly session: SessionName;

  constructor(output: string, session: SessionName, exitCode: number) {
    this.output = output;
    this.session = session;
    this.exitCode = exitCode;
    Object.freeze(this);
  }
}
