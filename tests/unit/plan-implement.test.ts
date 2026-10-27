import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  AgentBuilder,
  AgentContext,
  type AgentProfile,
  type AgentRequest,
  BranchStrategy,
  CliOutcome,
  type CliRunner,
  DockerRuntime,
  type GitOptions,
  type GitService,
  NativeHandle,
  ProcessFailedError,
  runEntrypoint,
} from '../../src/loop/index.ts';
import { Cancelled } from '../../src/loop/run/cancelled.ts';
import {
  IMPLEMENT_EFFORT,
  IMPLEMENT_INSTRUCTIONS,
  IMPLEMENT_MODEL,
  main,
  PLAN_EFFORT,
  PLAN_INSTRUCTIONS,
  PLAN_MODEL,
  type PlanImplementDependencies,
  responseBody,
} from '../../workflows/plan_implement.ts';
import { rejection } from './support/rejection.ts';

type Handler = (prompt: string) => string;

const plan = (text = 'do the work'): string => `${text}\n<completed>Success</completed>`;
const failedPlan = (reason = 'stuck'): string => `\n<completed>Failure: ${reason}</completed>`;
const done = '<completed>Success</completed>';
const failedImplementation = (reason = 'stuck'): string => `<completed>Failure: ${reason}</completed>`;

const crash: Handler = () => {
  throw new ProcessFailedError({ argv: ['copilot'], exitCode: 1, signal: null, stderr: 'crashed', stdout: '' });
};

/** A worktree that is a plain directory; records the options it opened with. */
class DirectoryWorktree implements GitService {
  closed = 0;
  readonly opened: GitOptions[] = [];

  constructor(readonly path: string) {}

  async open<T>(_cwd: string, options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    this.opened.push(options);
    mkdirSync(this.path, { recursive: true });
    try {
      return await body(this.path);
    } finally {
      this.closed++;
    }
  }
}

/** Answers each run with the next response; a response may be a function of the prompt. */
class ScriptedRunner implements CliRunner {
  readonly profiles: AgentProfile[] = [];
  readonly prompts: string[] = [];
  #responses: (Handler | string)[] = [];

  run(profile: AgentProfile, request: AgentRequest): Promise<CliOutcome> {
    this.prompts.push(request.prompt);
    this.profiles.push(profile);
    const response = this.#responses.shift() ?? '';
    try {
      const output = typeof response === 'function' ? response(request.prompt) : response;
      return Promise.resolve(new CliOutcome(output, new NativeHandle(profile.cli.name, `session${String(this.prompts.length)}`), 0));
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new Error('thrown'));
    }
  }

  script(...responses: (Handler | string)[]): void {
    this.#responses = responses;
  }
}

class Harness {
  commits = ['base0000'];
  readonly errors: string[] = [];
  readonly gitReads: [string, readonly string[]][] = [];
  readonly infos: string[] = [];
  readonly runner = new ScriptedRunner();
  readonly stderr: string[] = [];
  readonly stdout: string[] = [];
  readonly worktree: DirectoryWorktree;

  constructor(directory: string) {
    this.worktree = new DirectoryWorktree(join(directory, 'worktree'));
  }

  dependencies(overrides: PlanImplementDependencies = {}): PlanImplementDependencies {
    return {
      agent: () => new AgentBuilder(this.runner, this.worktree, new DockerRuntime(), new AgentContext({ cwd: '/harness/loop' })),
      cwd: () => '/harness/loop',
      logger: {
        error: (message) => this.errors.push(message),
        info: (message) => this.infos.push(message),
        warning: (message) => this.errors.push(message),
      },
      readGit: (path, args) => {
        this.gitReads.push([path, args]);
        if (args.join(' ') === 'rev-parse HEAD') {
          return Promise.resolve(this.commits.at(-1) ?? '');
        }
        const base = (args[3] ?? '').replace('..HEAD', '');
        return Promise.resolve(this.commits.slice(this.commits.indexOf(base) + 1).join('\n'));
      },
      stderr: (text) => this.stderr.push(text),
      stdout: (text) => this.stdout.push(text),
      uniqueId: () => 'abcd1234',
      ...overrides,
    };
  }

  run(responses: (Handler | string)[], task = 'add a widget', overrides: PlanImplementDependencies = {}): Promise<number> {
    this.runner.script(...responses);
    return main([task], this.dependencies(overrides));
  }
}

let directory = '';
let harness: Harness;

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'plan-implement-'));
  harness = new Harness(directory);
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

