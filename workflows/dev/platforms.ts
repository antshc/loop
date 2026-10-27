/** GitHub access, work tracking, and pull requests for dev. */
import { type CliCall, cliRunner, CommandError, runCommand } from './commands.ts';
import {
  type Change,
  type Comment,
  type PlannedTicket,
  type PullRequest,
  type Repository,
  Spec,
  Ticket,
  TicketWorkflowError,
} from './records.ts';

const SLUG_REMOTE = /github\.com[:/](?<owner>[^/]+)\/(?<repo>[^/]+?)(?:\.git)?\/?$/;

/** Runs `gh <args>` and resolves to its stdout. */
export type GhRunner = CliCall;

/** Resolves the repository client for a checkout; replaceable so a run needs no network. */
export type GithubFactory = (checkout: string) => GitHubRepo;

export interface GraphqlOptions {
  paginate?: boolean;
  /** Also passes the `$owner`/`$repo` variables. Default true. */
  scoped?: boolean;
  variables?: Readonly<Record<string, number | string>>;
}

interface PullRequestNode {
  body?: null | string;
  headRefName: string;
  number: number;
  title: string;
  url: string;
}

/** One GitHub repository reached through `gh`; the shared seam under the issue and pull-request clients. */
export class GitHubRepo {
  readonly name: string;
  readonly owner: string;
  /** `owner/name`, as `gh --repo` expects it. */
  get slug(): string {
    return `${this.owner}/${this.name}`;
  }
  readonly #gh: GhRunner;

  constructor(owner: string, name: string, gh: GhRunner) {
    this.owner = owner;
    this.name = name;
    this.#gh = gh;
  }

  /** The repo behind `checkout`'s `origin` remote; rejects with ValueError-like RangeError when it is not a GitHub remote. */
  static async fromOrigin(checkout: string): Promise<GitHubRepo> {
    const url = (await runCommand(['git', 'remote', 'get-url', 'origin'], { cwd: checkout })).trim();
    const groups = SLUG_REMOTE.exec(url)?.groups;
    if (groups?.owner === undefined || groups.repo === undefined) {
      throw new RangeError(`unsupported remote: ${url}`);
    }
    return new GitHubRepo(groups.owner, groups.repo, ghCli(checkout));
  }

  /** Runs a GraphQL query. */
  graphql(query: string, options: GraphqlOptions = {}): Promise<string> {
    const args = ['api', 'graphql'];
    if (options.paginate === true) {
      args.push('--paginate', '--slurp');
    }
    args.push('-f', `query=${query}`);
    if (options.scoped !== false) {
      args.push('-f', `owner=${this.owner}`, '-f', `repo=${this.name}`);
    }
    for (const [name, value] of Object.entries(options.variables ?? {})) {
      args.push(typeof value === 'number' ? '-F' : '-f', `${name}=${String(value)}`);
    }
    return this.#gh(args);
  }

  /** Runs `gh <args>` and resolves to its stdout. */
  run(args: readonly string[]): Promise<string> {
    return this.#gh(args);
  }
}

/** Creates draft PRs or reuses open PRs in one repository through `gh`. */
export class PullRequestClient {
  readonly #repo: GitHubRepo;

  constructor(repo: GitHubRepo) {
    this.#repo = repo;
  }

  /** One draft PR per head branch: resolves to the open PR for `head` when there is one, else creates it. */
  async createDraftPullRequest(head: string, base: string, title: string, body = ''): Promise<PullRequest> {
    const existing = await this.#findPullRequest(head);
    if (existing !== null) {
      if (body !== '' && existing.body !== body) {
        await this.#repo.run(['pr', 'edit', String(existing.number), '--repo', this.#repo.slug, '--body', body]);
        return { ...existing, body };
      }
      return existing;
    }
    const output = await this.#repo.run([
      'pr',
      'create',
      '--repo',
      this.#repo.slug,
      '--head',
      head,
      '--base',
      base,
      '--title',
      title,
      '--body',
      body,
      '--draft',
    ]);
    const number = /\/pull\/(\d+)/.exec(output.trim())?.[1];
    return { body, branch: head, number: number === undefined ? 0 : Number(number), title, url: output.trim() };
  }

  /** Takes a PR out of draft; `pullRequest` is its number or URL. */
  async markReady(pullRequest: string): Promise<void> {
    await this.#repo.run(['pr', 'ready', pullRequest, '--repo', this.#repo.slug]);
  }

  async #findPullRequest(headBranch: string): Promise<null | PullRequest> {
    const output = await this.#repo.run([
      'pr',
      'list',
      '--repo',
      this.#repo.slug,
      '--state',
      'open',
      '--head',
      headBranch,
      '--json',
      'number,title,url,headRefName,body',
    ]);
    const [pr] = JSON.parse(output) as PullRequestNode[];
    if (pr === undefined) {
      return null;
    }
    return { body: pr.body ?? '', branch: pr.headRefName, number: pr.number, title: pr.title, url: pr.url };
  }
}

