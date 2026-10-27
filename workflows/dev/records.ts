/** Work items, repository identities, and outcomes used by dev. */
import type { AgentWorkspace } from '../../src/loop/index.ts';

export interface PullRequest {
  readonly body: string;
  readonly branch: string;
  readonly number: number;
  readonly title: string;
  readonly url: string;
}

/** One repository's checkout, worktree directory, and GitHub identity. */
export interface Repository {
  readonly checkout: string;
  readonly ownerName: RepositoryId;
  readonly worktrees: string;
}

/** The response identifier and required commit-subject prefix for one Ticket. */
export class AgentCommitSubject {
  readonly initiative: string;
  readonly ticketNumber: number;
  /** The prefix shared by all delivering commits for this Initiative. */
  get initiativeCommitPrefix(): string {
    return `ccode(${this.initiative}|`;
  }

  constructor(initiative: string, ticketNumber: number) {
    this.initiative = initiative;
    this.ticketNumber = ticketNumber;
    Object.freeze(this);
  }

  toString(): string {
    return `${this.initiative}|${String(this.ticketNumber)}`;
  }

  /** The required prefix of a Ticket's delivering commit subject. */
  toSubject(): string {
    return `ccode(${this.toString()}): `;
  }
}

export class Commit {
  readonly body: string;
  readonly sha: string;
  readonly subject: string;

  constructor(sha: string, subject: string, body = '') {
    this.sha = sha;
    this.subject = subject;
    this.body = body;
    Object.freeze(this);
  }
}

/** GitHub owner and repository name, represented as an owner/repo slug. */
export class RepositoryId {
  readonly owner: string;
  readonly repo: string;

  constructor(owner: string, repo: string) {
    this.owner = owner;
    this.repo = repo;
    Object.freeze(this);
  }

  toString(): string {
    return `${this.owner}/${this.repo}`;
  }
}

/** Names an agent session for its source Spec. */
export function loopSessionName(number: number, title: string): string {
  return `loop(${String(number)}) ${title.split(/\s+/).filter(Boolean).join(' ')}`;
}

export const HITL_LABEL = 'hitl';

const BLOCKING_LABELS: ReadonlySet<string> = new Set([HITL_LABEL, 'spec']);
const INITIATIVE = /^(?<initiative>[^:]+):\s*(?<title>.+)$/;
const TARGET_PREFIX = 'repo:target:';
const BASE_PREFIX = 'repo:base:';
const SLUG = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
const VERSION = /(\d+(?:\.\d+)+)/;

export type AgentWorkflow = (context: TicketContext, workspace: AgentWorkspace) => Promise<TicketResult>;

/** A change made to a Spec or its Tickets that the tracker has yet to persist, in the order GitHub must see it. */
export type Change =
  | { readonly body: string; readonly kind: 'comment'; readonly number: number }
  | { readonly comment: string; readonly kind: 'close'; readonly number: number }
  | { readonly kind: 'label'; readonly label: string; readonly number: number };

/** The published pull request and its captured per-file diff, supplied to one chore agent run. */
export interface ChoreContext {
  readonly baseBranch: string;
  readonly branchCommits?: readonly string[];
  readonly diffDir: string;
  readonly featureBranch: string;
  readonly pullRequestUrl: string;
  readonly sessionName?: null | string;
}

/** The chore agent's proposed commit message and the findings it left alone; both may be empty. */
export interface ChoreResult {
  readonly commitMessage?: string;
  readonly outOfScope?: readonly string[];
  readonly summary?: string;
}

export type ChoreWorkflow = (context: ChoreContext, workspace: AgentWorkspace) => Promise<ChoreResult>;

export interface Comment {
  readonly author: string;
  readonly body: string;
  readonly createdAt: string;
}

export interface PlanContext {
  readonly baseBranch: string;
  readonly sessionName?: null | string;
  readonly sourceRoot?: null | string;
  readonly spec: Spec;
}

/** One Ticket the planner proposes; `blockedBy` holds zero-based indexes of earlier planned Tickets. */
export interface PlannedTicket {
  readonly blockedBy?: readonly number[];
  readonly body: string;
  readonly title: string;
}

/** The planned Tickets in dependency order, or why the Spec cannot be planned. */
export interface PlanResult {
  readonly reason?: null | string;
  readonly status: string;
  readonly tickets?: readonly PlannedTicket[];
}

export type PlanWorkflow = (context: PlanContext, workspace: AgentWorkspace) => Promise<PlanResult>;

export interface SpecInit {
  body?: string;
  comments?: readonly Comment[];
  heldTickets?: readonly number[];
  labels: readonly string[];
  number: number;
  subIssueTotal?: number;
  /** Open Tickets ready for delivery, in order; they live on the harness tracker even when the Spec targets another repo. */
  tickets?: readonly Ticket[];
  title: string;
  url: string;
}

/** The selected Ticket and branch history supplied to one agent-workflow attempt. */
export interface TicketContext {
  readonly baseBranch: string;
  readonly featureBranch: string;
  readonly identifier: AgentCommitSubject;
  readonly initiativeCommits: readonly string[];
  readonly ticket: Ticket;
}

