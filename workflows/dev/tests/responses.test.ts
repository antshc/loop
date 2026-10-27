import { describe, expect, test } from 'bun:test';

import { AgentCommitSubject } from '../records.ts';
import {
  ChoreResponse,
  ContentDecodeError,
  DevResponse,
  DevResultError,
  LastJsonObject,
  parseDevResult,
  PlanResponse,
  VerifyResponse,
} from '../responses.ts';

const COMPLETED = { commit: 'abc', summary: 'done', verification: 'ran tests' };

const text = (status = 'completed', result: object = {}): string =>
  `thinking...\n${JSON.stringify({ result, status })}\nbye`;

const hasStatus = (value: Record<string, unknown>): boolean => 'status' in value;

describe('the last JSON object', () => {
  test('is the last one carrying a status', () => {
    const input = 'noise {"status": "completed", "result": {}} {"other": 1} {"status": "failed", "result": {"reason": "r"}} tail';

    expect(new LastJsonObject(hasStatus).decode(input).status).toBe('failed');
  });

  test('parses nested objects and braces inside strings', () => {
    const input = '{"status": "completed", "result": {"plan": "do {this} first", "steps": {"a": 1}}}';

    expect(new LastJsonObject(hasStatus).decode(input).result).toEqual({ plan: 'do {this} first', steps: { a: 1 } });
  });

  test('ignores the prose around it', () => {
    expect(new LastJsonObject(hasStatus).decode('thinking out loud\n{"status": "completed", "result": {}}\ndone').status).toBe(
      'completed',
    );
  });

  test('is filtered by the condition', () => {
    expect(new LastJsonObject(hasStatus).decode('{"other": 1} {"status": "completed", "result": {}}')).toEqual({
      result: {},
      status: 'completed',
    });
  });

  test('matches every object by default', () => {
    expect(new LastJsonObject().decode('{"a": 1} {"b": 2}')).toEqual({ b: 2 });
  });

  test('finds an object nested in one the condition rejects', () => {
    expect(new LastJsonObject(hasStatus).decode('{"wrapper": {"status": "completed", "result": {}}}')).toEqual({
      result: {},
      status: 'completed',
    });
  });

  test.each(['', 'no json', '{"status": "completed"', '{"other": 1}', '[1]'])('is absent from %j', (input) => {
    expect(() => new LastJsonObject(hasStatus).decode(input)).toThrow(ContentDecodeError);
  });
});

describe('a Ticket response', () => {
  const identifier = new AgentCommitSubject('Checkout', 10);
  const envelope = (id = 'Checkout|10', status = 'completed', result: object = COMPLETED): string =>
    JSON.stringify({ identifier: id, result, status });

  test('completes with the commit, summary and verification', () => {
    expect(parseDevResult({ identifier: 'Checkout|10', result: COMPLETED, status: 'completed' })).toEqual({
      commit: 'abc',
      identifier: 'Checkout|10',
      status: 'completed',
      summary: 'done',
      verification: 'ran tests',
    });
  });

  test('fails with its reason', () => {
    const result = parseDevResult({ identifier: 'x', result: { reason: 'stuck' }, status: 'failed' });

    expect([result.status, result.reason]).toEqual(['failed', 'stuck']);
  });

  test.each([
    { identifier: 'x', result: {}, status: 'weird' },
    { identifier: 'x', result: { commit: 'a', summary: 's' }, status: 'completed' },
    { identifier: 'x', result: {}, status: 'failed' },
  ])('rejects the malformed envelope %j', (malformed) => {
    expect(() => parseDevResult(malformed)).toThrow(DevResultError);
  });

  test('must answer the asked task', () => {
    const withoutIdentifier = JSON.stringify({ result: COMPLETED, status: 'completed' });

    const result = new DevResponse(identifier).decode(`x ${withoutIdentifier}`);
    expect([result.identifier, result.commit]).toEqual(['Checkout|10', 'abc']);
    expect(new DevResponse(identifier).decode(`x ${envelope()}`).commit).toBe('abc');
    expect(() => new DevResponse(identifier).decode('nothing')).toThrow('no response object');
    expect(() => new DevResponse(identifier).decode(envelope('Checkout|11'))).toThrow(
      "response identifier 'Checkout|11' does not match task AgentCommitSubject(initiative='Checkout', ticket_number=10)",
    );
  });

  test('ignores the noise before it', () => {
    const noise = envelope('Other|1');

    expect(new DevResponse(identifier).decode(`working...\n${noise}\nmore text\n${envelope()}\n`).identifier).toBe('Checkout|10');
  });
});

describe('a plan response', () => {
  test('lists the Tickets in order with their blockers', () => {
    const plan = new PlanResponse().decode(
      text('completed', { tickets: [{ body: 'a', title: 'A' }, { blocked_by: [0], body: 'b', title: 'B' }] }),
    );

    expect(plan.status).toBe('completed');
    expect((plan.tickets ?? []).map((ticket) => [ticket.title, ticket.blockedBy])).toEqual([
      ['A', []],
      ['B', [0]],
    ]);
  });

  test('is a refusal with its reason', () => {
    const plan = new PlanResponse().decode(text('failed', { reason: 'ambiguous' }));

    expect([plan.status, plan.reason, plan.tickets]).toEqual(['failed', 'ambiguous', []]);
  });

  test.each([
    ['no JSON', 'no json'],
    ['tickets that are not a list', text('completed', { tickets: 'nope' })],
    ['a ticket that is not an object', text('completed', { tickets: ['nope'] })],
    ['blockers that are not indexes', text('completed', { tickets: [{ blocked_by: ['x'], title: 'A' }] })],
    ['a refusal without a reason', text('failed')],
    ['an unknown status', JSON.stringify({ result: {}, status: 'weird' })],
  ])('rejects %s', (_name, output) => {
    expect(() => new PlanResponse().decode(output)).toThrow(DevResultError);
  });
});

describe('a chore response', () => {
  test('is lenient about missing or malformed output', () => {
    expect(new ChoreResponse().decode('no json').commitMessage).toBe('');
    expect(new ChoreResponse().decode(text('failed', { reason: 'x' })).summary).toBe('');
    expect(new ChoreResponse().decode(text('completed', { commit_message: 3 })).commitMessage).toBe('');
    expect(new ChoreResponse().decode(text('completed', { commit_message: 'tidy', out_of_scope: ['old bug'], summary: 's' }))).toEqual({
      commitMessage: 'tidy',
      outOfScope: ['old bug'],
      summary: 's',
    });
  });
});

describe('a verify response', () => {
  test('carries the verdict, the findings and the fix message', () => {
    const result = new VerifyResponse().decode(
      text('completed', {
        commit_message: 'Fix total',
        findings: [{ status: 'fixed', summary: 'total' }, { reason: 'infra', summary: 'slow' }],
        summary: 'ok',
        verdict: 'PASS',
        verification: 'mole',
      }),
    );

    expect([result.verdict, result.commitMessage]).toEqual(['pass', 'Fix total']);
    expect(result.findings.map((finding) => [finding.summary, finding.status, finding.reason])).toEqual([
      ['total', 'fixed', ''],
      ['slow', 'unfixed', 'infra'],
    ]);
  });

  test('turns a failed run into an inconclusive verdict', () => {
    const result = new VerifyResponse().decode(text('failed', { reason: 'app did not start' }));

    expect([result.verdict, result.summary]).toEqual(['inconclusive', 'app did not start']);
  });

  test('rejects an unknown verdict', () => {
    expect(() => new VerifyResponse().decode(text('completed', { verdict: 'great' }))).toThrow('unknown verdict');
  });
});
