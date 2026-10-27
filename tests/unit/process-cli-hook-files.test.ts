import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  AgentContext,
  AgentProfile,
  AgentRequest,
  copilot,
  ProcessCliRunner,
  RunContext,
  SessionName,
  SessionStartAgentCliHook,
  Start,
} from '../../src/loop/index.ts';
import { Cancelled } from '../../src/loop/run/cancelled.ts';
import { type ProcessOptions, type ProcessResult, type ProcessRunner } from '../../src/loop/run/process.ts';
import { rejectionOf } from './support/doubles.ts';

interface Seen {
  env: ProcessOptions['env'];
  files: string[];
}

let directory = '';

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-hook-files-'));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

const hooksDirectory = (): string => join(directory, '.github', 'hooks');
const listed = (): string[] => (existsSync(hooksDirectory()) ? readdirSync(hooksDirectory()) : []);

/** Answers the git exclude lookup and records what exists on disk while the agent command runs. */
class Processes implements ProcessRunner {
  readonly seen: Seen[] = [];

  constructor(
    private readonly exclude: string,
    private readonly failure: Error | null = null,
    private readonly gitFound = true,
  ) {}

  run(argv: readonly string[], options?: ProcessOptions): Promise<ProcessResult> {
    if (argv[0] === 'git') {
      const found = { argv, exitCode: this.gitFound ? 0 : 128, signal: null, stderr: '', stdout: this.gitFound ? `${this.exclude}\n` : '' };
      return Promise.resolve(found);
    }
    this.seen.push({ env: options?.env, files: listed() });
    if (this.failure !== null) {
      return Promise.reject(this.failure);
    }
    return Promise.resolve({ argv, exitCode: 0, signal: null, stderr: '', stdout: '' });
  }

  start(): never {
    throw new Error('not used');
  }
}

const context = (): RunContext => new RunContext(new AgentContext({ cwd: directory }), [new SessionStartAgentCliHook('c')]);

const run = (processes: ProcessRunner): ReturnType<ProcessCliRunner['run']> =>
  new ProcessCliRunner(processes).run(new AgentProfile(copilot), new AgentRequest('x'), new Start(new SessionName('s')), context());

describe('the hook configuration file', () => {
  test('exists while the CLI runs, is removed after, and is excluded from git once', async () => {
    const exclude = join(directory, 'info', 'exclude');
    const processes = new Processes(exclude);

    await run(processes);
    await run(processes);

    for (const { env, files } of processes.seen) {
      expect(files).toHaveLength(1);
      expect(files[0]).toMatch(/^loop-[0-9a-f]{8}\.json$/);
      expect(env?.GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS).toBe('true');
    }
    expect(listed()).toEqual([]);
    expect(readFileSync(exclude, 'utf8').split('\n').filter((line) => line === '.github/hooks/loop-*.json')).toHaveLength(1);
  });

  test('has the generated hook content while the CLI runs', async () => {
    const processes = new Processes(join(directory, 'exclude'));
    let content = '';
    const reading: ProcessRunner = {
      run: (argv, options) => {
        const file = listed()[0];
        content = file === undefined ? '' : readFileSync(join(hooksDirectory(), file), 'utf8');
        return processes.run(argv, options);
      },
      start: () => processes.start(),
    };

    await run(reading);

    expect(JSON.parse(content)).toMatchObject({ hooks: { sessionStart: [{ type: 'command' }] }, version: 1 });
  });

  test('is removed when the run fails', async () => {
    const rejected = await rejectionOf(run(new Processes(join(directory, 'exclude'), new Error('boom'))));

    expect(rejected).toMatchObject({ message: 'boom' });
    expect(listed()).toEqual([]);
  });

  test('is removed when the run is cancelled', async () => {
    const rejected = await rejectionOf(run(new Processes(join(directory, 'exclude'), new Cancelled('SIGTERM'))));

    expect(rejected).toBeInstanceOf(Cancelled);
    expect(listed()).toEqual([]);
  });

  test('without a git repository the file is still written and removed and nothing is excluded', async () => {
    const processes = new Processes(join(directory, 'exclude'), null, false);

    await run(processes);

    expect(processes.seen[0]?.files).toHaveLength(1);
    expect(listed()).toEqual([]);
    expect(existsSync(join(directory, 'exclude'))).toBe(false);
  });

  test('is not written when the CLI has no file wiring', async () => {
    const processes = new Processes(join(directory, 'exclude'));
    const plain = new RunContext(new AgentContext({ cwd: directory }));

    await new ProcessCliRunner(processes).run(new AgentProfile(copilot), new AgentRequest('x'), new Start(new SessionName('s')), plain);

    expect(processes.seen[0]?.files).toEqual([]);
  });
});