/** Wraps one `gh` invocation; the only place `gh` is started. */
export function ghCli(cwd?: string): GhRunner {
  return cliRunner('gh', cwd);
}

const ISSUE_FIELDS =
  'number title url state body labels(first: 20) { nodes { name } }' +
  ' comments(first: 50) { nodes { author { login } body createdAt } }';

const SPECS_QUERY =
  'query($owner: String!, $repo: String!) {' +
  ' repository(owner: $owner, name: $repo) {' +
  `  issues(first: 100, states: OPEN, labels: ["spec"]) { nodes { ${ISSUE_FIELDS} } }` +
  ' } }';

const SUB_ISSUES_QUERY =
  'query($owner: String!, $repo: String!, $number: Int!) {' +
  ' repository(owner: $owner, name: $repo) {' +
  `  issue(number: $number) { subIssues(first: 100) { nodes { ${ISSUE_FIELDS} } } }` +
  ' } }';

interface IssueNode {
  body: string;
  comments: { nodes: { author: null | { login?: string }; body: string; createdAt: string }[] };
  labels: { nodes: { name: string }[] };
  number: number;
  state: string;
  title: string;
  url: string;
}

interface SpecsPage {
  data: { repository: { issues: { nodes: IssueNode[] } } };
}

interface SubIssuesPage {
  data: { repository: { issue: { subIssues: { nodes: IssueNode[] } } } };
}

/** The harness repo's Spec/Ticket tracker: loads Specs through `gh` and persists the changes made to them. */
export class TicketsTracker {
  readonly #gh: GhRunner;
  readonly #name: string;
  readonly #owner: string;

  constructor(owner: string, name: string, gh: GhRunner) {
    this.#owner = owner;
    this.#name = name;
    this.#gh = gh;
  }

