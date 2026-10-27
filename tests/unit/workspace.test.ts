import { afterEach, beforeEach, describe, expect, type Mock, spyOn, test } from 'bun:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { setLogSink } from '../../src/loop/diagnostics.ts';
import {
  Agent,
  AgentBuilder,
  AgentContext,
  AgentProfile,
  AgentRequest,
  type AgentRunRecord,
  BranchStrategy,
  codex,
  copilot,
  DockerRuntime,
  GitAgent,
  GitOptions,
  type GitService,
  MemorySessionStore,
  NativeHandle,
  ObservingGitService,
  repoAgent,
  Resume,
  type RunObserver,
  SessionName,
  Start,
  ValueError,
  type WorktreeEvent,
} from '../../src/loop/index.ts';
import { RecordingDocker, RecordingGit, RecordingRunner, rejectionOf } from './support/doubles.ts';

const PLANNER = new AgentProfile(copilot, { model: 'claude-opus-4.5', reasoningEffort: 'high' });
const DEVELOPER = new AgentProfile(codex, { model: 'gpt-5-codex', reasoningEffort: 'high' });

class RecordingObserver implements RunObserver {
  readonly records: AgentRunRecord[] = [];
  readonly worktrees: WorktreeEvent[] = [];

  agentRun(record: AgentRunRecord): void {
    this.records.push(record);
  }

  worktree(event: WorktreeEvent): void {
    this.worktrees.push(event);
  }
}

const points = (observer: RecordingObserver): [string, boolean | null][] => observer.worktrees.map((event) => [event.point, event.ok]);

/** A git service that opens an existing directory and can fail on removal, like a strategy whose merge fails. */
class FakeGit implements GitService {
  constructor(
    private readonly path: string,
    private readonly removeError: Error | null = null,
  ) {}

  async open<T>(_cwd: string, _options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    const result = await body(this.path);
    if (this.removeError !== null) {
      throw this.removeError;
    }
    return result;
  }
}

describe('the observing git service', () => {
  let directory = '';

  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), 'loop-observing-git-'));
  });

  afterEach(() => {
    rmSync(directory, { force: true, recursive: true });
  });

  test('wraps a successful run in ready, removing and retained events and returns the body result', async () => {
    const observer = new RecordingObserver();

    const result = await new ObservingGitService(new FakeGit(directory), observer).open(directory, new GitOptions(), (path) =>
      Promise.resolve(path),
    );

    expect(result).toBe(directory);
    expect(points(observer)).toEqual([
      ['ready', null],
      ['removing', true],
      ['retained', true],
    ]);
  });

  test('reports a failed body and propagates it', async () => {
    const observer = new RecordingObserver();
    const failure = new Error('agent failed');

    const rejected = await rejectionOf(
      new ObservingGitService(new FakeGit(directory), observer).open(directory, new GitOptions(), () => Promise.reject(failure)),
    );

    expect(rejected).toBe(failure);
    expect(points(observer)).toEqual([
      ['ready', null],
      ['removing', false],
      ['retained', false],
    ]);
  });

  test('a removal failure is not reported as ok', async () => {
    const observer = new RecordingObserver();

    const rejected = await rejectionOf(
      new ObservingGitService(new FakeGit(directory, new Error('merge')), observer).open(directory, new GitOptions(), () => Promise.resolve()),
    );

    expect(rejected).toMatchObject({ message: 'merge' });
    expect(points(observer)).toEqual([
      ['ready', null],
      ['removing', true],
      ['retained', false],
    ]);
  });

  test('reports removed when the directory is gone afterwards', async () => {
    const observer = new RecordingObserver();
    const gone = join(directory, 'gone');

    await new ObservingGitService(new FakeGit(gone), observer).open(directory, new GitOptions(), () => Promise.resolve());

    expect(points(observer).at(-1)).toEqual(['removed', true]);
  });

  test('a failing observer never breaks the worktree and is logged', async () => {
    const logged: string[] = [];
    const previous = setLogSink((record) => logged.push(`${record.level}: ${record.message}`));
    const broken: RunObserver = {
      agentRun: () => Promise.reject(new Error('sink down')),
      worktree: () => {
        throw new Error('sink down');
      },
    };

    try {
      const path = await new ObservingGitService(new FakeGit(directory), broken).open(directory, new GitOptions(), (opened) =>
        Promise.resolve(opened),
      );

      expect(path).toBe(directory);
      expect(logged.filter((line) => line.startsWith('warning: observer failed on worktree'))).toHaveLength(3);
    } finally {
      setLogSink(previous);
    }
  });

  test('an observer may be asynchronous', async () => {
    const seen: string[] = [];
    const slow: RunObserver = {
      agentRun: () => undefined,
      worktree: async (event) => {
        await Bun.sleep(1);
        seen.push(event.point);
      },
    };

    await new ObservingGitService(new FakeGit(directory), slow).open(directory, new GitOptions(), () => Promise.resolve());

    expect(seen).toEqual(['ready', 'removing', 'retained']);
  });

  test('the builder reports the worktree and the agent run', async () => {
    const observer = new RecordingObserver();
    const runner = new RecordingRunner();
    const builder = new AgentBuilder(runner, new FakeGit(directory), new DockerRuntime(), new AgentContext({ cwd: directory }));

    await builder
      .withGit()
      .withObserver(observer, { readers: [] })
      .open(async (workspace) => {
        await workspace.agent().run(new AgentRequest('go'));
      });

    expect(observer.worktrees.map((event) => event.point)).toEqual(['ready', 'removing', 'retained']);
    expect(observer.records).toHaveLength(1);
    expect(observer.records[0]?.cwd).toBe(directory);
  });
});

