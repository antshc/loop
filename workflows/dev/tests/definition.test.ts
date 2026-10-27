import { describe, expect, test } from 'bun:test';

import { Kind, Role } from '../../../src/loop/index.ts';
import { devWorkflow, Owner } from '../definition.ts';
import { RecordingObserver } from './support/observer.ts';
import { Stream } from './support/stream.ts';

describe('the dev definition', () => {
  const { definitions, name } = devWorkflow();

  test('declares every phase the delivery reports, in order', () => {
    expect(name).toBe('dev');
    expect(definitions.map((definition) => definition.name)).toEqual([
      'spec',
      'preflight',
      'plan',
      'create-tickets',
      'deliver',
      'ticket',
      'validate',
      'close-ticket',
      'restore',
      'escalate',
      'publish',
      'verify',
      'commit-fixes',
      'chore',
      'commit-chore',
      'review',
      'finalize',
    ]);
  });

  test('labels and owns each phase as the Python Workflow did', () => {
    const rows = definitions.map((definition) => [definition.label, definition.kind, definition.owner]);

    expect(rows).toContainEqual(['Spec', Kind.ENGINEER, Role.ENGINEER]);
    expect(rows).toContainEqual(['Plan tickets', Kind.AGENT, Role.PLANNER]);
    expect(rows).toContainEqual(['Implement ticket', Kind.AGENT, Role.DEVELOPER]);
    expect(rows).toContainEqual(['Verify application', Kind.AGENT, 'verifier']);
    expect(rows).toContainEqual(['Refine changes', Kind.AGENT, 'chore']);
    expect(rows).toContainEqual(['Review pull request', Kind.AGENT, Role.REVIEWER]);
    expect(rows).toContainEqual(['Publish draft PR', Kind.CODE, Role.CODE]);
    expect(Owner.VERIFIER).toBe('verifier');
    expect(Owner.CHORE).toBe('chore');
  });

  test('hides the housekeeping phases', () => {
    expect(definitions.filter((definition) => !definition.visible).map((definition) => definition.name)).toEqual([
      'validate',
      'close-ticket',
      'restore',
      'commit-fixes',
      'commit-chore',
    ]);
  });

  test('adds the live table and the telemetry only when asked', () => {
    expect([devWorkflow().visualize, devWorkflow().observer]).toEqual([null, null]);

    const observer = new RecordingObserver('http://x');
    const stream = new Stream();
    const workflow = devWorkflow({ observer, visualize: { stream } });

    expect(workflow.observer).toBe(observer);
    expect(workflow.visualize?.stream).toBe(stream);
    expect(devWorkflow({ visualize: true }).visualize).toEqual({});
  });
});
