import { describe, expect, test } from 'bun:test';

import {
  WorkflowBuilder as Builder,
  Kind,
  type OutputStream,
  Phases,
  type PhaseSpan,
  Role,
  ValueError,
  type WorkflowBuilder,
  type WorkflowLogger,
} from '../../src/loop/index.ts';
import { Cancelled } from '../../src/loop/run/cancelled.ts';
import { rejection } from './support/rejection.ts';
import { SpanRecorder } from './support/span-observer.ts';

const ESCAPE = '\u001B';

class RecordingLogger implements WorkflowLogger {
  readonly infos: string[] = [];
  readonly warnings: string[] = [];

  info(message: string): void {
    this.infos.push(message);
  }

  warn(message: string): void {
    this.warnings.push(message);
  }
}

class Stream implements OutputStream {
  readonly writes: string[] = [];

  get text(): string {
    return this.writes.join('');
  }

  constructor(readonly isTTY: boolean) {}

  write(text: string): void {
    this.writes.push(text);
  }
}

function builder(): WorkflowBuilder {
  return new Builder('Basket')
    .phase('ticket', 'Add product', Kind.AGENT, Role.DEVELOPER)
    .phase('commit', 'Commit changes', Kind.CODE, Role.CODE, { visible: false })
    .phase('verify', 'Verify basket', Kind.CODE, Role.CODE);
}

describe('console reporting', () => {
  test('reports only transitions, never payloads or gates', async () => {
    const logger = new RecordingLogger();

    await builder()
      .console(logger)
      .build()
      .run({ spec: 42 }, async (run) => {
        await run.phase('ticket', { attributes: { attempt: 2, ticket: 43 } }, (phase) => {
          phase.set({ output: 'PRIVATE_OUTPUT', prompt: 'PRIVATE_PROMPT' });
          phase.gate('PRIVATE_GATE', true);
        });
        await run.phase('commit', () => undefined);
      });

    const messages = [...logger.infos, ...logger.warnings];
    expect(logger.infos).toHaveLength(4);
    expect(logger.warnings).toHaveLength(0);
    expect(logger.infos.filter((message) => message.includes('Add product') && message.includes('completed'))).toHaveLength(1);
    expect(logger.infos[1]).toContain('ticket #43 · attempt 2');
    expect(logger.infos[0]).toBe('Basket · spec #42 started');
    expect(logger.infos[1]).toBe('Add product · spec #42 · ticket #43 · attempt 2 started');
    expect(messages.some((message) => message.includes('PRIVATE') || message.includes('Commit changes'))).toBe(false);
  });

  test('summarizes verdicts and the failure reason on one short line, warning when failed or cancelled', async () => {
    const logger = new RecordingLogger();

    await builder()
      .console(logger)
      .build()
      .run(async (run) => {
        await run.phase('ticket', (phase) => {
          phase.set({ ready: false, verdict: 'fail' });
          phase.fail(`failure\n${'x'.repeat(1000)}`);
        });
      });

    const warning = logger.warnings[0] ?? '';
    expect(warning).not.toContain('\n');
    expect(warning.length).toBeLessThan(260);
    expect(warning).toMatch(/^Add product failed · 0\.0s · verdict=fail · ready=False · failure x+$/);
  });

  test('logs a cancelled phase as a warning with its reason', async () => {
    const logger = new RecordingLogger();

    await rejection(
      builder()
        .console(logger)
        .build()
        .run(async (run) => {
          await run.phase('ticket', () => {
            throw new Cancelled('SIGINT');
          });
        }),
    );

    expect(logger.warnings.some((message) => message.startsWith('Add product cancelled · 0.0s · Cancelled'))).toBe(true);
  });

  test('without a logger the records are dropped', async () => {
    const run = await builder()
      .console()
      .build()
      .run((current) => current);

    expect(run.outcome.status).toBe('completed');
  });

  test('a broken logger can neither fail a run nor replace its error', async () => {
    const broken: WorkflowLogger = {
      info: () => {
        throw new Error('log destination closed');
      },
      warn: () => {
        throw new Error('log destination closed');
      },
    };
    const workflow = builder().console(broken).build();
    const original = new ValueError('original');

    const recovered = await workflow.run(async (run) => {
      await run.phase('ticket', () => undefined);
      return run;
    });
    const raised = await rejection(
      workflow.run(async (run) => {
        await run.phase('ticket', () => Promise.reject(original));
      }),
    );

    expect(recovered.outcome.status).toBe('completed');
    expect(raised).toBe(original);
  });
});

