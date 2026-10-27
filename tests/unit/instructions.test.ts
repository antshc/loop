import { afterAll, beforeAll, beforeEach, describe, expect, spyOn, test } from 'bun:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  Agent,
  AgentBuilder,
  AgentProfile,
  AgentRequest,
  copilot,
  DockerRuntime,
  Instructions,
  InstructionsError,
  MemorySessionStore,
  SessionName,
} from '../../src/loop/index.ts';
import { RecordingRunner, rejectionOf } from './support/doubles.ts';

const ONE_MIB = 1024 * 1024;
const hits: Record<string, number> = {};
let directory = '';
let server: ReturnType<typeof Bun.serve>;
let runner = new RecordingRunner();

function bind(profile: AgentProfile, store?: MemorySessionStore): ReturnType<AgentBuilder['create']> {
  const builder = new AgentBuilder(runner, null, new DockerRuntime(), null, store ?? null);
  return (store === undefined ? builder : builder.withSession()).create(profile, store === undefined ? null : new SessionName('cart'));
}

const url = (path: string): string => `http://localhost:${String(server.port)}${path}`;

beforeAll(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-instructions-'));
  server = Bun.serve({
    fetch(request) {
      const { pathname } = new URL(request.url);
      hits[pathname] = (hits[pathname] ?? 0) + 1;
      switch (pathname) {
        case '/big':
          return new Response('x'.repeat(ONE_MIB + 1), { headers: { 'Content-Type': 'text/plain' } });
        case '/empty':
          return new Response('', { headers: { 'Content-Type': 'text/plain' } });
        case '/html':
          return new Response('<html>Login</html>', { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
        case '/invalid':
          return new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'text/plain' } });
        case '/ok':
          return new Response('Review cart.', { headers: { 'Content-Type': 'text/plain' } });
        default:
          return new Response('nope', { status: 404 });
      }
    },
    port: 0,
  });
});

afterAll(async () => {
  await server.stop(true);
  rmSync(directory, { force: true, recursive: true });
});

beforeEach(() => {
  runner = new RecordingRunner();
  for (const key of Object.keys(hits)) {
    Reflect.deleteProperty(hits, key);
  }
});

describe('profile instructions', () => {
  test('are optional and leave the request unchanged, name included', async () => {
    const request = new AgentRequest('Review cart', 'cart-review');

    await bind(new AgentProfile(copilot)).run(request);

    expect(runner.requests).toEqual([request]);
  });

  test('a file definition is inert and each client snapshots the text when it first runs', async () => {
    const path = join(directory, 'developer.md');
    const profile = new AgentProfile(copilot, { instructions: Instructions.fromFile(path) });
    writeFileSync(path, 'Implement and verify.');
    const client = bind(profile, new MemorySessionStore());

    await client.run(new AgentRequest('Add cart item'));
    writeFileSync(path, 'Changed instructions.');
    await client.run(new AgentRequest('Fix quantity'));
    await bind(profile).run(new AgentRequest('Review cart', 'review'));

    expect(runner.requests[0]?.prompt).toBe('## Agent instructions\nImplement and verify.\n\n## Task\nAdd cart item');
    expect(runner.requests[1]?.prompt).toContain('Implement and verify.');
    expect(runner.requests[2]?.prompt).toContain('Changed instructions.');
    expect(runner.requests[2]?.name).toBe('review');
  });

  test('literal text is not expanded as a template', async () => {
    await bind(new AgentProfile(copilot, { instructions: 'Use {{RULE}}.' })).run(new AgentRequest('{{TASK}}'));

    expect(runner.requests[0]?.prompt).toContain('{{RULE}}');
    expect(runner.requests[0]?.prompt).toContain('{{TASK}}');
  });

  test('a missing file fails before a session is registered or the runner is touched', async () => {
    const store = new MemorySessionStore();
    const profile = new AgentProfile(copilot, { instructions: Instructions.fromFile(join(directory, 'missing.md')) });

    const rejected = await rejectionOf(bind(profile, store).run(new AgentRequest('go')));

    expect(rejected).toBeInstanceOf(InstructionsError);
    expect(await store.get(new SessionName('cart'), 'copilot')).toBeNull();
    expect(runner.requests).toEqual([]);
  });

  test('a URL is fetched only when a client first runs, and only once', async () => {
    const profile = new AgentProfile(copilot, { instructions: Instructions.fromUrl(url('/ok')) });
    expect(hits['/ok']).toBeUndefined();

    const client = bind(profile);
    expect(hits['/ok']).toBeUndefined();
    await client.run(new AgentRequest('First'));
    await client.run(new AgentRequest('Second'));

    expect(hits['/ok']).toBe(1);
    expect(runner.requests.every((request) => request.prompt.includes('Review cart.'))).toBe(true);
  });

  for (const path of ['/empty', '/invalid', '/html', '/big', '/missing']) {
    test(`unusable remote content ${path} is rejected explicitly`, async () => {
      const profile = new AgentProfile(copilot, { instructions: Instructions.fromUrl(url(path)) });

      expect(await rejectionOf(bind(profile).run(new AgentRequest('go')))).toBeInstanceOf(InstructionsError);
      expect(runner.requests).toEqual([]);
    });
  }

  test('a network failure keeps its cause and a later run retries', async () => {
    const profile = new AgentProfile(copilot, { instructions: Instructions.fromUrl('http://localhost:1/never.md') });
    const client = bind(profile);

    const rejected = await rejectionOf(client.run(new AgentRequest('go')));

    expect(rejected).toBeInstanceOf(InstructionsError);
    expect((rejected as InstructionsError).cause).toBeInstanceOf(Error);
    expect(await rejectionOf(client.run(new AgentRequest('again')))).toBeInstanceOf(InstructionsError);
  });

  test('dry run performs no instruction I/O', async () => {
    const silence = spyOn(console, 'log').mockImplementation(() => undefined);
    for (const source of [Instructions.fromUrl('http://localhost:1/never.md'), Instructions.fromFile(join(directory, 'missing.md'))]) {
      const result = await Agent({ dryRun: true }).create(new AgentProfile(copilot, { instructions: source })).run(new AgentRequest('Review'));

      expect(result.output).toContain('omitted in dry-run');
    }
    silence.mockRestore();
  });
});
