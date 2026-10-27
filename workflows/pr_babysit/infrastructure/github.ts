import { inject, injectable } from 'inversify';
import { createHash } from 'node:crypto';

import { type ProcessRunner, processRunner, ValueError } from '../../../src/loop/index.ts';
import { TOKENS } from '../tokens.ts';
import { encodeSortedCompact } from './json.ts';

const THREADS = `query($owner:String!,$name:String!,$number:Int!,$cursor:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){
pageInfo{hasNextPage endCursor} nodes{id isResolved isOutdated path line originalLine
comments(first:100){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}}}`;
const COMMENTS = `query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{
comments(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}`;
const REPLY =
  'mutation($id:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$id,body:$body}){comment{id}}}';
const TRUSTED = new Set(['COLLABORATOR', 'MEMBER', 'OWNER']);
const BOTS = new Set(['bugbot', 'chatgpt-codex-connector', 'claude', 'coderabbitai', 'copilot', 'copilot-pull-request-reviewer']);
const COMMAND_TIMEOUT_SEC = 120;

export interface Account {
  readonly login: string;
}

export interface ActionsRun {
  readonly conclusion?: null | string;
  readonly event?: string;
  readonly head_sha: string;
  readonly id: number;
  readonly run_attempt?: number;
  readonly status: string;
  readonly workflow_id: number;
}

export interface Check {
  readonly app?: null | { readonly id: number };
  readonly conclusion?: null | string;
  readonly id: number;
  readonly name: string;
  readonly status: string;
}

export interface CommitStatus {
  readonly context: string;
  readonly id: number;
  readonly state: string;
}

/** What the Workflow needs from GitHub; tests replace it with an in-memory remote. */
export interface GithubService {
  login: () => Promise<string>;
  logs: (repo: string, runs: readonly ActionsRun[]) => Promise<string>;
  openPrs: (repo: string, author: string) => Promise<PullRequest[]>;
  pullRequest: (repo: string, number: number) => Promise<PullRequest>;
  /** Post a plain reply while leaving the review thread open. */
  reply: (repo: string, number: number, item: ReplyTarget, body: string) => Promise<void>;
  requestCopilot: (repo: string, number: number) => Promise<void>;
  /** Rerun only the failed jobs of one diagnosed workflow run. */
  retry: (repo: string, runId: number) => Promise<void>;
  snapshot: (repo: string, number: number) => Promise<Snapshot>;
}

/** A PR conversation comment or submitted review that is not part of a review thread. */
export interface OtherFeedback {
  readonly author: Account | null;
  readonly author_association?: null | string;
  readonly body: string;
  readonly created_at?: null | string;
  readonly id: string;
  readonly kind: 'comment' | 'review';
  readonly updated_at?: null | string;
  readonly url: string;
}

export interface PullRequest {
  readonly draft: boolean;
  readonly head: {
    readonly ref: string;
    readonly repo?: null | { readonly full_name: string };
    readonly sha: string;
  };
  readonly html_url: string;
  readonly mergeable?: boolean | null;
  readonly mergeable_state?: string;
  readonly merged?: boolean;
  readonly number: number;
  readonly requested_reviewers?: readonly Account[];
  readonly state: string;
  readonly user: Account;
}

export interface ReplyTarget {
  readonly kind: string;
  readonly thread_id?: string;
}

export interface ReviewComment {
  readonly author: Account | null;
  readonly authorAssociation?: null | string;
  readonly body: string;
  readonly id: string;
  readonly pullRequestReview?: null | { readonly state: string };
}

export interface ReviewThread {
  readonly comments: ReviewComment[];
  readonly id: string;
  readonly isOutdated?: boolean;
  readonly isResolved: boolean;
  readonly line?: null | number;
  readonly originalLine?: null | number;
  readonly path: string;
}

/** A PR with its discussion gate inputs, checks and the workflow runs of its current head. */
export interface Snapshot {
  readonly checks: readonly Check[];
  readonly other: readonly OtherFeedback[];
  readonly pr: PullRequest;
  readonly review?: { readonly mergeStateStatus?: string; readonly reviewDecision?: null | string };
  readonly runs: readonly ActionsRun[];
  readonly statuses: readonly CommitStatus[];
  readonly threads: readonly ReviewThread[];
}

interface Connection<T> {
  readonly nodes: T[];
  readonly pageInfo: { readonly endCursor: null | string; readonly hasNextPage: boolean };
}

interface GraphqlResponse<T> {
  readonly data: T;
  readonly errors?: unknown;
}