export interface TicketInit {
  body?: string;
  comments?: readonly Comment[];
  labels: readonly string[];
  number: number;
  state: string;
  title: string;
  url: string;
}

/** A delegate's completed commit, summary and verification, or its failure reason. */
export interface TicketResult {
  readonly commit?: null | string;
  readonly identifier: string;
  readonly reason?: null | string;
  readonly status: string;
  readonly summary?: null | string;
  readonly verification?: null | string;
}

/** Aggregate root: its Tickets change only through it, and `TicketsTracker.updateSpec` persists the changes. */
export class Spec {
  body: string;
  comments: readonly Comment[];
  heldTickets: readonly number[];
  labels: readonly string[];
  number: number;
  subIssueTotal: number;
  tickets: readonly Ticket[];
  title: string;
  url: string;

  /** Carries the `hitl` label. */
  get awaitingHuman(): boolean {
    return this.labels.includes(HITL_LABEL);
  }
  /** The title without its Initiative prefix. */
  get bareTitle(): string {
    const match = INITIATIVE.exec(this.title);
    return match?.groups === undefined ? this.title : (match.groups.title ?? this.title).trim();
  }
  /** The single `repo:base:<branch>` label's value, or null when missing/empty/duplicated. */
  get baseBranch(): null | string {
    const values = this.labels.filter((label) => label.startsWith(BASE_PREFIX)).map((label) => label.slice(BASE_PREFIX.length));
    const [value] = values;
    return values.length === 1 && value !== undefined && value !== '' ? value : null;
  }
  /** `<version_with_underscores>_<title slug>` when baseBranch carries a version, else `<title slug>`; null without a baseBranch. */
  get featureBranch(): null | string {
    const baseBranch = this.baseBranch;
    if (baseBranch === null) {
      return null;
    }
    const slug = slugify(this.bareTitle);
    const version = VERSION.exec(baseBranch)?.[1];
    return version === undefined ? slug : `${version.replaceAll('.', '_')}_${slug}`;
  }
  /** Has at least one Ticket ready for delivery. */
  get hasWork(): boolean {
    return this.tickets.some((ticket) => ticket.actionable);
  }
  /** The `<initiative>: <title>` title prefix, or the Spec number when absent. */
  get initiative(): string {
    const match = INITIATIVE.exec(this.title);
    return match?.groups === undefined ? String(this.number) : (match.groups.initiative ?? '').trim();
  }
  /** Has no sub-issues or locally supplied Tickets. */
  get needsPlan(): boolean {
    return this.subIssueTotal === 0 && this.tickets.length === 0;
  }
  /** Changes not yet persisted, oldest first. */
  get pendingChanges(): readonly Change[] {
    return [...this.#changes];
  }
  /** The single `repo:target:<owner/name>` label's value, or null when missing/malformed/duplicated. */
  get target(): null | string {
    const values = this.labels.filter((label) => label.startsWith(TARGET_PREFIX)).map((label) => label.slice(TARGET_PREFIX.length));
    const [value] = values;
    return values.length === 1 && value !== undefined && SLUG.test(value) ? value : null;
  }

  readonly #changes: Change[] = [];

  constructor(init: SpecInit) {
    this.number = init.number;
    this.title = init.title;
    this.url = init.url;
    this.labels = init.labels;
    this.body = init.body ?? '';
    this.comments = init.comments ?? [];
    this.tickets = init.tickets ?? [];
    this.subIssueTotal = init.subIssueTotal ?? 0;
    this.heldTickets = init.heldTickets ?? [];
  }

  /** Marks the oldest occurrence of `change` as persisted. */
  acknowledge(change: Change): void {
    const index = this.#changes.findIndex((pending) => sameChange(pending, change));
    if (index < 0) {
      throw new Error('change is not pending');
    }
    this.#changes.splice(index, 1);
  }

  /** Tells the Spec that all its Tickets are delivered and where the draft PR is. */
  announceDelivered(pullRequestUrl: string): void {
    this.comment(`dev: all Tickets delivered; draft pull request: ${pullRequestUrl}`);
  }

  /** Hands the Spec to a human, but only when there is work it is blocking. */
  block(message: string): void {
    if (this.hasWork) {
      this.handToHuman(message);
    }
  }

  /** Closes a delivered Ticket with `comment`. */
  closeTicket(ticketNumber: number, comment: string): void {
    this.#ticket(ticketNumber).state = 'closed';
    this.#changes.push({ comment, kind: 'close', number: ticketNumber });
  }

  /** Comments `message` on the Spec. */
  comment(message: string): void {
    this.#changes.push({ body: message, kind: 'comment', number: this.number });
  }

  /** Hands both the Ticket and the Spec to a human. */
  escalate(ticketNumber: number, reason: string): void {
    const message = `dev: ${reason}`;
    const ticket = this.#ticket(ticketNumber);
    ticket.labels = withLabel(ticket.labels, HITL_LABEL);
    this.#changes.push(
      { kind: 'label', label: HITL_LABEL, number: ticketNumber },
      { body: message, kind: 'comment', number: ticketNumber },
    );
    this.handToHuman(message);
  }

