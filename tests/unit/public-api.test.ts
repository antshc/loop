import { describe, expect, test } from 'bun:test';

import * as loop from '../../src/loop/index.ts';
import { Agent, AgentRequest, copilot, DEFAULT, DockerRuntime, FakeCliRunner, FakeReply } from '../../src/loop/index.ts';

const FORBIDDEN_PARAMETERS = /signal|stream|cancel|abort|timeout|onOutput|onData/i;

describe('the single public entry point', () => {
  test('exports the Python names, with the CLIs and the default profile', () => {
    const names = Object.keys(loop);

    for (const name of [
      'Agent',
      'AgentBuilder',
      'AgentContext',
      'AgentProfile',
      'AgentRequest',
      'AgentResult',
      'AgentStartMode',
      'CliAgentClient',
      'CodexCli',
      'CodexJson',
      'CopilotCli',
      'CopilotJson',
      'CopilotLogReader',
      'CopilotText',
      'DEFAULT',
      'DirectoryGit',
      'DockerAgent',
      'DockerRuntime',
      'FakeCliRunner',
      'GitAgent',
      'GitOptions',
      'Instructions',
      'InstructionsError',
      'LogReading',
      'MemorySessionStore',
      'NativeHandle',
      'ObservingCliRunner',
      'Permissions',
      'ProcessCliRunner',
      'Prompt',
      'PromptError',
      'Resume',
      'Role',
      'RunContext',
      'SessionCliMismatch',
      'SessionHandleMissing',
      'SessionName',
      'Start',
      'ToolCall',
      'Usage',
      'UnsupportedAgentCliHookPoint',
      'UnsupportedPermissions',
      'ValueError',
      'WorktreeEvent',
      'codex',
      'copilot',
      'fakeAgent',
    ]) {
      expect(names).toContain(name);
    }
  });

  test('Loop exports the copilot CLI and a default profile on it, while profiles stay user-owned', () => {
    expect(DEFAULT.cli).toBe(copilot);
    expect(Object.keys(loop).filter((name) => /^(PLANNER|DEVELOPER|REVIEWER|ADDRESS|MAINTAINER)$/.test(name))).toEqual([]);
  });

  test('is the package entry', async () => {
    const manifest = (await Bun.file(new URL('../../package.json', import.meta.url)).json()) as { exports: Record<string, string> };

    expect(manifest.exports['.']).toBe('./src/loop/index.ts');
  });
});

describe('the API offers no streaming, per-run timeout or cancellation parameter', () => {
  const client = Agent({ dryRun: true }).create();
  const fakeRunner = new FakeCliRunner(() => new FakeReply('x'));

  test('a client runs with a request and an optional context only', () => {
    expect(client.run.length).toBeLessThanOrEqual(2);
    expect(client.run.toString()).not.toMatch(FORBIDDEN_PARAMETERS);
  });

  test('no builder, runner or wrapper method takes such a parameter', () => {
    const subjects: [string, object][] = [
      ['AgentBuilder', Object.getPrototypeOf(Agent({ dryRun: true })) as object],
      ['CliAgentClient', Object.getPrototypeOf(client) as object],
      ['ProcessCliRunner', loop.ProcessCliRunner.prototype],
      ['FakeCliRunner', Object.getPrototypeOf(fakeRunner) as object],
      ['DockerRuntime', DockerRuntime.prototype],
    ];

    for (const [label, prototype] of subjects) {
      for (const key of Object.getOwnPropertyNames(prototype)) {
        const member = Reflect.get(prototype, key) as unknown;
        if (typeof member === 'function' && key !== 'constructor') {
          const parameters = /\(([^)]*)\)/.exec(member.toString())?.[1] ?? '';
          expect({ forbidden: FORBIDDEN_PARAMETERS.test(parameters), key, label }).toEqual({ forbidden: false, key, label });
        }
      }
    }
  });

  test('the only timeout is the agent setting, validated on the context and profile', () => {
    expect(() => new loop.AgentContext({ timeoutSec: -1 })).toThrow(loop.ValueError);
    expect(Object.keys(new AgentRequest('p')).sort()).toEqual(['name', 'prompt']);
  });
});
