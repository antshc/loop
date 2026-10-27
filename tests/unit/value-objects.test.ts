import { describe, expect, test } from 'bun:test';

import { AgentCliHook } from '../../src/loop/hooks/agent-cli.ts';
import {
  AgentContext,
  AgentProfile,
  AgentRequest,
  AgentResult,
  AgentRunRecord,
  AgentStartMode,
  CliOutcome,
  copilot,
  Instructions,
  LogReading,
  NativeHandle,
  Permissions,
  PostToolAgentCliHook,
  Prompt,
  PromptError,
  Resume,
  RunContext,
  SessionEndAgentCliHook,
  SessionName,
  SessionStartAgentCliHook,
  Start,
  ToolCall,
  UnsupportedPermissions,
  Usage,
  ValueError,
  WorktreeEvent,
} from '../../src/loop/index.ts';
import { userCli } from './support/doubles.ts';

function mutate(value: object, key: string): void {
  (value as Record<string, unknown>)[key] = 'changed';
}

describe('valid value objects are frozen', () => {
  const name = new SessionName('s');
  const handle = new NativeHandle('copilot', 'h');
  const at = new Date(0);
  const usage = new Usage({ aiu: { input: 1 }, apiCalls: 1 });
  const tool = new ToolCall({ arguments: { a: 1 }, callId: 'c', endedAt: at, name: 'bash', ok: true, result: '', startedAt: at });
  const samples: Record<string, object> = {
    AgentContext: new AgentContext({ cwd: '/repo', env: { A: 'b' } }),
    AgentProfile: new AgentProfile(copilot, { addDirs: ['/a'], env: { A: 'b' } }),
    AgentRequest: new AgentRequest('p'),
    AgentResult: new AgentResult('o', name, 0),
    AgentRunRecord: new AgentRunRecord({
      cli: 'copilot',
      cwd: '/repo',
      endedAt: at,
      error: null,
      exitCode: 0,
      model: null,
      nativeId: null,
      output: 'o',
      prompt: 'p',
      reasoningEffort: null,
      session: 's',
      startedAt: at,
      tools: [tool],
      usage,
    }),
    CliOutcome: new CliOutcome('o', handle, 0),
    Instructions: Instructions.fromFile('/a/b.md'),
    LogReading: new LogReading({ model: null, nativeId: 'n', reasoningEffort: null, tools: [tool], usage }),
    NativeHandle: handle,
    Permissions: new Permissions({ denyTools: ['x'], tools: ['read'] }),
    Prompt: new Prompt('{{A}}', { A: 'b' }),
    Resume: new Resume(name, handle),
    RunContext: new RunContext(),
    SessionEndAgentCliHook: new SessionEndAgentCliHook('cmd'),
    SessionName: name,
    Start: new Start(name),
    ToolCall: tool,
    Usage: usage,
    WorktreeEvent: new WorktreeEvent({ at, ok: null, path: '/p', point: 'ready' }),
  };

  for (const [label, value] of Object.entries(samples)) {
    test(`${label} is frozen and cannot be mutated`, () => {
      expect(Object.isFrozen(value)).toBe(true);
      const first = Object.keys(value)[0] ?? '';

      expect(() => {
        mutate(value, first);
      }).toThrow(TypeError);
    });
  }

  test('their collections are frozen too', () => {
    expect(Object.isFrozen(new AgentContext({ env: { A: 'b' } }).env)).toBe(true);
    expect(Object.isFrozen(new AgentContext({ addDirs: ['/a'] }).addDirs)).toBe(true);
    expect(Object.isFrozen(usage.aiu)).toBe(true);
    expect(Object.isFrozen(new Permissions({ tools: ['read'] }).tools)).toBe(true);
  });

  test('profile and context copy mutable environment input', () => {
    const values = { APP: 'cart' };
    const profile = new AgentProfile(copilot, { env: values });
    values.APP = 'changed';

    expect(profile.env).toEqual({ APP: 'cart' });
    expect(() => {
      mutate(profile.env ?? {}, 'APP');
    }).toThrow(TypeError);
  });
});

