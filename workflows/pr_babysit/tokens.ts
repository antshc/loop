/** Dependency-injection tokens, named after the seam each one stands for; `composition.ts` binds them. */
export const TOKENS = {
  /** `() => AgentBuilder`: a fresh Agent builder per workspace. */
  AgentFactory: Symbol.for('pr_babysit.AgentFactory'),
  /** `DiscoveryService`: continuous PR discovery. */
  DiscoveryService: Symbol.for('pr_babysit.DiscoveryService'),
  /** `(message) => void`: operator-facing diagnostics. */
  ErrorReporter: Symbol.for('pr_babysit.ErrorReporter'),
  /** `() => void`: throws when another Workflow run holds the file lock. */
  FileLockGuard: Symbol.for('pr_babysit.FileLockGuard'),
  /** `GithubService`: GitHub queries and mutations. */
  GithubService: Symbol.for('pr_babysit.GithubService'),
  /** `ProcessRunner`: child processes for GitHub, Git and verification. */
  ProcessRunner: Symbol.for('pr_babysit.ProcessRunner'),
  /** `(text) => void`: the standard error stream. */
  StderrWriter: Symbol.for('pr_babysit.StderrWriter'),
  /** `(text) => void`: the standard output stream. */
  StdoutWriter: Symbol.for('pr_babysit.StdoutWriter'),
  /** `StopRequest`: the operator's SIGINT or SIGTERM and the interruptible clock. */
  StopRequest: Symbol.for('pr_babysit.StopRequest'),
  /** `Verify`: build and test subprocesses. */
  Verification: Symbol.for('pr_babysit.Verification'),
  /** `WorkerService`: maintenance of one explicit PR. */
  WorkerService: Symbol.for('pr_babysit.WorkerService'),
  /** `WorkflowGit`: Git inspection and publication. */
  WorkflowGit: Symbol.for('pr_babysit.WorkflowGit'),
} as const;
