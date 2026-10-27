import { ValueError } from '../value-error.ts';

export const DEFAULT_LOOP_HOOK_TIMEOUT_SEC = 120;

/** Moment in a Loop run at which a Loop hook runs. */
export const LoopHookPoint = {
  RUN_FINISHED: 'run-finished',
  WORKTREE_READY: 'worktree-ready',
  WORKTREE_REMOVING: 'worktree-removing',
} as const;
export type LoopHookPoint = (typeof LoopHookPoint)[keyof typeof LoopHookPoint];

/** Base of shell commands run by Loop at one fixed `point`. Use a subclass. */
export abstract class LoopHook {
  readonly command: string;
  abstract readonly point: LoopHookPoint;
  readonly timeoutSec: number;

  protected constructor(command: string, timeoutSec: number = DEFAULT_LOOP_HOOK_TIMEOUT_SEC) {
    if (new.target === LoopHook) {
      throw new TypeError('LoopHook fixes no hook point; use a point subclass');
    }
    if (command === '') {
      throw new ValueError('Loop hook command must not be empty');
    }
    if (timeoutSec <= 0) {
      throw new ValueError('Loop hook timeoutSec must be positive');
    }
    this.command = command;
    this.timeoutSec = timeoutSec;
  }
}

/** A Loop hook exited non-zero or timed out. */
export class LoopHookError extends Error {
  readonly command: string;
  readonly output: string;
  readonly point: LoopHookPoint;

  constructor(point: LoopHookPoint, command: string, output: string, options?: ErrorOptions) {
    super(`${point} hook failed: ${command}\n${output}`, options);
    this.name = 'LoopHookError';
    this.point = point;
    this.command = command;
    this.output = output;
  }
}

/** Runs in the repository after the worktree is merged and removed; only when the run succeeded. */
export class RunFinishedLoopHook extends LoopHook {
  readonly point = LoopHookPoint.RUN_FINISHED;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

/** Runs in a fresh worktree before the agent starts; may write only gitignored paths. */
export class WorktreeReadyLoopHook extends LoopHook {
  readonly point = LoopHookPoint.WORKTREE_READY;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

/** Runs after a successful body, before cleanliness checks and worktree removal. */
export class WorktreeRemovingLoopHook extends LoopHook {
  readonly point = LoopHookPoint.WORKTREE_REMOVING;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}
