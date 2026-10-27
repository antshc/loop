import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

import { copilotHome, copilotSessionId } from '../../src/loop/clis/copilot-log.ts';
import {
  CopilotLogReader,
  type LogReading,
  NativeHandle,
  Resume,
  SessionName,
  Start,
  type Turn,
} from '../../src/loop/index.ts';
import { rejectionOf } from './support/doubles.ts';
import { FIXTURES, golden } from './support/fixtures.ts';

interface Expected {
  cases: { case: string; reading: null | ReadingJson; session: string; turn: 'resume' | 'start' }[];
  sessionIds: Readonly<Record<string, string>>;
}

interface ReadingJson {
  model: null | string;
  nativeId: string;
  reasoningEffort: null | string;
  tools: ToolJson[];
  usage: Record<string, unknown>;
}

interface ToolJson {
  arguments: Record<string, unknown>;
  callId: string;
  endedAt: string;
  name: string;
  ok: boolean;
  result: string;
  startedAt: string;
}

const expected = await golden<Expected>('copilot-log', 'expected.json');

let home = '';

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'loop-copilot-log-'));
});

afterEach(() => {
  rmSync(home, { force: true, recursive: true });
});

function install(name: SessionName, fixture: string): void {
  const target = join(home, 'session-state', copilotSessionId(name), 'events.jsonl');
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(join(FIXTURES, 'copilot-log', `${fixture}.jsonl`), target);
}

function toJson(reading: LogReading | null): null | ReadingJson {
  if (reading === null) {
    return null;
  }
  const { usage } = reading;
  return {
    model: reading.model,
    nativeId: reading.nativeId,
    reasoningEffort: reading.reasoningEffort,
    tools: reading.tools.map((tool) => ({
      arguments: { ...tool.arguments },
      callId: tool.callId,
      endedAt: tool.endedAt.toISOString(),
      name: tool.name,
      ok: tool.ok,
      result: tool.result,
      startedAt: tool.startedAt.toISOString(),
    })),
    usage: {
      aiu: { ...usage.aiu },
      apiCalls: usage.apiCalls,
      cacheReadTokens: usage.cacheReadTokens,
      cacheWriteTokens: usage.cacheWriteTokens,
      inputTokens: usage.inputTokens,
      outputTokens: usage.outputTokens,
      premiumRequests: usage.premiumRequests,
      reasoningTokens: usage.reasoningTokens,
      totalAiu: usage.totalAiu,
      totalInputTokens: usage.totalInputTokens,
    },
  };
}

function turnOf(kind: 'resume' | 'start', session: string): Turn {
  const name = new SessionName(session);
  return kind === 'start' ? new Start(name) : new Resume(name, new NativeHandle('copilot', session));
}

describe('the Copilot session log is parsed exactly as the Python implementation does', () => {
  for (const { case: name, reading, session, turn } of expected.cases) {
    test(name, async () => {
      install(new SessionName(session), name);

      expect(toJson(await new CopilotLogReader(home).read(turnOf(turn, session)))).toEqual(reading);
    });
  }

  test('pre-1.0.95 logs carry only cumulative totals, which are not sliced into a reading', () => {
    const legacy = expected.cases.find((item) => item.case === 'pre-1.0.95-cumulative-totals');

    expect(legacy?.reading).toBeNull();
  });
});

describe('the session directory derives from the session name', () => {
  for (const [name, id] of Object.entries(expected.sessionIds)) {
    test(`${name.slice(0, 24)} maps to ${id}`, () => {
      expect(copilotSessionId(new SessionName(name))).toBe(id);
    });
  }

  test('the log is read from <home>/session-state/<id>/events.jsonl', async () => {
    const name = new SessionName('ticket-7');
    install(name, 'first-turn');

    expect((await new CopilotLogReader(home).read(new Start(name)))?.nativeId).toBe(copilotSessionId(name));
  });

  test('COPILOT_HOME overrides the default ~/.copilot', () => {
    const previous = process.env.COPILOT_HOME;
    try {
      process.env.COPILOT_HOME = '/custom/copilot';
      expect(copilotHome()).toBe('/custom/copilot');
      process.env.COPILOT_HOME = '';
      expect(copilotHome().endsWith('.copilot')).toBe(true);
    } finally {
      if (previous === undefined) {
        delete process.env.COPILOT_HOME;
      } else {
        process.env.COPILOT_HOME = previous;
      }
    }
  });
});

describe('the reader', () => {
  const name = new SessionName('ticket-7');

  test('pins only a Start to its derivable session id', () => {
    const reader = new CopilotLogReader();

    expect(reader.startArgs(new Start(name))).toEqual(['--session-id', copilotSessionId(name)]);
    expect(reader.startArgs(new Resume(name, new NativeHandle('copilot', name.value)))).toEqual([]);
  });

  test('reports nothing for a missing log', async () => {
    expect(await new CopilotLogReader(home).read(new Start(name))).toBeNull();
  });

  test('raises on a malformed log so the observing runner can discard it', async () => {
    const path = join(home, 'session-state', copilotSessionId(name), 'events.jsonl');
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, '{not json\n');

    expect(await rejectionOf(new CopilotLogReader(home).read(new Start(name)))).toBeInstanceOf(SyntaxError);
  });
});