describe('invalid input throws the same error type as before (ValueError)', () => {
  const invalid: Record<string, () => unknown> = {
    'AgentContext harness mode without a harness directory': () => new AgentContext({ startMode: AgentStartMode.HARNESS }),
    'AgentContext with a NaN timeout': () => new AgentContext({ timeoutSec: NaN }),
    'AgentContext with a negative timeout': () => new AgentContext({ timeoutSec: -1 }),
    'AgentContext with an empty environment key': () => new AgentContext({ env: { '': 'v' } }),
    'AgentContext with an environment key holding =': () => new AgentContext({ env: { 'A=B': 'v' } }),
    'AgentContext with an infinite timeout': () => new AgentContext({ timeoutSec: Infinity }),
    'AgentContext with an unknown start mode': () => new AgentContext({ startMode: 'elsewhere' as AgentStartMode }),
    'AgentContext with a zero timeout': () => new AgentContext({ timeoutSec: 0 }),
    'AgentContext with NUL in an environment value': () => new AgentContext({ env: { APP: 'bad\0value' } }),
    'AgentProfile with an invalid environment': () => new AgentProfile(copilot, { env: { '': 'v' } }),
    'AgentProfile with an invalid timeout': () => new AgentProfile(copilot, { timeoutSec: NaN }),
    'AgentProfile with a relative add dir': () => new AgentProfile(copilot, { addDirs: ['/ok', 'relative'] }),
    'AgentProfile with a relative cwd': () => new AgentProfile(copilot, { cwd: 'relative' }),
    'a hook with a negative timeout': () => new PostToolAgentCliHook('x', -5),
    'a hook with an empty command': () => new SessionStartAgentCliHook(''),
    'a hook with a zero timeout': () => new SessionStartAgentCliHook('x', 0),
    'Instructions with a file URL': () => Instructions.fromUrl('file:///tmp/prompt.md'),
    'Instructions with a malformed URL': () => Instructions.fromUrl('******example.com/a.md'),
    'Instructions with a relative file': () => Instructions.fromFile('relative.md'),
    'Instructions with both file and URL': () => new Instructions({ path: '/a.md', url: 'https://example.com/a.md' }),
    'Instructions with credentials': () => Instructions.fromUrl('https://user:secret@example.com/a.md'),
    'Instructions with neither file nor URL': () => new Instructions(),
    'Permissions with an empty pattern': () => new Permissions({ tools: ['read', ' '] }),
    'Permissions with an unknown tools word': () => new Permissions({ tools: 'some' as 'allow-all' }),
    'Permissions with a text deny list': () => new Permissions({ denyTools: 'x' as unknown as string[] }),
    'SessionName empty': () => new SessionName(''),
    'SessionName with a space': () => new SessionName('a b'),
    'SessionName with a tab': () => new SessionName('a\tb'),
  };

  for (const [label, build] of Object.entries(invalid)) {
    test(label, () => {
      expect(build).toThrow(ValueError);
    });
  }

  test('unsupported permissions are a ValueError too', () => {
    const profile = new AgentProfile(userCli(), { permissions: new Permissions() });

    expect(UnsupportedPermissions.prototype).toBeInstanceOf(ValueError);
    expect(() => {
      profile.validatePermissions();
    }).toThrow(UnsupportedPermissions);
  });

  test('the hook base class cannot be instantiated', () => {
    const base = AgentCliHook as unknown as new (command: string) => unknown;

    expect(() => new base('x')).toThrow(TypeError);
  });

  test('permission arguments conflict with typed permissions', () => {
    for (const flag of ['--allow-all', '--yolo', '--allow-tool=write', '--deny-tool', '--allow-all-tools']) {
      const profile = new AgentProfile(copilot, { args: [flag], permissions: new Permissions() });

      expect(() => {
        profile.validatePermissions();
      }).toThrow(ValueError);
    }
  });
});

describe('value object behavior', () => {
  test('a session name is accepted when it holds no whitespace, and a new one is unique', () => {
    expect(new SessionName('feat/x-1').value).toBe('feat/x-1');
    expect(SessionName.new().value).toMatch(/^loop-[0-9a-f]{32}$/);
    expect(SessionName.new()).not.toEqual(SessionName.new());
  });

  test('a start mode is accepted by its string value', () => {
    expect(new AgentContext({ startMode: 'worktree' }).startMode).toBe(AgentStartMode.WORKTREE);
  });

  test('every persisted and emitted string value is unchanged', () => {
    expect(AgentStartMode).toEqual({ HARNESS: 'harness', WORKTREE: 'worktree' });
  });

  test('usage totals count cached input and every token type of AI units', () => {
    const usage = new Usage({ aiu: { input: 0.5, output: 0.25 }, cacheReadTokens: 20, cacheWriteTokens: 3, inputTokens: 7 });

    expect(usage.totalInputTokens).toBe(30);
    expect(usage.totalAiu).toBe(0.75);
  });

  test('a prompt renders its placeholders and rejects a missing argument', () => {
    expect(new Prompt('Do {{ TASK }} in {{WHERE}}.', { TASK: 'x', WHERE: 'y' }).render()).toBe('Do x in y.');
    expect(() => new Prompt('{{MISSING}}', {}).render()).toThrow(PromptError);
  });

  test('with() returns an updated copy and leaves the original unchanged', () => {
    const context = new AgentContext({ cwd: '/a', timeoutSec: 5 });
    const moved = context.with({ cwd: '/b', timeoutSec: null });

    expect([context.cwd, context.timeoutSec]).toEqual(['/a', 5]);
    expect([moved.cwd, moved.timeoutSec]).toEqual(['/b', null]);
    expect(new AgentRequest('p', 'n').with({ prompt: 'q' })).toEqual(new AgentRequest('q', 'n'));
  });
});