describe('the workspace scope', () => {
  let events: string[] = [];
  let runner = new RecordingRunner();
  let subject = new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events));

  beforeEach(() => {
    events = [];
    runner = new RecordingRunner(events);
    subject = new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events)).withGit();
  });

  test('returns the body result and shares one worktree between profiles of different CLIs', async () => {
    let path = '';

    const result = await subject.open(async (workspace) => {
      path = workspace.path;
      await workspace.agent(PLANNER).run(new AgentRequest('plan'));
      await workspace.agent(DEVELOPER).run(new AgentRequest('implement'));
      return 'finished';
    });

    expect(result).toBe('finished');
    expect(events).toEqual(['worktree.enter', 'cli.run', 'cli.run', 'worktree.exit']);
    expect(runner.contexts.map((context) => context.agent.cwd)).toEqual([path, path]);
    expect(runner.profiles.map((profile) => profile.cli.name)).toEqual(['copilot', 'codex']);
  });

  test('sessions are per CLI inside one worktree', async () => {
    const name = new SessionName('n');

    await subject.withSession().open(name, async (workspace) => {
      await workspace.agent(DEVELOPER).run(new AgentRequest('one'));
      await workspace.agent(PLANNER).run(new AgentRequest('two'));
      await workspace.agent(DEVELOPER).run(new AgentRequest('three'));
      await workspace.agent(PLANNER).run(new AgentRequest('four'));
    });

    const [one, two, three, four] = runner.turns;
    expect(one).toEqual(new Start(name));
    expect(two).toEqual(new Start(name));
    expect(three).toEqual(new Resume(name, new NativeHandle('codex', 'codex1')));
    expect(four).toEqual(new Resume(name, new NativeHandle('copilot', 'copilot2')));
  });

  test('an agent session override starts fresh', async () => {
    await subject.withSession().open(new SessionName('n'), async (workspace) => {
      await workspace.agent(PLANNER).run(new AgentRequest('one'));
      await workspace.agent(PLANNER, SessionName.new()).run(new AgentRequest('review'));
      await workspace.agent(PLANNER).run(new AgentRequest('two'));
    });

    expect(runner.turns[1]).toBeInstanceOf(Start);
    expect(runner.turns[1]?.name).not.toEqual(new SessionName('n'));
    expect(runner.turns[2]).toBeInstanceOf(Resume);
  });

  test('a session needs withSession()', async () => {
    expect(await rejectionOf(subject.open(new SessionName('n'), () => Promise.resolve()))).toBeInstanceOf(ValueError);
    await subject.open(async (workspace) => {
      expect(() => workspace.agent(PLANNER, new SessionName('x'))).toThrow(ValueError);
      await Promise.resolve();
    });
  });

  test('a failing body still exits the worktree and its error is rethrown', async () => {
    const failure = new Error('boom');

    const rejected = await rejectionOf(subject.open(() => Promise.reject(failure)));

    expect(rejected).toBe(failure);
    expect(events).toEqual(['worktree.enter', 'worktree.exit']);
  });

  test('the workspace is closed once the scope ends', async () => {
    const workspace = await subject.open((opened) => Promise.resolve(opened));

    expect(() => workspace.agent()).toThrow('worktree closed');
  });

  test('without git the workspace is the cwd and no worktree is touched', async () => {
    const plain = new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events), new AgentContext({ cwd: '/repo' }));

    await plain.open((workspace) => {
      expect(workspace.path).toBe('/repo');
      return Promise.resolve();
    });

    expect(events).toEqual([]);
  });

  test('shares an in-memory session store across clients created from the workspace', async () => {
    const store = new MemorySessionStore();
    const shared = new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events), null, store).withGit().withSession();

    await shared.open(new SessionName('s'), async (workspace) => {
      await workspace.agent(PLANNER).run(new AgentRequest('one'));
    });

    expect(await store.get(new SessionName('s'), 'copilot')).not.toBeNull();
  });
});