  /**
   * Creates each planned Ticket as an open sub-issue of `spec`, in order, and adds them to `spec.tickets`.
   * `blockedBy` indexes become `Blocked by #N` lines in the body. A failure rejects with `TicketWorkflowError`
   * naming the sub-issues already created, which stay on the tracker.
   */
  async createTickets(spec: Spec, planned: Iterable<PlannedTicket>): Promise<Ticket[]> {
    const slug = `${this.#owner}/${this.#name}`;
    const created: Ticket[] = [];
    try {
      for (const plannedTicket of planned) {
        const body = withBlockers(
          plannedTicket,
          created.map((ticket) => ticket.number),
        );
        const output = (
          await this.#gh(['issue', 'create', '--repo', slug, '--title', plannedTicket.title, '--body', body])
        ).trim();
        const found = /\/issues\/(\d+)/.exec(output)?.[1];
        if (found === undefined) {
          throw new TicketWorkflowError(`cannot read the number of the created issue from ${pythonRepr(output)}`);
        }
        const number = Number(found);
        const issueId = (await this.#gh(['api', `repos/${slug}/issues/${String(number)}`, '--jq', '.id'])).trim();
        await this.#gh([
          'api',
          '--method',
          'POST',
          `repos/${slug}/issues/${String(spec.number)}/sub_issues`,
          '-F',
          `sub_issue_id=${issueId}`,
        ]);
        created.push(new Ticket({ body, labels: [], number, state: 'open', title: plannedTicket.title, url: output }));
      }
    } catch (error) {
      if (error instanceof CommandError || error instanceof TicketWorkflowError) {
        const made = created.map((ticket) => `#${String(ticket.number)}`).join(', ') || 'none';
        throw new TicketWorkflowError(`creating Tickets failed after creating ${made}: ${error.message}`, {
          cause: error,
        });
      }
      throw error;
    }
    spec.tickets = [...spec.tickets, ...created];
    spec.subIssueTotal += created.length;
    return created;
  }

  /** Every open Spec on the harness tracker, each with its deliverable Tickets. */
  async specs(): Promise<Spec[]> {
    const specs: Spec[] = [];
    for (const node of await this.#specIssues()) {
      const tickets = (await this.#subIssues(node.number)).map(ticketOf);
      const actionable = tickets.filter((ticket) => ticket.actionable);
      const held = tickets.filter((ticket) => ticket.state === 'open' && !ticket.actionable).map((ticket) => ticket.number);
      specs.push(specOf(node, actionable, tickets.length, held));
    }
    return specs;
  }

  /** Persists the Spec's and its Tickets' pending changes in order; a failed change stays pending. */
  async updateSpec(spec: Spec): Promise<void> {
    for (const change of spec.pendingChanges) {
      await this.#apply(change);
      spec.acknowledge(change);
    }
  }

  async #apply(change: Change): Promise<void> {
    const slug = `${this.#owner}/${this.#name}`;
    const number = String(change.number);
    switch (change.kind) {
      case 'close':
        await this.#gh(['issue', 'close', number, '--repo', slug, '--comment', change.comment]);
        break;
      case 'comment':
        await this.#gh(['issue', 'comment', number, '--repo', slug, '--body', change.body]);
        break;
      case 'label':
        await this.#gh(['issue', 'edit', number, '--repo', slug, '--add-label', change.label]);
        break;
    }
  }

  #graphql(query: string, number?: number): Promise<string> {
    const args = ['api', 'graphql', '--paginate', '--slurp', '-f', `query=${query}`];
    args.push('-f', `owner=${this.#owner}`, '-f', `repo=${this.#name}`);
    if (number !== undefined) {
      args.push('-F', `number=${String(number)}`);
    }
    return this.#gh(args);
  }

  async #specIssues(): Promise<IssueNode[]> {
    const pages = JSON.parse(await this.#graphql(SPECS_QUERY)) as SpecsPage[];
    return pages.flatMap((page) => page.data.repository.issues.nodes);
  }

  async #subIssues(number: number): Promise<IssueNode[]> {
    const pages = JSON.parse(await this.#graphql(SUB_ISSUES_QUERY, number)) as SubIssuesPage[];
    return pages.flatMap((page) => page.data.repository.issue.subIssues.nodes);
  }
}

/** Creates the repository client, using the injected factory when supplied. */
export function githubFor(repository: Repository, factory: GithubFactory | null): GitHubRepo {
  if (factory !== null) {
    return factory(repository.checkout);
  }
  return new GitHubRepo(repository.ownerName.owner, repository.ownerName.repo, ghCli(repository.checkout));
}

function commentsOf(node: IssueNode): Comment[] {
  // `author` is null for deleted accounts.
  return node.comments.nodes.map((comment) => ({
    author: comment.author?.login ?? 'ghost',
    body: comment.body,
    createdAt: comment.createdAt,
  }));
}

function labelsOf(node: IssueNode): string[] {
  return node.labels.nodes.map((label) => label.name);
}

/** The text as Python's `repr` quotes a string, so messages persisted by the Python Workflow keep their form. */
function pythonRepr(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  const escaped = text
    .replaceAll('\\', '\\\\')
    .replaceAll('\n', '\\n')
    .replaceAll('\r', '\\r')
    .replaceAll('\t', '\\t')
    .replaceAll(quote, `\\${quote}`);
  return `${quote}${escaped}${quote}`;
}

function specOf(node: IssueNode, tickets: readonly Ticket[], total = 0, held: readonly number[] = []): Spec {
  return new Spec({
    body: node.body,
    comments: commentsOf(node),
    heldTickets: held,
    labels: labelsOf(node),
    number: node.number,
    subIssueTotal: total,
    tickets,
    title: node.title,
    url: node.url,
  });
}

function ticketOf(node: IssueNode): Ticket {
  return new Ticket({
    body: node.body,
    comments: commentsOf(node),
    labels: labelsOf(node),
    number: node.number,
    state: node.state.toLowerCase(),
    title: node.title,
    url: node.url,
  });
}

function withBlockers(planned: PlannedTicket, earlier: readonly number[]): string {
  const blockers = (planned.blockedBy ?? [])
    .filter((index) => index >= 0 && index < earlier.length)
    .map((index) => `#${String(earlier[index])}`);
  return blockers.length > 0 ? `${planned.body.trimEnd()}\n\nBlocked by ${blockers.join(', ')}` : planned.body;
}
