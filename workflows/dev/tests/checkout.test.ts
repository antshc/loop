import { describe, expect, test } from 'bun:test';

import { CliGitService, type Run } from '../checkout.ts';
import { CommandError, runCommand } from '../commands.ts';
import { Commit } from '../records.ts';

const SEP_F = '\u001F';
const SEP_R = '\u001E';

/** A fake `run` that answers a command by its git subcommand and records every call. */
class Script {
  readonly calls: { command: readonly string[]; cwd: string }[] = [];
  get commands(): (readonly string[])[] {
    return this.calls.map((call) => call.command);
  }

  constructor(private readonly outputs: Record<string, CommandError | string> = {}) {}

  readonly run: Run = (command, options) => {
    this.calls.push({ command, cwd: options.cwd });
    const result = this.outputs[command[1] ?? ''] ?? '';
    return result instanceof CommandError ? Promise.reject(result) : Promise.resolve(result);
  };
}

const log = (...commits: [string, string][]): string =>
  commits.map(([sha, subject]) => `${sha}${SEP_F}${subject}${SEP_R}\n`).join('');

describe('the git service', () => {
  test('head and commitsSince parse the log oldest first', async () => {
    const script = new Script({ log: log(['a1', 'first'], ['b2', 'second: body']) });
    const git = new CliGitService(script.run);

    expect(await git.head('/w')).toEqual(new Commit('a1', 'first'));
    expect(await git.commitsSince('/w', 'z9')).toEqual([new Commit('a1', 'first'), new Commit('b2', 'second: body')]);
    expect(script.commands[1]).toContain('--reverse');
    expect(script.commands[1]).toContain('z9..HEAD');
  });

  test('initiative commits drop commits matching only in the body', async () => {
    const git = new CliGitService(new Script({ log: log(['a1', 'ccode(X|1): work'], ['b2', 'other']) }).run);

    expect(await git.initiativeCommits('/w', 'main', 'ccode(X|')).toEqual([new Commit('a1', 'ccode(X|1): work')]);
  });

  test('pushIfAhead pushes a local branch with commits beyond its upstream', async () => {
    const script = new Script({ 'rev-list': 'c3\n' });

    expect(await new CliGitService(script.run).pushIfAhead('/w', 'feat', 'main')).toBe(true);

    expect(script.commands.at(-1)).toEqual(['git', 'push', 'origin', 'feat']);
    expect(script.commands).toContainEqual(['git', 'rev-list', 'origin/feat..feat']);
  });

  test('pushIfAhead does nothing when not ahead or the branch is missing', async () => {
    const script = new Script({ 'rev-list': '' });
    expect(await new CliGitService(script.run).pushIfAhead('/w', 'feat', 'main')).toBe(false);
    expect(script.commands.every((command) => command[1] !== 'push')).toBe(true);

    const missing = new Script({ 'show-ref': new CommandError('git', 1, '') });
    expect(await new CliGitService(missing.run).pushIfAhead('/w', 'feat', 'main')).toBe(false);
  });

  test('remoteBranchExists reports the show-ref outcome', async () => {
    expect(await new CliGitService(new Script().run).remoteBranchExists('/w', 'main')).toBe(true);
    expect(
      await new CliGitService(new Script({ 'show-ref': new CommandError('git', 1, '') }).run).remoteBranchExists('/w', 'm'),
    ).toBe(false);
  });

  test('restore resets hard and cleans untracked files', async () => {
    const script = new Script();

    await new CliGitService(script.run).restore('/w', 'abc');

    expect(script.commands).toEqual([
      ['git', 'reset', '--hard', 'abc'],
      ['git', 'clean', '-fd'],
    ]);
  });

  test('isClean follows the porcelain status', async () => {
    expect(await new CliGitService(new Script({ status: '' }).run).isClean('/w')).toBe(true);
    expect(await new CliGitService(new Script({ status: ' M a.py\n' }).run).isClean('/w')).toBe(false);
  });

  test('fix commits stage edits and preserve their explanation', async () => {
    const script = new Script({ log: `fix1${SEP_F}fix(Basket): total${SEP_F}Verified the basket total.${SEP_R}\n` });

    const commit = await new CliGitService(script.run).commit('/w', 'fix(Basket): total', 'Verified the basket total.');

    expect(commit).toEqual(new Commit('fix1', 'fix(Basket): total', 'Verified the basket total.'));
    expect(script.calls[0]).toEqual({ command: ['git', 'add', '-A'], cwd: '/w' });
    expect(script.calls[1]?.command).toEqual(['git', 'commit', '-m', 'fix(Basket): total', '-m', 'Verified the basket total.']);
  });

  test('changed files include untracked files and rename destinations', async () => {
    const script = new Script({ status: ' M src/Basket.cs\n?? proof/new.json\nR  old.cs -> src/New.cs\n' });

    expect(await new CliGitService(script.run).changedFiles('/w')).toEqual([
      'src/Basket.cs',
      'proof/new.json',
      'src/New.cs',
    ]);
  });
});

describe('runCommand', () => {
  test('returns stdout and rejects with a CommandError on failure', async () => {
    expect((await runCommand(['bun', '-e', "console.log('hi')"])).trim()).toBe('hi');

    const failure = await runCommand(['bun', '-e', 'process.exit(3)']).then(
      () => null,
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(CommandError);
    expect((failure as CommandError).returncode).toBe(3);

    const missing = await runCommand(['definitely-not-a-binary-xyz']).then(
      () => null,
      (error: unknown) => error,
    );
    expect(missing).toBeInstanceOf(CommandError);
    expect((missing as CommandError).returncode).toBeNull();
  });

  test('reports a timeout', async () => {
    const failure = await runCommand(['bun', '-e', 'setTimeout(() => undefined, 5000)'], { timeoutS: 0.2 }).then(
      () => null,
      (error: unknown) => error,
    );

    expect((failure as CommandError).message).toContain('timed out after 0.2s');
  });

  test('a command error names the command, the exit status and the output', () => {
    expect(new CommandError('git push', 1, 'rejected\n').message).toBe('command failed (1): git push\nrejected');
  });
});
