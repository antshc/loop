/** Publishes a Spec's feature branch as a draft pull request. */
import type { DevDeps } from './backlog.ts';
import type { Repository, Spec } from './records.ts';

import { githubFor, PullRequestClient } from './platforms.ts';
import { TicketWorkflowError } from './records.ts';

/** Pushes the feature branch from `pusher` when it holds new commits and opens (or reuses) its draft pull request; resolves to the URL, or null when nothing was pushed. */
export async function publishPullRequest(spec: Spec, repository: Repository, pusher: string, deps: DevDeps): Promise<null | string> {
  const { baseBranch, featureBranch } = spec;
  if (baseBranch === null || featureBranch === null) {
    throw new TicketWorkflowError(`cannot resolve branches for Spec ${spec.url}`);
  }
  return deps.phases.phase('publish', { attributes: { spec: spec.number } }, async (phase) => {
    if (!(await deps.git.pushIfAhead(pusher, featureBranch, baseBranch))) {
      phase.skip('No new commits to publish');
      return null;
    }
    const client = new PullRequestClient(githubFor(repository, deps.githubFactory));
    const title = `${spec.initiative}: ${spec.bareTitle}`;
    const pullRequest = await client.createDraftPullRequest(featureBranch, baseBranch, title, pullRequestBody(spec));
    phase.set({ pull_request: pullRequest.url });
    return pullRequest.url;
  });
}

/** The initiative ID on the first line (the code-review initiative gate reads it), then every Ticket and whether it is delivered. */
function pullRequestBody(spec: Spec): string {
  const lines = [spec.initiative, '', `Spec: ${spec.url}`, '', 'Tickets:'];
  for (const ticket of spec.tickets) {
    lines.push(`- [${ticket.state === 'closed' ? 'x' : ' '}] #${String(ticket.number)} ${ticket.title}`);
  }
  return lines.join('\n');
}