interface RawThread extends Omit<ReviewThread, 'comments'> {
  readonly comments: Connection<ReviewComment>;
}

interface RestFeedback {
  readonly author_association?: null | string;
  readonly body?: null | string;
  readonly created_at?: null | string;
  readonly html_url: string;
  readonly id: number | string;
  readonly state?: string;
  readonly submitted_at?: null | string;
  readonly updated_at?: null | string;
  readonly user: Account | null;
}

/** Read GitHub state and perform explicit workflow-owned mutations via gh. */
@injectable()
export class Github implements GithubService {
  readonly #runner: ProcessRunner;

  constructor(@inject(TOKENS.ProcessRunner) runner: ProcessRunner = processRunner) {
    this.#runner = runner;
  }

  /** Decode a GitHub CLI JSON response. */
  async json(...args: string[]): Promise<unknown> {
    const text = await this.text(...args);
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch (error) {
      throw new ValueError(`GitHub CLI returned invalid JSON: ${error instanceof Error ? error.message : String(error)}`, {
        cause: error,
      });
    }
    if (typeof value === 'object' && value !== null && 'errors' in value && isTruthy(value.errors)) {
      throw new ValueError(`GitHub API errors: ${JSON.stringify(value.errors)}`);
    }
    return value;
  }

  /** Resolve the authenticated actor used by the discussion gate. */
  async login(): Promise<string> {
    return ((await this.json('api', 'user')) as Account).login;
  }

  /** Collect failed workflow logs for maintainer diagnosis. */
  async logs(repo: string, runs: readonly ActionsRun[]): Promise<string> {
    const logs: string[] = [];
    for (const run of runs) logs.push(await this.text('run', 'view', String(run.id), '-R', repo, '--log-failed'));
    return logs.join('\n');
  }

  /** Discover all open PRs authored by the configured user, including drafts. */
  async openPrs(repo: string, author: string): Promise<PullRequest[]> {
    const prs = await this.pages<PullRequest>(`repos/${repo}/pulls?state=open&per_page=100`);
    return prs.filter((pr) => pr.user.login.toLowerCase() === author.toLowerCase());
  }

  /** Collect every page of a REST collection. */
  async pages<T>(endpoint: string): Promise<T[]> {
    return ((await this.json('api', '--paginate', '--slurp', endpoint)) as T[][]).flat();
  }

  async pullRequest(repo: string, number: number): Promise<PullRequest> {
    return (await this.json('api', `repos/${repo}/pulls/${String(number)}`)) as PullRequest;
  }

  async reply(repo: string, number: number, item: ReplyTarget, body: string): Promise<void> {
    if (item.kind === 'thread') {
      await this.json('api', 'graphql', '-f', `query=${REPLY}`, '-f', `id=${item.thread_id ?? ''}`, '-f', `body=${body}`);
    } else {
      await this.json('api', `repos/${repo}/issues/${String(number)}/comments`, '-X', 'POST', '-f', `body=${body}`);
    }
  }

  /** Request Copilot review only when explicitly enabled by configuration. */
  async requestCopilot(repo: string, number: number): Promise<void> {
    await this.text('pr', 'edit', String(number), '-R', repo, '--add-reviewer', '@copilot');
  }

  async retry(repo: string, runId: number): Promise<void> {
    await this.text('run', 'rerun', String(runId), '-R', repo, '--failed');
  }

  /** Read a PR, discussion gate inputs, checks, and current-SHA workflow runs. */
  async snapshot(repo: string, number: number): Promise<Snapshot> {
    const pr = await this.pullRequest(repo, number);
    if (pr.state !== 'open') return { checks: [], other: [], pr, runs: [], statuses: [], threads: [] };
    const sha = pr.head.sha;
    const review = (await this.json(
      'pr',
      'view',
      String(number),
      '-R',
      repo,
      '--json',
      'reviewDecision,mergeStateStatus',
    )) as Snapshot['review'];
    const threads = await this.threads(repo, number);
    const other: OtherFeedback[] = [];
    for (const [kind, endpoint] of [
      ['comment', `issues/${String(number)}/comments`],
      ['review', `pulls/${String(number)}/reviews`],
    ] as const) {
      for (const item of await this.pages<RestFeedback>(`repos/${repo}/${endpoint}?per_page=100`)) {
        if (item.state === 'PENDING' || (item.body ?? '').trim() === '') continue;
        other.push({
          author: item.user,
          author_association: item.author_association ?? null,
          body: item.body ?? '',
          created_at: item.submitted_at ?? item.created_at ?? null,
          id: String(item.id),
          kind,
          updated_at: item.updated_at ?? null,
          url: item.html_url,
        });
      }
    }
    const checkPages = (await this.json(
      'api',
      '--paginate',
      '--slurp',
      `repos/${repo}/commits/${sha}/check-runs?per_page=100`,
    )) as { check_runs: Check[] }[];
    const statuses = await this.pages<CommitStatus>(`repos/${repo}/commits/${sha}/statuses?per_page=100`);
    const runPages = (await this.json(
      'api',
      '--paginate',
      '--slurp',
      `repos/${repo}/actions/runs?head_sha=${sha}&per_page=100`,
    )) as { workflow_runs: ActionsRun[] }[];
    return {
      checks: checkPages.flatMap((page) => page.check_runs),
      other,
      pr,
      review,
      runs: runPages.flatMap((page) => page.workflow_runs).filter((run) => run.head_sha === sha),
      statuses,
      threads,
    };
  }

