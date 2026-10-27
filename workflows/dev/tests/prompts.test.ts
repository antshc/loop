import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { ChoreContext, PlanContext, VerifyContext } from '../records.ts';

import { choreArgs, loadPrompt, planArgs, PROMPT, promptArgs, PromptError, renderPrompt, verifyArgs } from '../agent.ts';
import { AgentCommitSubject, Spec, Ticket } from '../records.ts';
import { testDirectory } from './support/harness.ts';

interface Case {
  args: Record<string, string>;
  branchCommits?: string[];
  commits?: string[];
  rendered: string;
  skills?: string[];
  specJson?: string;
  template: string;
  ticketsJson?: string;
}

interface CommentFixture {
  author: string;
  body: string;
  createdAt: string;
}

interface Golden {
  bareTicket: TicketFixture;
  cases: Record<string, Case>;
  spec: TicketFixture;
  ticket: TicketFixture;
}

interface TicketFixture {
  body: string;
  comments: CommentFixture[];
  labels: string[];
  number: number;
  state: string;
  title: string;
  url: string;
}

/** Rendered by the Python Workflow from the same inputs, with the templates this Workflow still reads. */
const GOLDEN = JSON.parse(readFileSync(join(import.meta.dir, 'fixtures', 'prompts.json'), 'utf8')) as Golden;

const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

let directory: string;

beforeEach(() => {
  directory = testDirectory('dev-prompts-');
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

const ticketOf = (fixture: TicketFixture): Ticket => new Ticket(fixture);

function golden(name: string): Case {
  const found = GOLDEN.cases[name];
  if (found === undefined) {
    throw new Error(`no golden prompt ${name}`);
  }
  return found;
}

function specOf(): Spec {
  const { bareTicket, spec, ticket } = GOLDEN;
  return new Spec({ ...spec, tickets: [ticketOf(ticket), ticketOf(bareTicket)] });
}

describe('the named prompts render byte for byte as the Python Workflow rendered them', () => {
  test('implement, with Initiative commits', () => {
    const expected = golden('implement');
    const args = promptArgs(
      ticketOf(GOLDEN.ticket),
      new AgentCommitSubject('Checkout', 10),
      expected.commits ?? [],
      '/w/tree',
      'release/2.4',
      '2_4_add-login-page',
    );

    expect(args).toEqual(expected.args);
    expect(renderPrompt(loadPrompt(expected.template), args)).toBe(expected.rendered);
  });

  test('implement, without Initiative commits', () => {
    const expected = golden('implementNoCommits');
    const args = promptArgs(ticketOf(GOLDEN.bareTicket), new AgentCommitSubject('Checkout', 11), [], '/w/tree', 'main', 'add-login-page');

    expect(args).toEqual(expected.args);
    expect(renderPrompt(loadPrompt(expected.template), args)).toBe(expected.rendered);
  });

  test('plan', () => {
    const expected = golden('plan');
    const context: PlanContext = { baseBranch: 'release/2.4', sourceRoot: '/w/checkout', spec: specOf() };
    const args = planArgs(context, '/w/checkout');

    expect(args).toEqual(expected.args);
    expect(renderPrompt(loadPrompt(expected.template), args)).toBe(expected.rendered);
  });

  test.each(['chore', 'choreNoCommits'])('%s', (name) => {
    const expected = golden(name);
    const context: ChoreContext =
      name === 'chore'
        ? {
            baseBranch: 'release/2.4',
            branchCommits: expected.branchCommits,
            diffDir: '/w/tree/bin/review_diff',
            featureBranch: '2_4_add-login-page',
            pullRequestUrl: 'https://github.com/owner/repo/pull/99',
          }
        : { baseBranch: 'main', diffDir: '/d', featureBranch: 'f', pullRequestUrl: 'https://github.com/owner/repo/pull/99' };
    const args = choreArgs(context, '/w/tree');

    expect(args).toEqual(expected.args);
    expect(renderPrompt(loadPrompt(expected.template), args)).toBe(expected.rendered);
  });

  test('verify', () => {
    const expected = golden('verify');
    const context: VerifyContext = {
      baseBranch: 'release/2.4',
      branchCommits: expected.branchCommits,
      evidenceDir: '/w/evidence/checkout/1',
      featureBranch: '2_4_add-login-page',
      pullRequestUrl: 'https://github.com/owner/repo/pull/99',
      skills: expected.skills ?? [],
      specJson: expected.specJson ?? '',
      ticketsJson: expected.ticketsJson ?? '',
    };
    const args = verifyArgs(context, '/w/tree');

    expect(args).toEqual(expected.args);
    expect(renderPrompt(loadPrompt(expected.template), args)).toBe(expected.rendered);
  });
});

describe('prompt arguments', () => {
  test('match the placeholders of the implement template exactly', () => {
    const args = promptArgs(ticketOf(GOLDEN.ticket), new AgentCommitSubject('Checkout', 10), ['abc1234 first'], '/w', 'main', 'f');
    const placeholders = new Set([...readFileSync(PROMPT, 'utf8').matchAll(PLACEHOLDER)].map((match) => match[1]));

    expect(placeholders).toEqual(new Set(Object.keys(args)));
  });

  test('carry only the Ticket and the Initiative commits', () => {
    const identifier = new AgentCommitSubject('Checkout', 10);
    const args = promptArgs(ticketOf(GOLDEN.ticket), identifier, [], '/w', 'main', 'feature');

    const ticketJson = JSON.parse(args.TICKET_JSON ?? '') as { body: string; comments: { created_at: string }[]; number: number };
    expect([ticketJson.number, ticketJson.body]).toEqual([10, GOLDEN.ticket.body]);
    expect(ticketJson.comments[0]?.created_at).toBe('2026-01-01T00:00:00Z');
    expect(args.COMMIT_SUBJECT_PREFIX).toBe(identifier.toSubject());
    expect(args.INITIATIVE_COMMITS).toContain('No task commits');
    for (const excluded of ['SPEC_JSON', 'TICKETS_JSON', 'RECENT_COMMITS', 'TASK_ID']) {
      expect(Object.keys(args)).not.toContain(excluded);
    }
  });
});

describe('rendering', () => {
  test('substitutes placeholders and leaves other braces alone', () => {
    expect(renderPrompt('A={{A}} B={{ B }} {A} ${A}', { A: '1', B: '2' })).toBe('A=1 B=2 {A} ${A}');
  });

  test('never expands placeholders arriving through arguments', () => {
    expect(renderPrompt('{{BODY}}', { BODY: '{{BODY}}' })).toBe('{{BODY}}');
  });

  test('keeps replacement patterns in arguments literal', () => {
    expect(renderPrompt('{{BODY}}', { BODY: "$& $1 $'" })).toBe("$& $1 $'");
  });

  test('rejects a missing argument', () => {
    expect(() => renderPrompt('{{X}}', {})).toThrow(PromptError);
    expect(() => renderPrompt('{{X}}', {})).toThrow('missing prompt argument: X');
  });

  test('reads a named template from a prompt folder', () => {
    writeFileSync(join(directory, 'first.md'), 'Implement {{TITLE}}');

    expect(renderPrompt(loadPrompt('first', directory), { TITLE: 'login' })).toBe('Implement login');
  });
});