describe('visualization to redirected output', () => {
  test('prints one final table with retries and skips, hides hidden phases, and has no escape sequences', async () => {
    let now = 0;
    const stream = new Stream(false);
    const logger = new RecordingLogger();

    await builder()
      .console(logger)
      .visualize({ clock: () => now, stream })
      .build()
      .run({ spec: 42 }, async (run) => {
        await run.phase('ticket', { attributes: { attempt: 1, ticket: 43 } }, (phase) => {
          now += 1500;
          phase.fail('try again');
        });
        await run.phase('ticket', { attributes: { attempt: 2, ticket: 43 } }, () => {
          now += 2000;
        });
        await run.phase('commit', () => {
          now += 100;
        });
        await run.skip('verify', 'external verifier');
        now += 500;
      });

    expect(stream.writes).toHaveLength(1);
    expect(stream.text).toBe(
      [
        '┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━┓',
        '┃ Workflow name ┃ Phase                                           ┃ Owner     ┃ Status    ┃ Time ┃',
        '┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━┩',
        '│ Basket        │ Basket · spec #42                               │ code      │ completed │ 4.1s │',
        '│ Basket        │ Add product · spec #42 · ticket #43 · attempt 1 │ developer │ failed    │ 1.5s │',
        '│ Basket        │ Add product · spec #42 · ticket #43 · attempt 2 │ developer │ completed │ 2.0s │',
        '│ Basket        │ Verify basket · spec #42                        │ code      │ skipped   │ 0.0s │',
        '└───────────────┴─────────────────────────────────────────────────┴───────────┴───────────┴──────┘',
        '',
      ].join('\n'),
    );
    expect(stream.text).not.toContain(ESCAPE);
    expect(stream.text).not.toContain('Commit changes');
    expect(logger.infos.filter((message) => message.includes('Add product') && message.includes('completed'))).toHaveLength(1);
  });

  test('a failed run still prints its final table', async () => {
    const stream = new Stream(false);

    await rejection(
      builder()
        .visualize({ stream })
        .build()
        .run(async (run) => {
          await run.phase('ticket', () => Promise.reject(new ValueError('broken')));
        }),
    );

    expect(stream.text).toContain('failed');
    expect(stream.text).toContain('pending');
    expect(stream.text).not.toContain(ESCAPE);
  });
});

describe('visualization on a terminal', () => {
  function live(): { advance: (ms: number) => void; options: object; stopped: () => boolean; stream: Stream; tick: () => void; } {
    let now = 0;
    let stopped = false;
    let tick = (): void => undefined;
    const stream = new Stream(true);
    return {
      advance: (ms) => {
        now += ms;
      },
      options: {
        clock: () => now,
        stream,
        ticker: (callback: () => void) => {
          tick = callback;
          return () => {
            stopped = true;
          };
        },
      },
      stopped: () => stopped,
      stream,
      tick: () => {
        tick();
      },
    };
  }

  test('redraws in place with pending phases and running durations, then leaves the final table', async () => {
    const view = live();

    await builder()
      .visualize(view.options)
      .build()
      .run(async (run) => {
        await run.phase('ticket', () => {
          view.advance(2000);
          view.tick();
        });
        view.advance(1000);
      });

    const frames = view.stream.writes;
    const lines = (frame: string): number => frame.trimEnd().split('\n').length;
    expect(frames[0]).not.toContain(ESCAPE);
    expect(frames[0]).toContain('pending');
    expect(frames[0]).toMatch(/Basket\s+│ Basket\s+│ code\s+│ running\s+│ 0\.0s/);
    for (let index = 1; index < frames.length; index++) {
      expect(frames[index]?.startsWith(`${ESCAPE}[${String(lines(frames[index - 1] ?? ''))}A${ESCAPE}[J`)).toBe(true);
    }
    const ticked = frames.find((frame) => frame.includes('2.0s') && frame.includes('running'));
    expect(ticked).toMatch(/Add product\s+│ developer\s+│ running\s+│ 2\.0s/);
    const last = frames.at(-1) ?? '';
    expect(last).toMatch(/Basket\s+│ Basket\s+│ code\s+│ completed\s+│ 3\.0s/);
    expect(last).toMatch(/Add product\s+│ developer\s+│ completed\s+│ 2\.0s/);
    expect(last).toMatch(/Verify basket\s+│ code\s+│ pending/);
    expect(view.stopped()).toBe(true);
  });

  test('stops redrawing and the ticker when the body raises', async () => {
    const view = live();

    const raised = await rejection(
      builder()
        .visualize(view.options)
        .build()
        .run(async (run) => {
          await run.phase('ticket', () => Promise.reject(new ValueError('broken')));
        }),
    );

    expect(String(raised)).toContain('broken');
    expect(view.stopped()).toBe(true);
  });
});

describe('reporting never changes execution', () => {
  test('a broken output stream does not replace the body error or fail the run', async () => {
    const stream: OutputStream = {
      isTTY: false,
      write: () => {
        throw new Error('closed stream');
      },
    };
    const logger = new RecordingLogger();
    const error = new ValueError('basket invalid');
    const workflow = builder().console(logger).visualize({ stream }).build();

    const raised = await rejection(
      workflow.run(async (run) => {
        await run.phase('ticket', () => Promise.reject(error));
      }),
    );
    const recovered = await workflow.run((run) => run);

    expect(raised).toBe(error);
    expect(recovered.outcome.status).toBe('completed');
    expect(logger.warnings).toContain('Workflow reporting unavailable (Error)');
  });

  test('a reporter that fails on every point is skipped', async () => {
    const phases = new Phases({
      reporters: [
        {
          emit: () => {
            throw new Error('boom');
          },
        },
      ],
    });

    const result = await phases.phase('direct', { kind: Kind.CODE, owner: Role.CODE }, () => 'ok');

    expect(result).toBe('ok');
  });
});