  /** Execute one bounded GitHub CLI command. */
  async text(...args: string[]): Promise<string> {
    return (await this.#runner.run(['gh', ...args], { check: true, timeoutSec: COMMAND_TIMEOUT_SEC })).stdout;
  }

  /** Read all review threads and all their submitted comments. */
  async threads(repo: string, number: number): Promise<ReviewThread[]> {
    const [owner = '', name = ''] = repo.split('/');
    const result: ReviewThread[] = [];
    let cursor = '';
    for (;;) {
      const response = (await this.json(
        'api',
        'graphql',
        '-f',
        `query=${THREADS}`,
        '-f',
        `owner=${owner}`,
        '-f',
        `name=${name}`,
        '-F',
        `number=${String(number)}`,
        ...(cursor === '' ? [] : ['-f', `cursor=${cursor}`]),
      )) as GraphqlResponse<{ repository: { pullRequest: { reviewThreads: Connection<RawThread> } } }>;
      if (isTruthy(response.errors)) throw new ValueError(`Cannot fetch review threads: ${JSON.stringify(response.errors)}`);
      const connection = response.data.repository.pullRequest.reviewThreads;
      for (const thread of connection.nodes) {
        let comments = thread.comments;
        const nodes = [...comments.nodes];
        while (comments.pageInfo.hasNextPage) {
          const page = (await this.json(
            'api',
            'graphql',
            '-f',
            `query=${COMMENTS}`,
            '-f',
            `id=${thread.id}`,
            '-f',
            `cursor=${comments.pageInfo.endCursor ?? ''}`,
          )) as GraphqlResponse<{ node: { comments: Connection<ReviewComment> } }>;
          if (isTruthy(page.errors)) throw new ValueError(`Cannot fetch thread comments: ${JSON.stringify(page.errors)}`);
          comments = page.data.node.comments;
          nodes.push(...comments.nodes);
        }
        result.push({ ...thread, comments: nodes.filter((comment) => comment.pullRequestReview?.state !== 'PENDING') });
      }
      if (!connection.pageInfo.hasNextPage) return result;
      cursor = connection.pageInfo.endCursor ?? '';
    }
  }
}

/** Produce a stable key for remote work and blocked-run recovery. */
export function digest(value: unknown): string {
  return createHash('sha256').update(encodeSortedCompact(value)).digest('hex');
}

/** Identify changed review and CI inputs without scheduler telemetry. */
export function remoteFingerprint(snapshot: Snapshot): string {
  return digest({
    checks: snapshot.checks.map((check) => [check.id, check.status, check.conclusion ?? null]),
    other: snapshot.other,
    runs: snapshot.runs.map((run) => [run.id, run.status, run.conclusion ?? null, run.run_attempt ?? null]),
    sha: snapshot.pr.head.sha,
    state: snapshot.pr.state,
    statuses: snapshot.statuses.map((status) => [status.id, status.state]),
    threads: snapshot.threads,
  });
}

/** Accept repository collaborators, the operator, and known review automation. */
export function trusted(
  comment: { author?: Account | null | string; author_association?: null | string; authorAssociation?: null | string },
  login: string,
): boolean {
  const author = comment.author;
  const name = (typeof author === 'string' ? author : (author?.login ?? '')) || '';
  const association = comment.authorAssociation ?? comment.author_association;
  return (
    name === login ||
    (typeof association === 'string' && TRUSTED.has(association)) ||
    BOTS.has(name.toLowerCase().replace(/\[bot\]$/, ''))
  );
}

/** Python truthiness of a decoded JSON value, which is how the GitHub error fields were checked. */
function isTruthy(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object' && value !== null) return Object.keys(value).length > 0;
  return Boolean(value);
}
