import { describe, expect, test } from 'bun:test';

import { Kind, Phases, Role, ValueError, WorkflowBuilder, type WorkflowRun } from '../../src/loop/index.ts';
import { Cancelled } from '../../src/loop/run/cancelled.ts';
import { rejection } from './support/rejection.ts';

function builder(): WorkflowBuilder {
  return new WorkflowBuilder('Basket delivery')
    .phase('ticket', 'Add product', Kind.AGENT, Role.DEVELOPER)
    .phase('verify', 'Verify basket', Kind.CODE, Role.CODE);
}

describe('phase status', () => {
  test('a body that succeeds completes the phase and its result is returned', async () => {
    const phases = new Phases();

    const result = await phases.phase('direct', { kind: Kind.CODE, owner: Role.CODE }, () => 42);

    expect(result).toBe(42);
    expect(phases.executions.map((phase) => [phase.name, phase.status])).toEqual([['direct', 'completed']]);
  });

  test('a failing body fails the phase and rethrows the original error', async () => {
    const phases = new Phases();
    const error = new ValueError('invalid basket');

    const raised = await phases.phase('direct', { kind: Kind.CODE, owner: Role.CODE }, () => Promise.reject(error)).catch((e: unknown) => e);

    expect(raised).toBe(error);
    expect(phases.executions[0]?.status).toBe('failed');
    expect(phases.executions[0]?.reason).toBe('ValueError');
  });

  test('a cancelled body cancels the phase and rethrows the original Cancelled', async () => {
    const phases = new Phases();
    const error = new Cancelled('SIGINT');

    const raised = await phases.phase('direct', { kind: Kind.CODE, owner: Role.CODE }, () => Promise.reject(error)).catch((e: unknown) => e);

    expect(raised).toBe(error);
    expect(phases.executions[0]?.status).toBe('cancelled');
    expect(phases.executions[0]?.reason).toBe('Cancelled');
  });

  test('any other error type fails the phase, named by its class, and is rethrown', async () => {
    const phases = new Phases();

    const raised = await phases.phase('direct', { kind: Kind.CODE, owner: Role.CODE }, () => Promise.reject(new TypeError('plain'))).catch((e: unknown) => e);

    expect(raised).toBeInstanceOf(TypeError);
    expect(phases.executions[0]?.reason).toBe('TypeError');
    expect(phases.executions[0]?.status).toBe('failed');
  });

  test('a failed gate stays failed through a clean exit, a later success and a skip', async () => {
    const phases = new Phases();

    const phase = await phases.phase('ticket', { attributes: { ticket: 43 }, kind: Kind.AGENT, owner: Role.DEVELOPER }, (current) => {
      expect(current.gate('product added', false, 'basket unchanged')).toBe(false);
      current.succeed();
      current.skip('not needed');
      current.set({ verdict: 'fail' });
      return current;
    });

    expect(phase.status).toBe('failed');
    expect(phase.reason).toBe('basket unchanged');
    expect(phase.attributes.verdict).toBe('fail');
    expect(phase.gates).toEqual([{ check: 'product added', note: 'basket unchanged', ok: false }]);
  });

  test('seconds are measured by the injected clock', async () => {
    let now = 1000;
    const phases = new Phases({ clock: () => now });

    await phases.phase('slow', { kind: Kind.CODE, owner: Role.CODE }, () => {
      now += 2500;
    });

    expect(phases.executions[0]?.seconds).toBe(2.5);
  });
});

describe('phase parentage', () => {
  test('a nested phase records the enclosing phase as its parent and inherits its attributes', async () => {
    const phases = new Phases();
    const options = { kind: Kind.CODE, owner: Role.CODE };

    await phases.phase('outer', { ...options, attributes: { ticket: 43 } }, async () => {
      await phases.phase('inner', options, () => undefined);
    });

    const [outer, inner] = phases.executions;
    expect(outer?.parentId).toBeNull();
    expect(inner?.parentId).toBe(outer?.id);
    expect(inner?.attributes.ticket).toBe(43);
  });

  test('the parent survives awaits and concurrent siblings do not see each other', async () => {
    const phases = new Phases();
    const options = { kind: Kind.CODE, owner: Role.CODE };
    const nested = async (name: string, delay: number): Promise<void> => {
      await phases.phase(name, options, async () => {
        await Bun.sleep(delay);
        await phases.phase(`${name}-child`, options, async () => {
          await Bun.sleep(delay);
        });
      });
    };

    await Promise.all([nested('a', 5), nested('b', 1)]);

    const byName = new Map(phases.executions.map((phase) => [phase.name, phase]));
    expect(byName.get('a-child')?.parentId).toBe(byName.get('a')?.id);
    expect(byName.get('b-child')?.parentId).toBe(byName.get('b')?.id);
    expect(byName.get('a')?.parentId).toBeNull();
    expect(byName.get('b')?.parentId).toBeNull();
  });

  test('after a child closes, later phases use the original parent and do not inherit the child attributes', async () => {
    const run = await builder().build().run({ spec: 42 }, async (current) => {
      await current.phase('ticket', { attributes: { ticket: 43 } }, async () => {
        await current.phase('verify', () => undefined);
      });
      await current.phase('verify', () => undefined);
      return current;
    });

    const [root, ticket, child, sibling] = run.executions;
    expect(child?.parentId).toBe(ticket?.id);
    expect(child?.attributes.ticket).toBe(43);
    expect(ticket?.parentId).toBe(root?.id);
    expect(sibling?.parentId).toBe(root?.id);
    expect(sibling?.attributes).not.toHaveProperty('ticket');
    expect(child?.attributes.spec).toBe(42);
  });
});

