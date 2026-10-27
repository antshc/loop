import { describe, expect, test } from 'bun:test';
import { fileURLToPath } from 'node:url';

import {
  AgentContext,
  AgentProfile,
  AgentRequest,
  AgentStartMode,
  codex,
  CodexCli,
  copilot,
  CopilotCli,
  CopilotJson,
  NativeHandle,
  Permissions,
  PromptSubmittedAgentCliHook,
  Resume,
  RunContext,
  SessionEndAgentCliHook,
  SessionHandleMissing,
  SessionName,
  SessionStartAgentCliHook,
  Start,
  type Turn,
} from '../../src/loop/index.ts';
import { applyStartMode, directoryInstruction } from '../../src/loop/run/launch.ts';
import { golden } from './support/fixtures.ts';

interface CommandsFixture {
  codexWiringArgs: string[];
  commands: { argv: string[]; label: string }[];
  copilotWiring: {
    env: Record<string, string>;
    gitExcludes: string[];
    maskedContent: string;
    path: string;
    shimTails: Record<string, string[]>;
  };
}

interface StartModeFixture {
  harness: { addDirs: string[]; cwd: string; name: string; prompt: string };
  instructions: { instruction: string; worktree: string }[];
}

const SHIM = fileURLToPath(new URL('../../src/loop/hooks/shim.ts', import.meta.url));
const REPO = '/repo';
const name = new SessionName('n');
const context = new RunContext(new AgentContext({ cwd: REPO }));
const withDirs = new RunContext(new AgentContext({ addDirs: ['/docs', '/shared'], cwd: REPO }));
const planner = new AgentProfile(copilot, { args: ['--x'], model: 'claude-opus-4.5', reasoningEffort: 'high' });
const developer = new AgentProfile(codex, { args: ['--x'], model: 'gpt-5-codex', reasoningEffort: 'high' });
const bypass = new CodexCli(undefined, { hookTrustBypass: true });
const hooks = [
  new SessionStartAgentCliHook('start'),
  new SessionEndAgentCliHook('end', 9),
  new PromptSubmittedAgentCliHook('my cmd', 7),
];
const hookContext = new RunContext(new AgentContext({ cwd: REPO }), hooks);
const copilotHandle = new NativeHandle('copilot', 'h');
const codexHandle = new NativeHandle('codex', 't1');

const mask = (text: string): string => text.replaceAll(`${process.execPath} ${SHIM} `, '<SHIM> ');

const built: Readonly<Record<string, () => string[]>> = {
  'codex hooks resume': () =>
    bypass.command(new AgentRequest('p'), new AgentProfile(bypass), new Resume(name, codexHandle), hookContext),
  'codex hooks start': () => bypass.command(new AgentRequest('p'), new AgentProfile(bypass), new Start(name), hookContext),
  'codex resume': () => codex.command(new AgentRequest('p'), developer, new Resume(name, codexHandle), withDirs),
  'codex start': () => codex.command(new AgentRequest('p'), developer, new Start(name), context),
  'copilot allow-all tools': () =>
    copilot.command(
      new AgentRequest('p'),
      new AgentProfile(copilot, { permissions: new Permissions({ denyTools: ['shell(rm)'], tools: 'allow-all' }) }),
      new Start(name),
      context,
    ),
  'copilot allowed tools': () =>
    copilot.command(
      new AgentRequest('p'),
      new AgentProfile(copilot, {
        permissions: new Permissions({ denyTools: ['shell(git push)'], tools: ['read', 'shell(git diff)'] }),
      }),
      new Start(name),
      context,
    ),
  'copilot dirs and context': () =>
    copilot.command(new AgentRequest('p'), new AgentProfile(copilot, { context: 'ctx' }), new Start(name), withDirs),
  'copilot json transport': () =>
    new CopilotCli(undefined, { transport: new CopilotJson() }).command(new AgentRequest('p'), planner, new Start(name), context),
  'copilot named start': () => copilot.command(new AgentRequest('p', 'loop(7) Fix it'), planner, new Start(name), context),
  'copilot resume': () => copilot.command(new AgentRequest('p'), planner, new Resume(name, copilotHandle), context),
  'copilot start': () => copilot.command(new AgentRequest('p'), planner, new Start(name), context),
};

const commands = await golden<CommandsFixture>('commands.json');
const fixture = await golden<StartModeFixture>('start-mode.json');

describe('CLI commands equal the golden fixture', () => {
  for (const { argv, label } of commands.commands) {
    test(label, () => {
      const build = built[label];

      expect(build?.().map(mask)).toEqual(argv);
    });
  }

  test('every golden command is covered', () => {
    expect(commands.commands.map((item) => item.label).sort()).toEqual(Object.keys(built).sort());
  });
});

