/** Injection tokens named after the dev Workflow's seam interfaces; the composition root binds each to an implementation. */

/** `() => AgentBuilder`: a fresh Agent builder per run. */
export const AGENT_FACTORY = Symbol.for('AgentFactory');
/** `() => number`: monotonic milliseconds for the visualization's running durations. */
export const CLOCK = Symbol.for('Clock');
/** `Run`: the CLI process runner behind the Git service. */
export const COMMAND_RUNNER = Symbol.for('CommandRunner');
/** `() => string`: the directory a relative or default log directory is resolved from. */
export const CURRENT_DIRECTORY = Symbol.for('CurrentDirectory');
/** `DevOptions`: what the command line asked for. */
export const DEV_OPTIONS = Symbol.for('DevOptions');
/** `ExecutionStore`: where failed attempts are kept. */
export const EXECUTION_STORE = Symbol.for('ExecutionStore');
/** `GitService`: the Workflow's Git reads and pushes. */
export const GIT_SERVICE = Symbol.for('GitService');
/** `GithubFactory | null`: the injected repository client factory; null selects the real `gh`. */
export const GITHUB_FACTORY = Symbol.for('GithubFactory');
/** `GitHubRepo`: the harness repository's GitHub client. */
export const HARNESS_GITHUB = Symbol.for('HarnessGithub');
/** `Repository`: the harness checkout the Workflow runs from. */
export const HARNESS_REPOSITORY = Symbol.for('HarnessRepository');
/** `string`: where agents' evidence and the log file are kept. */
export const LOG_DIRECTORY = Symbol.for('LogDirectory');
/** `readonly LoopHook[]`: the hooks run around every Ticket. */
export const LOOP_HOOKS = Symbol.for('LoopHooks');
/** `(endpoint) => TracingObserver`: builds the observer exporting traces and metrics to the OTLP endpoint. */
export const OBSERVER_FACTORY = Symbol.for('ObserverFactory');
/** `readonly RepositoryWorkflow[]`: the configured repositories and their agent delegates. */
export const REPOSITORIES = Symbol.for('RepositoryWorkflows');
/** `readonly SessionLogReader[] | null`: the session-log readers observing runs; null selects the Copilot reader. */
export const SESSION_LOG_READERS = Symbol.for('SessionLogReaders');
/** `OutputStream`: receives the console log and the visualization. */
export const STDERR = Symbol.for('Stderr');
/** `TicketsTracker`: the harness repository's Spec and Ticket tracker. */
export const TICKETS_TRACKER = Symbol.for('TicketsTracker');