describe('the workflow run', () => {
  test('a retry that recovers is distinct from the run business outcome', async () => {
    const run = await builder().build().run({ spec: 42 }, async (current) => {
      await current.phase('ticket', { attributes: { attempt: 1, ticket: 43 } }, (phase) => {
        phase.fail('no changes');
      });
      await current.phase('ticket', { attributes: { attempt: 2, ticket: 43 } }, (phase) => {
        phase.set({ commit: 'abc' });
      });
      await current.skip('verify', 'handled externally');
      current.set({ ready: true });
      return current;
    });

    const [root, first, second, skipped] = run.executions;
    expect(first?.status).toBe('failed');
    expect(second?.status).toBe('completed');
    expect(first?.id).not.toBe(second?.id);
    expect(first?.parentId).toBe(root?.id);
    expect(second?.parentId).toBe(root?.id);
    expect(second?.attributes.spec).toBe(42);
    expect(run.outcome.status).toBe('completed');
    expect(run.outcome.attributes.ready).toBe(true);
    expect(skipped?.status).toBe('skipped');
    expect(skipped?.reason).toBe('handled externally');
    expect(run.executions.every((phase) => phase.seconds >= 0)).toBe(true);
  });

  test('an explicit run failure survives a successful child', async () => {
    const run = await builder().build().run(async (current) => {
      current.fail('PR remains draft');
      await current.phase('verify', () => undefined);
      return current;
    });

    expect(run.outcome.status).toBe('failed');
    expect(run.outcome.reason).toBe('PR remains draft');
  });

  test('the run body result is returned', async () => {
    expect(await builder().build().run(() => 'done')).toBe('done');
  });

  for (const [label, error, status] of [
    ['an error', new ValueError('invalid basket'), 'failed'],
    ['a cancellation', new Cancelled('SIGINT'), 'cancelled'],
  ] as const) {
    test(`${label} keeps its identity and closes the phase and the run as ${status}`, async () => {
      let seen: undefined | WorkflowRun;
      const raised = await builder()
        .build()
        .run(async (current) => {
          seen = current;
          await current.phase('ticket', () => {
            throw error;
          });
        })
        .catch((e: unknown) => e);

      expect(raised).toBe(error);
      expect(seen?.executions.map((phase) => phase.status)).toEqual([status, status]);
      expect(seen?.outcome.status).toBe(status);
    });
  }

  test('a definition is frozen and runs do not share state', async () => {
    const source = builder();
    const workflow = source.build();
    source.phase('extra', 'Extra', Kind.CODE, Role.CODE);

    const first = await workflow.run({ spec: 1 }, async (current) => {
      await current.phase('ticket', () => undefined);
      return current;
    });
    const second = await workflow.run({ spec: 2 }, async (current) => {
      expect(current.executions).toHaveLength(1);
      expect(String(await rejection(current.phase('extra', () => undefined)))).toMatch(/Unknown phase/);
      return current;
    });

    expect(first.outcome.id).not.toBe(second.outcome.id);
    expect(second.outcome.attributes.spec).toBe(2);
    expect(() => {
      Object.assign(workflow, { name: 'changed' });
    }).toThrow(TypeError);
    expect(workflow.name).toBe('Basket delivery');
  });

  test('invalid declarations and use after the run raise clear errors', async () => {
    expect(() => builder().phase('ticket', 'Duplicate', Kind.CODE, Role.CODE)).toThrow(/Duplicate phase/);
    expect(() => new WorkflowBuilder(' ')).toThrow(/must not be empty/);
    expect(() => builder().phase('', 'Empty', Kind.CODE, Role.CODE)).toThrow(/must not be empty/);
    expect(() => new WorkflowBuilder(' ')).toThrow(ValueError);

    let retained: undefined | WorkflowRun;
    await builder().build().run((current) => {
      retained = current;
    });

    expect(String(await rejection(retained?.phase('verify', () => undefined)))).toMatch(/closed/);
  });

  test('a helper accepting Phases can use a builder run and ownership stays consistent', async () => {
    const helper = (phases: Phases): Promise<void> =>
      phases.phase('ticket', { attributes: { ticket: 43 }, kind: Kind.AGENT, owner: Role.DEVELOPER }, (phase) => {
        phase.gate('basket changed', true);
      });

    const run = await builder().build().run(async (current) => {
      await helper(current);
      expect(String(await rejection(current.phase('ticket', { kind: Kind.AGENT, owner: Role.REVIEWER }, () => undefined)))).toMatch(/owner differs/);
      expect(String(await rejection(current.phase('ticket', { kind: Kind.CODE }, () => undefined)))).toMatch(/kind differs/);
      return current;
    });

    expect(run.executions[1]?.attributes.ticket).toBe(43);
    expect(run.executions[1]?.status).toBe('completed');
  });
});