describe('observer spans', () => {
  test('phase spans nest under their parent phase and carry attributes, gates and the final status', async () => {
    const observer = new SpanRecorder();
    let seen: string | undefined;

    await builder()
      .otel(observer)
      .build()
      .run({ spec: 42 }, async (run) => {
        await run.phase('ticket', { attributes: { ticket: 43 } }, async (phase) => {
          await Bun.sleep(1);
          seen = observer.current?.name;
          phase.gate('basket changed', false, 'no changes');
        });
        await run.phase('commit', () => undefined);
        run.set({ ready: false });
        run.fail('PR remains draft');
      });

    const spans = new Map(observer.spans.map((span) => [span.name, span]));
    const ticket = spans.get('Add product');
    expect(seen).toBe('Add product');
    expect(ticket?.parent).toBe(spans.get('Basket') ?? null);
    expect(spans.get('Basket')?.parent).toBeNull();
    expect(ticket?.role).toBe('developer');
    expect(ticket?.end?.error).toBe('no changes');
    expect(ticket?.end?.gates).toEqual([{ check: 'basket changed', note: 'no changes', ok: false }]);
    expect(ticket?.end?.attributes).toMatchObject({
      'loop.phase.kind': 'agent',
      'loop.phase.owner': 'developer',
      'loop.phase.parent_id': spans.get('Basket')?.attributes['loop.phase.id'],
      'loop.phase.status': 'failed',
      'loop.spec': 42,
      'loop.ticket': 43,
    });
    expect(spans.get('Commit changes')?.end?.attributes['loop.phase.status']).toBe('completed');
    expect(spans.get('Commit changes')?.end?.error).toBeNull();
    expect(spans.get('Basket')?.end?.attributes).toMatchObject({ 'loop.phase.status': 'failed', 'loop.ready': false });
    expect(spans.get('Basket')?.end?.error).toBe('PR remains draft');
    expect(observer.current).toBeUndefined();
  });

  test('a cancelled phase ends its span with the cancellation as the error', async () => {
    const observer = new SpanRecorder();

    await rejection(
      builder()
        .otel(observer)
        .build()
        .run(async (run) => {
          await run.phase('ticket', () => {
            throw new Cancelled('SIGINT');
          });
        }),
    );

    expect(observer.spans.map((span) => span.end?.attributes['loop.phase.status'])).toEqual(['cancelled', 'cancelled']);
    expect(observer.spans[1]?.end?.error).toBe('Cancelled');
  });

  test('an observer that fails to open or close a span does not change execution', async () => {
    const logger = new RecordingLogger();
    const unavailable = {
      span: (): PhaseSpan => {
        throw new Error('exporter unavailable');
      },
    };
    const brokenClose = {
      span: (): PhaseSpan => ({
        end: () => {
          throw new Error('cleanup failed');
        },
        run: (body) => body(),
      }),
    };
    const original = new ValueError('original');

    const opened = await builder().console(logger).otel(unavailable).build().run((run) => run);
    const closed = await builder().console(logger).otel(brokenClose).build().run((run) => run);
    const raised = await rejection(
      builder()
        .otel(brokenClose)
        .build()
        .run(() => Promise.reject(original)),
    );

    expect(opened.outcome.status).toBe('completed');
    expect(closed.outcome.status).toBe('completed');
    expect(raised).toBe(original);
    expect(logger.warnings).toEqual([
      'Workflow telemetry unavailable (Error)',
      'Workflow telemetry cleanup unavailable (Error)',
    ]);
  });

  test('a span that cannot run and end is ignored', async () => {
    const logger = new RecordingLogger();
    const malformed = { span: () => ({}) };

    const run = await builder().console(logger).otel(malformed).build().run((current) => current);

    expect(run.outcome.status).toBe('completed');
    expect(logger.warnings[0]).toBe('Workflow telemetry unavailable (TypeError)');
  });
});

describe('the optional span capability of an observer', () => {
  test('an observer without span() is refused with a TypeError naming it', () => {
    const observer = { agentRun: () => undefined, worktree: () => undefined };

    expect(() => builder().otel(observer)).toThrow(TypeError);
    expect(() => builder().otel(observer)).toThrow(/span/);
  });

  test('a span that is not callable counts as absent, as before', () => {
    expect(() => builder().otel({ span: 'not callable' })).toThrow(TypeError);
    expect(() => builder().otel(null)).toThrow(TypeError);
    expect(() => builder().otel(undefined)).toThrow(TypeError);
  });

  test('an observer with span() on its prototype is accepted', () => {
    expect(() => builder().otel(new SpanRecorder())).not.toThrow();
  });
});
