import { describe, expect, test } from 'bun:test';

import {
  AgentCommitSubject,
  loopSessionName,
  RepositoryId,
  ReviewResult,
  slugify,
  Spec,
  Ticket,
} from '../records.ts';

function spec(labels: readonly string[], title = 'Basket: Add Login Page!', tickets: readonly Ticket[] = []): Spec {
  return new Spec({ labels, number: 1, tickets, title, url: 'url' });
}

function ticket(number: number, labels: readonly string[] = [], state = 'open'): Ticket {
  return new Ticket({ labels, number, state, title: `T${String(number)}`, url: 'u' });
}

describe('repository identity', () => {
  test('contains owner and repo and renders the slug', () => {
    const identity = new RepositoryId('antshc', 'loop');

    expect([identity.owner, identity.repo, identity.toString()]).toEqual(['antshc', 'loop', 'antshc/loop']);
  });
});

describe('a Spec', () => {
  test('prefixes the feature branch slug with an underscored version', () => {
    expect(spec(['repo:base:release/2.4'], 'Add Login Page!').featureBranch).toBe('2_4_add-login-page');
  });

  test('uses just the slug without a version', () => {
    expect(spec(['repo:base:main'], 'Add Login Page!').featureBranch).toBe('add-login-page');
    expect(spec([]).featureBranch).toBeNull();
  });

  test('splits the Initiative from the title', () => {
    expect([spec([]).initiative, spec([]).bareTitle]).toEqual(['Basket', 'Add Login Page!']);
    expect([spec([], 'No prefix').initiative, spec([], 'No prefix').bareTitle]).toEqual(['1', 'No prefix']);
    expect(slugify('!!!')).toBe('spec');
  });

  test('resolves one target and one base label only', () => {
    expect(spec(['repo:target:acme/app', 'repo:base:main']).target).toBe('acme/app');
    expect(spec(['repo:target:acme/app', 'repo:target:acme/other']).target).toBeNull();
    expect(spec(['repo:target:not a slug']).target).toBeNull();
    expect(spec(['repo:base:main', 'repo:base:dev']).baseBranch).toBeNull();
    expect(spec(['repo:base:']).baseBranch).toBeNull();
  });

  test('has work only with an actionable Ticket', () => {
    expect(spec([], 't', [ticket(1, ['hitl']), ticket(2, [], 'closed'), ticket(3, ['Spec'])]).hasWork).toBe(false);
    expect(spec([], 't', [ticket(1), ticket(2, ['hitl'])]).hasWork).toBe(true);
  });

  test('needs a plan when it has neither sub-issues nor Tickets', () => {
    expect(spec([]).needsPlan).toBe(true);
    expect(spec([], 't', [ticket(1)]).needsPlan).toBe(false);
  });

  test('records changes in the order GitHub must see them', () => {
    const target = spec([], 't', [ticket(10)]);

    target.comment('one');
    target.escalate(10, 'stuck');
    target.closeTicket(10, 'done');
    target.announceDelivered('https://pr');

    expect(target.pendingChanges).toEqual([
      { body: 'one', kind: 'comment', number: 1 },
      { kind: 'label', label: 'hitl', number: 10 },
      { body: 'dev: stuck', kind: 'comment', number: 10 },
      { kind: 'label', label: 'hitl', number: 1 },
      { body: 'dev: stuck', kind: 'comment', number: 1 },
      { comment: 'done', kind: 'close', number: 10 },
      { body: 'dev: all Tickets delivered; draft pull request: https://pr', kind: 'comment', number: 1 },
    ]);
    expect(target.awaitingHuman).toBe(true);
    expect(target.tickets[0]?.labels).toEqual(['hitl']);
    expect(target.tickets[0]?.state).toBe('closed');
  });

  test('blocks only when there is work to block', () => {
    const idle = spec([]);
    idle.block('why');
    const busy = spec([], 't', [ticket(1)]);
    busy.block('why');

    expect(idle.pendingChanges).toEqual([]);
    expect(busy.pendingChanges).toHaveLength(2);
  });

  test('acknowledges the oldest equal change only', () => {
    const target = spec([]);
    target.comment('same');
    target.comment('same');

    target.acknowledge({ body: 'same', kind: 'comment', number: 1 });

    expect(target.pendingChanges).toHaveLength(1);
  });

  test('refuses to change a Ticket it does not own', () => {
    expect(() => {
      spec([]).closeTicket(5, 'x');
    }).toThrow('ticket #5 does not belong to spec #1');
  });
});

describe('agent identifiers', () => {
  test('format Ticket identifiers and commit prefixes', () => {
    const identifier = new AgentCommitSubject('Checkout', 10);

    expect(identifier.toString()).toBe('Checkout|10');
    expect(identifier.initiativeCommitPrefix).toBe('ccode(Checkout|');
    expect(identifier.toSubject()).toBe('ccode(Checkout|10): ');
  });

  test('name a session after its source Spec', () => {
    expect(loopSessionName(7, '  Basket:   Add\tdiscount ')).toBe('loop(7) Basket: Add discount');
  });
});

describe('a review result', () => {
  const axes = ['bugs', 'quality', 'smells', 'initiative'].map((name) => ({ name, outcome: 'completed' }));

  test('is completed only when every axis completed', () => {
    expect(new ReviewResult({ axes }).completed).toBe(true);
    expect(new ReviewResult({ axes: axes.slice(1) }).completed).toBe(false);
    expect(new ReviewResult({ axes: [{ name: 'bugs', outcome: 'skipped' }, ...axes.slice(1)] }).completed).toBe(false);
  });

  test('blocks only on bug and initiative findings', () => {
    const result = new ReviewResult({
      findings: [
        { kind: 'bug', summary: 'a' },
        { kind: 'quality', summary: 'b' },
        { kind: 'initiative', summary: 'c' },
        { kind: 'smell', summary: 'd' },
      ],
    });

    expect(result.blockingFindings.map((finding) => finding.summary)).toEqual(['a', 'c']);
  });
});
