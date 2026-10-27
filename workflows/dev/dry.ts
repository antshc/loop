/** Dry-run slice of the dev Workflow: a scripted fake agent over in-memory Git and GitHub, so a whole run needs no CLI, repository or network. */
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { GitService } from './checkout.ts';
import type { ExecutionStore } from './journal.ts';
import type { GhRunner, GithubFactory } from './platforms.ts';

import {
  type AgentBuilder,
  type AgentRequest,
  fakeAgent,
  FakeCliRunner,
  FakeReply,
  FakeToolUse,
  getLogger,
  GitCli,
  GitRuntime,
  type RunContext,
  type SessionLogReader,
  Usage,
  ValueError,
} from '../../src/loop/index.ts';
import { runCommand } from './commands.ts';
import { FileExecutionStore } from './journal.ts';
import { GitHubRepo } from './platforms.ts';
import { Commit, RepositoryId } from './records.ts';

const logger = getLogger('workflow.dev');

export const DRY_TARGET = new RepositoryId('antshc', 'loop');
export const DRY_BASE_BRANCH = 'main';

const SPEC_NUMBER = 1;
const TICKET_TITLES: readonly string[] = ['Add discount policy', 'Apply discount at checkout'];
const TICKET_NUMBERS: readonly number[] = TICKET_TITLES.map((_, index) => 10 + index);
const SUBJECT = /subject `(?<prefix>ccode\((?<identifier>[^)|]+\|(?<number>\d+))\): )/;
const TICKET_JSON = /<ticket-json>\n(?<json>.*?)\n<\/ticket-json>/s;
const REVIEW_AXES: readonly string[] = ['bugs', 'quality', 'smells', 'initiative'];

export const SEAMS = ['agent', 'git', 'github', 'store'] as const;
/** Makes a commit with `subject` in `cwd` and resolves to its SHA. */
export type Committer = (cwd: string, subject: string) => Promise<string> | string;

export type Seam = (typeof SEAMS)[number];

/** Stands in for `gh`: canned reads for one Spec with two Tickets; writes are logged, not sent. */
export class DryGh {
  readonly calls: (readonly string[])[] = [];
  readonly #specs: unknown;
  readonly #tickets: unknown;

  constructor() {
    const labels = ['spec', `repo:target:${DRY_TARGET.toString()}`, `repo:base:${DRY_BASE_BRANCH}`];
    this.#specs = [issue(SPEC_NUMBER, 'Basket: Discount policy', labels)];
    this.#tickets = TICKET_TITLES.map((title, index) => issue(TICKET_NUMBERS[index] ?? 0, title, []));
  }

  /** The `gh` seam: resolves to the canned output for `args`. */
  readonly run: GhRunner = (args) => {
    this.calls.push(args);
    const [first, second] = args;
    if (first === 'pr' && second === 'diff') {
      return Promise.resolve('');
    }
    if (first === 'pr' && second === 'ready') {
      return Promise.resolve('');
    }
    if (first === 'pr' && second === 'list') {
      return Promise.resolve('[]');
    }
    if (first === 'pr' && second === 'create') {
      return Promise.resolve(`https://github.com/${DRY_TARGET.toString()}/pull/99`);
    }
    if (first === 'issue') {
      logger.debug(`dry-run: gh ${args.slice(0, 3).join(' ')}`);
      return Promise.resolve('');
    }
    const query = args.find((arg) => arg.startsWith('query=')) ?? '';
    if (query.includes('subIssues')) {
      return Promise.resolve(page({ issue: { subIssues: { nodes: this.#tickets } } }));
    }
    return Promise.resolve(page({ issues: { nodes: this.#specs } }));
  };
}

/** An in-memory history on one branch: a commit list and how much of it `origin` already holds. */
export class DryGit implements GitService {
  readonly commits: Commit[] = [new Commit('base0000', 'base')];
  #pushed = 1;

  branchCommits(): Promise<Commit[]> {
    return Promise.resolve(this.commits.slice(1));
  }

  changedFiles(): Promise<string[]> {
    return Promise.resolve([]);
  }

  commit(_path: string, subject: string, body = ''): Promise<Commit> {
    const commit = new Commit(this.#nextSha(), subject, body);
    this.commits.push(commit);
    return Promise.resolve(commit);
  }

  commitsSince(_path: string, sha: string): Promise<Commit[]> {
    return Promise.resolve(this.commits.slice(this.#index(sha) + 1));
  }

  fetch(path: string): Promise<void> {
    logger.debug(`dry-run: git fetch ${path}`);
    return Promise.resolve();
  }

  head(): Promise<Commit> {
    return Promise.resolve(this.#last());
  }

  initiativeCommits(_path: string, _base: string, prefix: string): Promise<Commit[]> {
    return Promise.resolve(this.commits.slice(1).filter((commit) => commit.subject.startsWith(prefix)));
  }

  isClean(): Promise<boolean> {
    return Promise.resolve(true);
  }

  pushIfAhead(_path: string, branch: string): Promise<boolean> {
    if (this.commits.length <= this.#pushed) {
      return Promise.resolve(false);
    }
    logger.debug(`dry-run: git push origin ${branch}`);
    this.#pushed = this.commits.length;
    return Promise.resolve(true);
  }

  /** Adds a commit and returns its SHA. */
  record(subject: string): string {
    const sha = this.#nextSha();
    this.commits.push(new Commit(sha, subject));
    return sha;
  }

  refSha(): Promise<string> {
    return Promise.resolve(this.commits[this.#pushed - 1]?.sha ?? '');
  }

  remoteBranchExists(): Promise<boolean> {
    return Promise.resolve(true);
  }

  restore(_path: string, sha: string): Promise<void> {
    this.commits.splice(this.#index(sha) + 1);
    return Promise.resolve();
  }

  status(): Promise<string> {
    return Promise.resolve('');
  }

  #index(sha: string): number {
    const index = this.commits.findIndex((commit) => commit.sha === sha);
    if (index < 0) {
      throw new RangeError(`unknown commit ${sha}`);
    }
    return index;
  }

  #last(): Commit {
    const [last] = this.commits.slice(-1);
    if (last === undefined) {
      throw new RangeError('the dry-run history is empty');
    }
    return last;
  }

  #nextSha(): string {
    return `c${String(this.commits.length).padStart(7, '0')}`;
  }
}

/**
 * Replaces any subset of the dev Workflow's external dependencies with fakes; every other seam stays real.
 *
 * agent: the Copilot CLI, scripted. git: the Workflow's Git reads and pushes plus the agent's worktree, in memory.
 * github: `gh`, canned. store: the failure log, in a temporary directory.
 */
export class Fakes {
  readonly seams: ReadonlySet<string>;
  readonly #gh = new DryGh();
  readonly #git = new DryGit();
  readonly #runner: FakeCliRunner;

  constructor(seams: Iterable<string>, options: { rework?: boolean; speed?: number } = {}) {
    this.seams = new Set(seams);
    const unknown = [...this.seams].filter((seam) => !isSeam(seam)).sort();
    if (unknown.length > 0) {
      throw new ValueError(`unknown seam ${unknown.join(', ')}; choose from ${SEAMS.join(', ')}`);
    }
    if (this.seams.has('git') && !this.seams.has('agent')) {
      throw new ValueError('a faked git needs a faked agent: a real agent would commit where the fake git cannot see');
    }
    const commit: Committer = this.seams.has('git') ? (_cwd, subject) => this.#git.record(subject) : commitInWorktree;
    this.#runner = new FakeCliRunner(new ScriptedAgent(commit, options.rework ?? true).respond, {
      speed: options.speed ?? 1,
    });
  }

  /** The in-memory Git when faked, else null. */
  git(): GitService | null {
    return this.seams.has('git') ? this.#git : null;
  }

  /** A factory of the canned GitHub repository when faked, else null. */
  githubFactory(): GithubFactory | null {
    return this.seams.has('github') ? () => new GitHubRepo(DRY_TARGET.owner, DRY_TARGET.repo, this.#gh.run) : null;
  }

  /** Builders over the scripted agent when faked, else null; the worktree is a temporary directory only with a faked git. */
  newAgent(): (() => AgentBuilder) | null {
    if (!this.seams.has('agent')) {
      return null;
    }
    const worktrees = this.seams.has('git') ? null : new GitRuntime(new GitCli());
    return () => fakeAgent(this.#runner, worktrees);
  }

  /** The scripted agent as the session-log reader when faked, else null. */
  readers(): null | readonly SessionLogReader[] {
    return this.seams.has('agent') ? [this.#runner] : null;
  }

  /** A failure log in a fresh temporary directory when faked, else null. */
  store(): ExecutionStore | null {
    return this.seams.has('store') ? new FileExecutionStore(mkdtempSync(join(tmpdir(), 'loop-fake-log-'))) : null;
  }
}

/** Answers each Ticket with one commit made by `commit`; the second Ticket's first attempt fails when `rework`. */
export class ScriptedAgent {
  readonly #attempts = new Map<string, number>();
  readonly #commit: Committer;
  readonly #rework: boolean;

  constructor(commit: Committer, rework = true) {
    this.#commit = commit;
    this.#rework = rework;
  }

  /** The `FakeCliRunner` responder. */
  readonly respond = async (request: AgentRequest, context: RunContext): Promise<FakeReply> => {
    const subject = SUBJECT.exec(request.prompt)?.groups;
    const [prefix, identifier, number] = [subject?.prefix, subject?.identifier, subject?.number];
    if (prefix === undefined || identifier === undefined || number === undefined) {
      return unscripted(request.prompt);
    }
    const attempt = (this.#attempts.get(identifier) ?? 0) + 1;
    this.#attempts.set(identifier, attempt);
    const title = ticketTitle(request.prompt);
    if (this.#rework && Number(number) === TICKET_NUMBERS[1] && attempt === 1) {
      return failed(identifier, `dry-run: tests for ${pythonRepr(title)} failed`);
    }
    const sha = await this.#commit(context.agent.cwd, `${prefix}${title}`);
    return completed(identifier, sha, title, Number(number));
  };
}

/** Makes a real empty commit in `cwd` and resolves to its SHA; for a fake agent over real Git. */
export async function commitInWorktree(cwd: string, subject: string): Promise<string> {
  await runCommand(['git', 'commit', '--allow-empty', '-m', subject], { cwd });
  return (await runCommand(['git', 'rev-parse', 'HEAD'], { cwd })).trim();
}

/** A Python `json.dumps` of the value: `, ` and `: ` separators, non-ASCII escaped, keys in insertion order. */
export function pythonJson(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'number') {
    return JSON.stringify(value);
  }
  if (typeof value === 'string') {
    return JSON.stringify(value).replaceAll(/[\u007f-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  }
  if (Array.isArray(value)) {
    return `[${value.map(pythonJson).join(', ')}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>).map(([key, item]) => `${pythonJson(key)}: ${pythonJson(item)}`);
  return `{${entries.join(', ')}}`;
}

function answer(identifier: string, status: string, result: unknown, narration: string): string {
  const envelope = pythonJson(
    ordered([
      ['identifier', identifier],
      ['status', status],
      ['result', result],
    ]),
  );
  return `${narration}\n\n\`\`\`json\n${envelope}\n\`\`\``;
}

function completed(identifier: string, sha: string, title: string, number: number): FakeReply {
  const result = ordered([
    ['commit', sha],
    ['summary', title],
    ['verification', 'dry-run: dotnet test -> 12 passed'],
  ]);
  return new FakeReply(answer(identifier, 'completed', result, `Implemented ${title}.`), {
    tools: [
      new FakeToolUse('view', { arguments: { path: 'src/Basket.cs' }, result: 'public class Basket { }', seconds: 0.3 }),
      new FakeToolUse('edit', { arguments: { path: 'src/Discount.cs' }, result: 'updated', seconds: 0.6 }),
      new FakeToolUse('bash', { arguments: { command: 'dotnet test' }, result: '12 passed', seconds: 1.5 }),
    ],
    usage: usage(number),
  });
}

function envelope(status: string, result: unknown): string {
  return pythonJson(
    ordered([
      ['status', status],
      ['result', result],
    ]),
  );
}

function failed(identifier: string, reason: string): FakeReply {
  return new FakeReply(answer(identifier, 'failed', ordered([['reason', reason]]), 'The checks failed.'), {
    tools: [new FakeToolUse('bash', { arguments: { command: 'dotnet test' }, ok: false, result: '1 failed', seconds: 1.2 })],
    usage: usage(1),
  });
}

function isSeam(seam: string): seam is Seam {
  return (SEAMS as readonly string[]).includes(seam);
}

function issue(number: number, title: string, labels: readonly string[]): unknown {
  return ordered([
    ['number', number],
    ['title', title],
    ['url', `https://github.com/${DRY_TARGET.toString()}/issues/${String(number)}`],
    ['state', 'OPEN'],
    ['body', `Dry-run body of #${String(number)}: ${title}.`],
    ['labels', { nodes: labels.map((name) => ({ name })) }],
    ['comments', { nodes: [] }],
  ]);
}

/** An object whose keys keep the order given, which sorted object literals cannot promise. */
function ordered(entries: readonly (readonly [string, unknown])[]): Record<string, unknown> {
  return Object.fromEntries(entries);
}

function page(repository: unknown): string {
  return pythonJson([{ data: { repository } }]);
}

/** The text as Python's `repr` quotes a string. */
function pythonRepr(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  return `${quote}${text.replaceAll('\\', '\\\\').replaceAll(quote, `\\${quote}`)}${quote}`;
}

function ticketTitle(prompt: string): string {
  const json = TICKET_JSON.exec(prompt)?.groups?.json;
  return json === undefined ? 'ticket' : (JSON.parse(json) as { title: string }).title;
}

function unscripted(prompt: string): FakeReply {
  if (prompt.startsWith('# TASK')) {
    const result = ordered([
      ['summary', 'No cleanup needed'],
      ['out_of_scope', []],
    ]);
    return new FakeReply(envelope('completed', result));
  }
  if (prompt.startsWith('# Review')) {
    const head = /head is commit\s+`([^`]+)`/.exec(prompt)?.[1] ?? '';
    const result = ordered([
      ['axes', REVIEW_AXES.map((name) => ordered([['name', name], ['outcome', 'completed']]))],
      ['head', head],
      ['findings', []],
    ]);
    return new FakeReply(envelope('completed', result));
  }
  return new FakeReply(envelope('failed', ordered([['reason', 'No fake response configured']])));
}

function usage(seed: number): Usage {
  return new Usage({
    aiu: { input: 0.4 + 0.05 * seed, output: 0.9 + 0.1 * seed },
    apiCalls: 3 + (seed % 3),
    cacheReadTokens: 12_000,
    inputTokens: 4000 + 500 * seed,
    outputTokens: 900 + 100 * seed,
    premiumRequests: 0.33,
  });
}
