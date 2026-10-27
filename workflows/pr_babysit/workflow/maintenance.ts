import type {
  AgentBuilder,
  RunObserver,
  SpanObserver,
  WorkflowRun,
} from '../../../src/loop/index.ts';
import type { WorkflowGit } from '../infrastructure/git.ts';
import type { GithubService } from '../infrastructure/github.ts';
import type { PrState } from '../infrastructure/state.ts';
import type { Verify } from '../infrastructure/verification.ts';
import type { Repository, Settings } from '../settings.ts';

/** Everything one maintenance pass touches; tests and the entry point supply the doubles or real parts. */
export interface Maintenance {
  /** Throws `Cancelled` when the operator interrupted the worker; called between steps that spawn no child. */
  readonly checkpoint: () => void;
  readonly flow: WorkflowRun;
  readonly git: WorkflowGit;
  readonly github: GithubService;
  readonly login: string;
  /** A fresh Agent builder per workspace. */
  readonly newAgent: () => AgentBuilder;
  readonly observer: null | Observer;
  readonly repository: Repository;
  readonly settings: Settings;
  readonly state: PrState;
  readonly verify: Verify;
}

/** Reports Agent Runs and Worktrees, and opens the Workflow's phase spans. */
export type Observer = RunObserver & SpanObserver;
