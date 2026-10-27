import { describe, expect, test } from 'bun:test';

import { CodexJson, CopilotJson, CopilotText, type Transport } from '../../src/loop/index.ts';
import { golden } from './support/fixtures.ts';

interface Fixture {
  decode: { case: string; stdout: string; transcript: Transcript; transport: string }[];
  encode: { encoded: string; handle: null | string; text: string; transport: string }[];
}

interface Transcript {
  exitCode: null | number;
  handleValue: null | string;
  text: string;
}

const TRANSPORTS: Readonly<Record<string, Transport>> = {
  'codex-json': new CodexJson(),
  'copilot-json': new CopilotJson(),
  'copilot-text': new CopilotText(),
};

function transport(key: string): Transport {
  const found = TRANSPORTS[key];
  if (found === undefined) {
    throw new Error(`unknown transport ${key}`);
  }
  return found;
}

const fixture = await golden<Fixture>('transports.json');

describe('decoding stdout events equals the golden fixture', () => {
  for (const { case: name, stdout, transcript, transport: key } of fixture.decode) {
    test(`${key}: ${name}`, () => {
      const decoded = transport(key).decode(stdout);

      expect({ exitCode: decoded.exitCode, handleValue: decoded.handleValue, text: decoded.text }).toEqual(transcript);
    });
  }
});

describe('dry-run encoding equals the golden fixture and round-trips through the same parser', () => {
  for (const { encoded, handle, text, transport: key } of fixture.encode) {
    test(`${key}: ${JSON.stringify(text)} / ${String(handle)}`, () => {
      const subject = transport(key);

      expect(subject.encode(text, handle)).toBe(encoded);
      const decoded = subject.decode(encoded);
      expect(decoded.text).toBe(text);
      if (key !== 'copilot-text') {
        expect(decoded.handleValue).toBe(handle);
      }
    });
  }
});

describe('transport contract', () => {
  test('non-JSON lines are skipped', () => {
    for (const subject of [new CopilotJson(), new CodexJson()]) {
      const stdout = `not json\n${subject.encode('hello', 'h1')}\nalso not json`;

      expect(subject.decode(stdout).text).toBe('hello');
    }
  });

  test('transports carry the CLI arguments that select their output shape', () => {
    expect(new CopilotText().args).toEqual(['--output-format', 'text']);
    expect(new CopilotJson().args).toEqual(['--output-format', 'json']);
    expect(new CodexJson().args).toEqual(['--json']);
  });
});