describe('plan_implement', () => {
  test('each run uses its own model and effort', async () => {
    expect(await harness.run([plan(), done])).toBe(0);

    const [planner, implementer] = harness.runner.profiles;
    expect([planner?.model, planner?.reasoningEffort]).toEqual([PLAN_MODEL, PLAN_EFFORT]);
    expect([implementer?.model, implementer?.reasoningEffort]).toEqual([IMPLEMENT_MODEL, IMPLEMENT_EFFORT]);
    expect([planner?.name, implementer?.name]).toEqual(['planner', 'developer']);
  });

  test('the planning prompt carries the task and the implementing prompt the plan', async () => {
    await harness.run([plan('Refactor the widget module.'), done]);

    expect(harness.runner.prompts[0]?.startsWith('add a widget')).toBe(true);
    expect(harness.runner.prompts[1]).toContain('Refactor the widget module.');
  });

  test('both runs share one worktree on a fresh branch off origin/main under the harness worktree root', async () => {
    await harness.run([plan(), done]);

    expect(harness.worktree.opened).toHaveLength(1);
    expect(harness.worktree.closed).toBe(1);
    const options = harness.worktree.opened[0];
    expect(options?.strategy).toBeInstanceOf(BranchStrategy);
    const strategy = options?.strategy as BranchStrategy;
    expect(strategy.branch).toBe('loop/run-abcd1234');
    expect(strategy.baseBranch).toBe('origin/main');
    expect(options?.rootPath).toBe(join('workspace', 'loop.worktrees'));
  });

  test('the default branch name is loop/run- and eight hex characters', async () => {
    harness.runner.script(plan(), done);

    await main(['task'], { ...harness.dependencies(), uniqueId: undefined });

    const strategy = harness.worktree.opened[0]?.strategy as BranchStrategy;
    expect(strategy.branch).toMatch(/^loop\/run-[0-9a-f]{8}$/);
  });

  test('noise around the response is ignored', async () => {
    expect(await harness.run([`thinking\n${plan()}\ndone`, `${done}\n`])).toBe(0);
  });

  const noPlan: [string, Handler | string][] = [
    ['an empty plan', plan('')],
    ['a failed plan', failedPlan()],
    ['no response', 'no response'],
    ['a crash', crash],
  ];
  for (const [name, response] of noPlan) {
    test(`planning with ${name} yields exit code 1 and no implementing run`, async () => {
      expect(await harness.run([response])).toBe(1);

      expect(harness.runner.prompts).toHaveLength(1);
      expect(harness.gitReads).toEqual([]);
      expect(harness.worktree.closed).toBe(1);
      expect(harness.errors).toContain('planning run failed: no usable plan');
    });
  }

  test.each([
    ['a failed plan', failedPlan('blocked'), 'agent reported: Failure: blocked'],
    ['no response', 'no response', 'no <completed> tag in agent output'],
    ['a crash', crash, 'agent command failed: '],
  ])('planning with %s logs the details', async (_name, response, reason) => {
    expect(await harness.run([response])).toBe(1);

    expect(harness.errors.some((message) => message.includes(reason))).toBe(true);
  });

  test('a crashed run logs the command failure with its stderr', async () => {
    await harness.run([crash]);

    expect(harness.errors[0]).toBe("agent command failed: Command 'copilot' returned non-zero exit status 1.; stderr: crashed");
  });

  const failedImplementations: [string, Handler | string][] = [
    ['a failed implementation', failedImplementation()],
    ['no response', 'no response'],
    ['a crash', crash],
  ];
  for (const [name, response] of failedImplementations) {
    test(`implementing with ${name} yields exit code 1 after both runs`, async () => {
      expect(await harness.run([plan(), response])).toBe(1);

      expect(harness.runner.prompts).toHaveLength(2);
      expect(harness.errors).toContain('implementing run failed');
      expect(harness.worktree.closed).toBe(1);
    });
  }

  test('the branch name and the implementing run commits are logged, reading git twice', async () => {
    const implement: Handler = () => {
      harness.commits.push('first000', 'second00');
      return done;
    };

    await harness.run([plan(), implement]);

    expect(harness.infos).toContain('branch loop/run-abcd1234');
    expect(harness.infos).toContain('commits: first000, second00');
    expect(harness.gitReads).toEqual([
      [harness.worktree.path, ['rev-parse', 'HEAD']],
      [harness.worktree.path, ['log', '--reverse', '--format=%H', 'base0000..HEAD']],
    ]);
  });

  test('no commits are reported as none', async () => {
    await harness.run([plan(), done]);

    expect(harness.infos).toContain('commits: none');
  });

  test('a failed implementation still reports its commits and closes the worktree', async () => {
    const implement: Handler = () => {
      harness.commits.push('partial0');
      return failedImplementation('tests failed');
    };

    expect(await harness.run([plan(), implement])).toBe(1);

    expect(harness.infos).toContain('commits: partial0');
    expect(harness.errors).toContain('agent reported: Failure: tests failed');
    expect(harness.worktree.closed).toBe(1);
  });

  test('the task and the plan are passed as literal text', async () => {
    const task = 'fix {widget}\n$(do not execute)';
    const text = 'Use {{literal}} and ${unchanged}.\nKeep both lines.';

    expect(await harness.run([plan(text), done], task)).toBe(0);

    expect(harness.runner.prompts).toEqual([`${task}\n\n${PLAN_INSTRUCTIONS}`, `${text}\n\n${IMPLEMENT_INSTRUCTIONS}`]);
  });

  test('a cancellation propagates for the entry point to map to an exit code, still closing the worktree', async () => {
    const interrupt: Handler = () => {
      throw new Cancelled('SIGINT');
    };

    const error = await rejection(harness.run([interrupt]));

    expect(error).toBeInstanceOf(Cancelled);
    expect(harness.worktree.closed).toBe(1);
  });

  test('the entry point exits 130 on SIGINT and 143 on SIGTERM', async () => {
    const codes: number[] = [];

    await runEntrypoint(() => Promise.reject(new Cancelled('SIGINT')), { exit: (code) => codes.push(code) });
    await runEntrypoint(() => Promise.reject(new Cancelled('SIGTERM')), { exit: (code) => codes.push(code) });

    expect(codes).toEqual([130, 143]);
  });

  test('a git failure propagates and closes the worktree', async () => {
    const readGit = (): Promise<string> => Promise.reject(new Error('bad revision'));

    const error = await rejection(harness.run([plan()], 'task', { readGit }));

    expect((error as Error).message).toBe('bad revision');
    expect(harness.runner.prompts).toHaveLength(1);
    expect(harness.worktree.closed).toBe(1);
  });

  test('an unexpected error from a run propagates and closes the worktree', async () => {
    const broken: Handler = () => {
      throw new Error('runner bug');
    };

    const error = await rejection(harness.run([broken]));

    expect((error as Error).message).toBe('runner bug');
    expect(harness.worktree.closed).toBe(1);
  });
});