  /** Labels the Spec `hitl` and comments `message` on it. */
  handToHuman(message: string): void {
    this.labels = withLabel(this.labels, HITL_LABEL);
    this.#changes.push(
      { kind: 'label', label: HITL_LABEL, number: this.number },
      { body: message, kind: 'comment', number: this.number },
    );
  }

  #ticket(number: number): Ticket {
    const ticket = this.tickets.find((candidate) => candidate.number === number);
    if (ticket === undefined) {
      throw new RangeError(`ticket #${String(number)} does not belong to spec #${String(this.number)}`);
    }
    return ticket;
  }
}

export class Ticket {
  body: string;
  comments: readonly Comment[];
  labels: readonly string[];
  number: number;
  state: string;
  title: string;
  url: string;

  /** Open and carrying neither the `hitl` nor the `spec` label. */
  get actionable(): boolean {
    return this.state === 'open' && !this.labels.some((label) => BLOCKING_LABELS.has(label.toLowerCase()));
  }

  constructor(init: TicketInit) {
    this.number = init.number;
    this.title = init.title;
    this.state = init.state;
    this.labels = init.labels;
    this.url = init.url;
    this.body = init.body ?? '';
    this.comments = init.comments ?? [];
  }
}

/** An expected failure in ticket delivery or its agent delegate. */
export class TicketWorkflowError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'TicketWorkflowError';
  }
}

export function slugify(text: string): string {
  const slug = text
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/g, '-')
    .replaceAll(/^-+|-+$/g, '');
  return slug === '' ? 'spec' : slug;
}

function sameChange(left: Change, right: Change): boolean {
  const entries = Object.entries(left);
  return entries.length === Object.keys(right).length && entries.every(([key, value]) => Object.entries(right).some(([other, candidate]) => other === key && candidate === value));
}

function withLabel(labels: readonly string[], label: string): readonly string[] {
  return labels.includes(label) ? labels : [...labels, label];
}

export const VERDICTS = ['pass', 'partial', 'fail', 'inconclusive'] as const;

export interface ReviewAxis {
  readonly name: string;
  readonly note?: string;
  readonly outcome: string;
}

export interface ReviewContext {
  readonly headSha: string;
  readonly initiative: string;
  readonly pullRequestUrl: string;
  readonly sessionName?: null | string;
  readonly sourceRoot: string;
}

/** What the verifier needs to drive the app on the integrated branch. */
export interface VerifyContext {
  readonly baseBranch: string;
  readonly branchCommits?: readonly string[];
  readonly evidenceDir: string;
  readonly featureBranch: string;
  readonly pullRequestUrl: string;
  readonly sessionName?: null | string;
  readonly skills: readonly string[];
  readonly specJson: string;
  readonly ticketsJson: string;
}

export interface VerifyFinding {
  readonly reason?: string;
  readonly status: string;
  readonly summary: string;
}

/** The verifier's verdict, findings and proposed message for the single fix commit the harness may make. */
export interface VerifyResult {
  readonly commitMessage?: string;
  readonly findings?: readonly VerifyFinding[];
  readonly summary?: string;
  readonly verdict: string;
  readonly verification?: string;
}

export type VerifyWorkflow = (context: VerifyContext, workspace: AgentWorkspace) => Promise<VerifyResult>;

/** Findings of these kinds keep the pull request a draft; quality and smell findings are only reported. */
export const BLOCKING_FINDING_KINDS: ReadonlySet<string> = new Set(['bug', 'initiative']);

const REVIEW_AXES: readonly string[] = ['bugs', 'quality', 'smells', 'initiative'];

export interface ReviewFinding {
  readonly axis?: string;
  readonly kind: string;
  readonly summary: string;
}

export interface ReviewResultInit {
  axes?: readonly ReviewAxis[];
  findings?: readonly ReviewFinding[];
  headSha?: string;
  posted?: readonly string[];
}

export type ReviewWorkflow = (context: ReviewContext, workspace: AgentWorkspace) => Promise<ReviewResult>;

export class ReviewResult {
  readonly axes: readonly ReviewAxis[];
  readonly findings: readonly ReviewFinding[];
  readonly headSha: string;
  readonly posted: readonly string[];
  get blockingFindings(): readonly ReviewFinding[] {
    return this.findings.filter((finding) => BLOCKING_FINDING_KINDS.has(finding.kind));
  }
  get completed(): boolean {
    const names = new Set(this.axes.map((axis) => axis.name));
    return (
      names.size === REVIEW_AXES.length &&
      REVIEW_AXES.every((name) => names.has(name)) &&
      this.axes.every((axis) => axis.outcome === 'completed')
    );
  }

  constructor(init: ReviewResultInit = {}) {
    this.axes = init.axes ?? [];
    this.findings = init.findings ?? [];
    this.posted = init.posted ?? [];
    this.headSha = init.headSha ?? '';
  }
}