describe('dry-run worktrees', () => {
  let output: string[] = [];
  let spy: Mock<typeof console.log> | null = null;

  beforeEach(() => {
    output = [];
    spy = spyOn(console, 'log').mockImplementation((...args: unknown[]) => {
      output.push(args.map(String).join(' '));
    });
  });

  afterEach(() => {
    spy?.mockRestore();
  });

  test('a branch logs every step without running git', async () => {
    const agent = Agent({ dryRun: true }).withGit(new GitOptions({ rootPath: 'wt', strategy: new BranchStrategy('loop/a') })).withDocker();

    await agent.create().run(new AgentRequest('hello'));

    expect(output.every((line) => line.startsWith('[dry-run] '))).toBe(true);
    const text = output.join('\n');
    for (const expected of ['fetch --all --prune', 'worktree add', 'docker image=', 'copilot -p hello', 'worktree remove']) {
      expect(text).toContain(expected);
    }
  });

  test('a workspace logs both CLIs between one worktree pair', async () => {
    const agent = Agent({ dryRun: true }).withGit(new GitOptions({ rootPath: 'wt', strategy: new BranchStrategy('loop/a') }));

    await agent.open(async (workspace) => {
      await workspace.agent(PLANNER).run(new AgentRequest('plan'));
      await workspace.agent(DEVELOPER).run(new AgentRequest('implement'));
    });

    const text = output.join('\n');
    expect(text.split('worktree add')).toHaveLength(2);
    expect(text.split('worktree remove')).toHaveLength(2);
    expect(text.indexOf('copilot -p plan')).toBeLessThan(text.indexOf('codex exec --json'));
    expect(text).toContain('--model gpt-5-codex');
  });

  test('a workspace with a session logs the name and resumes it', async () => {
    const agent = Agent({ dryRun: true }).withSession();

    await agent.open(new SessionName('ticket-123'), async (workspace) => {
      await workspace.agent(PLANNER).run(new AgentRequest('plan'));
      await workspace.agent(DEVELOPER).run(new AgentRequest('a'));
      await workspace.agent(DEVELOPER).run(new AgentRequest('b'));
    });

    const text = output.join('\n');
    expect(text).toContain('--name ticket-123');
    expect(text).toContain('resume');
  });

  test('the head strategy logs no git commands', async () => {
    await Agent({ dryRun: true }).withGit().create().run(new AgentRequest('hello'));

    expect(output.join('\n')).not.toContain('git -C');
  });
});

describe('the repository agent', () => {
  test('is a worktree-isolated client, with or without a session', () => {
    expect(repoAgent('app')).toBeInstanceOf(GitAgent);
    expect(repoAgent('app', 'feature', new SessionName('s'))).toBeInstanceOf(GitAgent);
  });
});
