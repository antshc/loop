import {
  type AgentBuilder,
  fakeAgent,
  FakeCliRunner,
  FakeReply,
  type WorkflowRun,
} from '../../../../src/loop/index.ts';
import { CommandError } from '../../commands.ts';
import { devWorkflow } from '../../definition.ts';
import { GitHubRepo, PullRequestClient, TicketsTracker } from '../../platforms.ts';
import { type Repository, RepositoryId, Spec, Ticket } from '../../records.ts';
import { reviewDelegate } from '../../review-agent.ts';
import { type Delivery, type ReviewDeps } from '../../review.ts';
import { FakeGh } from './fake-gh.ts';
import { FakeReviewGit } from './review-fakes.ts';

export const ALL_AXES = ['bugs', 'quality', 'smells', 'initiative'];

export const PULL_REQUEST_URL = 'https://github.com/antshc/loop/pull/9';

type Respond = (prompt: string) => string;

/** The review phases wired to fakes: in-memory git, a recording `gh`, a fake agent CLI, and the dev Workflow's log lines. */
export class ReviewHarness {
  failReady = false;
  readonly gh = new FakeGh();
  readonly git = new FakeReviewGit();
  readonly lines: string[] = [];
  readonly prompts: string[] = [];
  readonly repository: Repository = {
    checkout: '/work/loop',
    ownerName: new RepositoryId('antshc', 'loop'),
    worktrees: '/work/loop.worktrees',
  };
  /** The real reviewer delegate over the fake agent CLI. */
  runReview = reviewDelegate();
  readonly spec: Spec;
  readonly workflow = devWorkflow({
    logger: {
      info: (message) => this.lines.push(message),
      warn: (message) => this.lines.push(message),
    },
  });

  constructor(tickets: readonly Ticket[] = [closedTicket(10)], labels: readonly string[] = ['spec', 'repo:base:main', 'repo:target:antshc/loop']) {
    this.spec = new Spec({ labels, number: 1, tickets, title: 'CHK-1: Add basket', url: 'https://github.com/antshc/loop/issues/1' });
  }

  /** The delivery of the Spec at the current head. */
  delivery(changes: Partial<Delivery> = {}): Delivery {
    return {
      commits: ['c0000001 ccode(CHK-1|10): Add basket'],
      delivered: true,
      headSha: this.git.commits.at(-1)?.sha ?? '',
      pullRequestUrl: PULL_REQUEST_URL,
      ...changes,
    };
  }

  /** The `gh` calls that start with `prefix`. */
  ghCalls(...prefix: string[]): (readonly string[])[] {
    return this.gh.calls.filter((call) => prefix.every((part, index) => call[index] === part));
  }

  newAgent = (): AgentBuilder => fakeAgent(this.#runner());

  pulls(): PullRequestClient {
    return new PullRequestClient(this.#repo());
  }

  /** What the reviewer answers to a prompt; by default a clean review of the head named in it. */
  respond: Respond = (prompt) => reviewReply(/head is commit\s+`([^`]+)`/.exec(prompt)?.[1] ?? '');

  /** Everything the review stage needs, as the delivery flow would pass it. */
  reviewDeps(phases: WorkflowRun): ReviewDeps & { githubFactory: () => GitHubRepo; tracker: TicketsTracker } {
    const repo = this.#repo();
    return { git: this.git, githubFactory: () => repo, newAgent: this.newAgent, phases, tracker: this.tracker() };
  }

  run<T>(body: (phases: WorkflowRun) => Promise<T>): Promise<T> {
    return this.workflow.run(body);
  }

  tracker(): TicketsTracker {
    return new TicketsTracker('antshc', 'loop', (args) => this.#gh(args));
  }

  #gh(args: readonly string[]): Promise<string> {
    if (this.failReady && args[0] === 'pr' && args[1] === 'ready') {
      this.gh.calls.push(args);
      return Promise.reject(new CommandError('gh pr ready', 1, 'GraphQL: not mergeable'));
    }
    return this.gh.run(args);
  }

  #repo(): GitHubRepo {
    return new GitHubRepo('antshc', 'loop', (args) => this.#gh(args));
  }

  #runner(): FakeCliRunner {
    return new FakeCliRunner(
      (request) => {
        this.prompts.push(request.prompt);
        return new FakeReply(this.respond(request.prompt));
      },
      { speed: 0 },
    );
  }
}

export function closedTicket(number: number, labels: readonly string[] = []): Ticket {
  return new Ticket({
    labels,
    number,
    state: 'closed',
    title: `Ticket ${String(number)}`,
    url: `https://github.com/antshc/loop/issues/${String(number)}`,
  });
}

/** The reason a promise rejects with; fails the test when it fulfils. */
export async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected the promise to reject');
}

/** The reviewer's final message: prose, then one JSON object with every axis completed unless changed. */
export function reviewReply(head: string, changes: { axes?: readonly string[]; findings?: readonly object[]; head?: string } = {}): string {
  const result = {
    axes: (changes.axes ?? ALL_AXES).map((name) => ({ name, outcome: 'completed' })),
    findings: changes.findings ?? [],
    head: changes.head ?? head,
  };
  return `Reviewed.\n${JSON.stringify({ result, status: 'completed' })}`;
}