describe('the default git reader', () => {
  const git = (cwd: string, ...args: string[]): string => {
    const result = Bun.spawnSync(['git', '-C', cwd, ...args], { stderr: 'pipe', stdout: 'pipe' });
    if (result.exitCode !== 0) {
      throw new Error(`git ${args.join(' ')} failed: ${result.stderr.toString()}`);
    }
    return result.stdout.toString().trim();
  };

  test('reads the commits the implementing run made in the worktree', async () => {
    mkdirSync(harness.worktree.path, { recursive: true });
    git(harness.worktree.path, 'init');
    git(harness.worktree.path, 'config', 'user.name', 'Test');
    git(harness.worktree.path, 'config', 'user.email', 'test@example.com');
    git(harness.worktree.path, 'config', 'commit.gpgsign', 'false');
    git(harness.worktree.path, 'commit', '--allow-empty', '-m', 'base');
    const implement: Handler = () => {
      git(harness.worktree.path, 'commit', '--allow-empty', '-m', 'widget');
      return done;
    };

    const dependencies = { ...harness.dependencies(), readGit: undefined };
    harness.runner.script(plan(), implement);

    expect(await main(['add a widget'], dependencies)).toBe(0);
    expect(harness.infos).toContain(`commits: ${git(harness.worktree.path, 'rev-parse', 'HEAD')}`);
  });

  test('rejects with the git error outside a repository, closing the worktree', async () => {
    const dependencies = { ...harness.dependencies(), readGit: undefined };
    harness.runner.script(plan());

    const error = await rejection(main(['task'], dependencies));

    expect((error as Error).message).toContain('git rev-parse HEAD failed');
    expect(harness.worktree.closed).toBe(1);
  });
});

describe('response parsing', () => {
  test.each([
    ['plan\n<completed>Success</completed>', 'plan'],
    ['  line one\nline two  \n<completed> success </completed>\nnoise', 'line one\nline two'],
    ['<completed>SUCCESS</completed>', ''],
  ])('returns only the body of %j', (response, body) => {
    expect(responseBody(response)).toBe(body);
  });

  test.each([
    ['no response', 'no <completed> tag'],
    ['<completed>Failure: blocked</completed>', 'Failure: blocked'],
    ['<completed>unknown</completed>', 'unknown'],
  ])('rejects %j', (response, reason) => {
    expect(() => responseBody(response)).toThrow(reason);
  });
});

describe('the command line', () => {
  const agent = (): AgentBuilder => {
    throw new Error('no run may start on bad input');
  };

  test('missing task prints usage and returns 2', async () => {
    const code = await main([], harness.dependencies({ agent }));

    expect(code).toBe(2);
    expect(harness.stderr.join('')).toContain('Usage: plan_implement [options] <task>');
    expect(harness.stdout).toEqual([]);
  });

  test('extra arguments and unknown options print usage and return 2', async () => {
    expect(await main(['one', 'two'], harness.dependencies({ agent }))).toBe(2);
    expect(await main(['--nope', 'task'], harness.dependencies({ agent }))).toBe(2);

    expect(harness.stderr.join('')).toContain('Usage: plan_implement');
  });

  test('help goes to stdout and returns 0', async () => {
    const code = await main(['--help'], harness.dependencies({ agent }));

    expect(code).toBe(0);
    expect(harness.stdout.join('')).toContain('Usage: plan_implement [options] <task>');
    expect(harness.stderr).toEqual([]);
  });
});

describe('running as a script', () => {
  const script = join(import.meta.dir, '..', '..', 'workflows', 'plan_implement.ts');

  test('bad input prints usage on stderr and sets the process exit status to 2', async () => {
    const child = Bun.spawn([process.execPath, script], { stderr: 'pipe', stdout: 'pipe' });

    expect(await child.exited).toBe(2);
    expect(await new Response(child.stderr).text()).toContain('Usage: plan_implement [options] <task>');
  });
});