describe('Copilot hook wiring equals the golden fixture', () => {
  const wiring = copilot.hookWiring(hooks, new Start(name));

  test('writes one repository hook file, git-excluded and trusted for prompt mode', () => {
    expect([...wiring.files.keys()]).toEqual([commands.copilotWiring.path]);
    expect({ ...wiring.env }).toEqual(commands.copilotWiring.env);
    expect([...wiring.gitExcludes]).toEqual(commands.copilotWiring.gitExcludes);
  });

  test('the hook file content is formatted exactly as before, shim command aside', () => {
    const [content = ''] = wiring.files.values();
    const parsed = JSON.parse(content) as { hooks: Record<string, { bash: string }[]> };
    const bash = Object.values(parsed.hooks).flat();
    const masked = bash.reduce((text, item) => text.replace(JSON.stringify(item.bash), '"<SHIM>"'), content);

    expect(masked).toBe(commands.copilotWiring.maskedContent);
  });

  test('each event runs the shim with the CLI, point, session and the user command', () => {
    const [content = ''] = wiring.files.values();
    const parsed = JSON.parse(content) as { hooks: Record<string, { bash: string }[]> };
    const tails = Object.fromEntries(
      Object.entries(parsed.hooks).map(([event, entries]) => [event, entries.map((entry) => entry.bash.split(`${SHIM} `)[1])]),
    );

    expect(tails).toEqual(commands.copilotWiring.shimTails);
  });

  test('Codex passes hooks as -c overrides and bypasses hook trust', () => {
    expect(bypass.hookWiring(hooks, new Start(name)).args.map(mask)).toEqual(commands.codexWiringArgs);
  });
});

describe('Copilot and Codex adapters', () => {
  test('Codex claims hook points only with the trust bypass', () => {
    expect(codex.hookPoints.size).toBe(0);
    expect(bypass.hookPoints.size).toBe(9);
    expect(copilot.hookPoints.size).toBe(9);
  });

  test('Copilot names a new session after the request name, but a resume only by handle', () => {
    const named = new AgentRequest('p', 'loop(7) Fix it');

    expect(copilot.command(named, planner, new Start(name), context).slice(5, 7)).toEqual(['--name', 'loop(7) Fix it']);
    expect(copilot.command(named, planner, new Resume(name, copilotHandle), context)).not.toContain('loop(7) Fix it');
  });

  test('Copilot assigns the handle from the session name; Codex assigns its own', () => {
    expect(copilot.handleForNew(name)).toEqual(new NativeHandle('copilot', 'n'));
    expect(codex.handleForNew()).toBeNull();
  });

  test('Codex parse needs a thread.started event unless resuming', () => {
    expect(() => codex.parse('', new Start(name), 0)).toThrow(SessionHandleMissing);
    expect(codex.parse('', new Resume(name, codexHandle), 0).handle).toEqual(codexHandle);
    const stdout = codex.transport.encode('answer', 't9');
    expect(codex.parse(stdout, new Start(name), 0)).toMatchObject({ exitCode: 0, handle: { cli: 'codex', value: 't9' }, output: 'answer' });
  });

  test('Copilot parse lets the process exit code win and falls back to the stream exit code', () => {
    const json = new CopilotCli(undefined, { transport: new CopilotJson() });
    const stdout = '{"type":"result","sessionId":"s","exitCode":3}';
    const turn: Turn = new Start(name);

    expect(json.parse(stdout, turn, 0).exitCode).toBe(3);
    expect(json.parse(stdout, turn, 1).exitCode).toBe(1);
  });

  test('permissions replace Copilot broad defaults and emit deny rules', () => {
    const profile = new AgentProfile(copilot, {
      permissions: new Permissions({ denyTools: ['shell(git push)'], tools: ['read', 'shell(git diff)'] }),
    });
    const command = copilot.command(new AgentRequest('Review'), profile, new Start(name), new RunContext());

    expect(command).not.toContain('--allow-all');
    expect(command).not.toContain('--allow-all-tools');
    expect(command[command.indexOf('--deny-tool') + 1]).toBe('shell(git push)');
    expect(command.filter((part) => part === '--allow-tool')).toHaveLength(2);
  });
});

describe('harness start mode equals the golden fixture', () => {
  for (const { instruction, worktree } of fixture.instructions) {
    test(`the instruction names and quotes ${worktree}`, () => {
      expect(directoryInstruction(worktree)).toBe(instruction);
    });
  }

  test('launches in the harness, grants the worktree and prefixes the prompt', () => {
    const agent = new AgentContext({
      addDirs: ['/extra'],
      cwd: '/repo',
      harnessDir: '/harness',
      startMode: AgentStartMode.HARNESS,
    });

    const [launched, request] = applyStartMode(agent, new AgentRequest('do it', 'loop(1) Title'));

    expect({ addDirs: [...launched.addDirs], cwd: launched.cwd, name: request.name, prompt: request.prompt }).toEqual(
      fixture.harness,
    );
  });
});
