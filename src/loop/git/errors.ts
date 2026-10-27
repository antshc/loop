/** The worktree identity or unfinished changes prevent a safe lifecycle transition. */
export class WorktreeError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'WorktreeError';
  }
}
