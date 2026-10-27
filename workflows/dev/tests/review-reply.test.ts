import { describe, expect, test } from 'bun:test';

import { ContentDecodeError, DevResultError, LastJsonObject, ReviewResponse } from '../review-reply.ts';

interface DecodedGolden {
  readonly axes?: readonly { readonly name: string; readonly note: string; readonly outcome: string }[];
  readonly blocking?: number;
  readonly completed?: boolean;
  readonly error?: string;
  readonly findings?: readonly { readonly axis: string; readonly kind: string; readonly summary: string }[];
  readonly head?: string;
  readonly posted?: readonly string[];
}

interface ResponseGolden {
  readonly decoded: DecodedGolden;
  readonly raw: string;
}

const golden = (await Bun.file(new URL('fixtures/review-parity.json', import.meta.url)).json()) as {
  readonly responses: Record<string, ResponseGolden>;
};

const decode = (text: string): DecodedGolden => {
  try {
    const result = new ReviewResponse().decode(text);
    return {
      axes: result.axes.map((axis) => ({ name: axis.name, note: axis.note ?? '', outcome: axis.outcome })),
      blocking: result.blockingFindings.length,
      completed: result.completed,
      findings: result.findings.map((finding) => ({ axis: finding.axis ?? '', kind: finding.kind, summary: finding.summary })),
      head: result.headSha,
      posted: result.posted,
    };
  } catch (error) {
    if (error instanceof DevResultError) {
      return { error: error.message };
    }
    throw error;
  }
};

describe('the reviewer response', () => {
  for (const [name, { decoded, raw }] of Object.entries(golden.responses)) {
    test(`decodes ${name} as the Python Workflow did`, () => {
      expect(decode(raw)).toEqual(decoded);
    });
  }

  test('decodes axes, findings, posted references and the reviewed head', () => {
    const text = `thinking...\n${JSON.stringify({
      result: {
        axes: [
          { name: 'bugs', outcome: 'completed' },
          { name: 'smells', note: 'n', outcome: 'FAILED' },
        ],
        findings: [{ axis: 'bugs', kind: 'Bug', summary: 'npe' }],
        head: 'abc',
        posted: ['c1', 7],
      },
      status: 'completed',
    })}\nbye`;

    const result = new ReviewResponse().decode(text);

    expect(result.completed).toBe(false);
    expect([result.headSha, result.posted]).toEqual(['abc', ['c1', '7']]);
    expect(result.blockingFindings.map((finding) => finding.summary)).toEqual(['npe']);
  });

  test('rejects a failed review with its reason', () => {
    const text = JSON.stringify({ result: { reason: 'no skill' }, status: 'failed' });

    expect(() => new ReviewResponse().decode(text)).toThrow(new DevResultError('review failed: no skill'));
  });
});

describe('the last JSON object in a text', () => {
  const anything = new LastJsonObject();

  test('is the last top-level object that satisfies the condition', () => {
    const wanted = new LastJsonObject((value) => 'status' in value);

    expect(wanted.decode('{"status": 1} then {"other": 2} {"status": 3, "inner": {"status": 4}} {oops')).toEqual({
      inner: { status: 4 },
      status: 3,
    });
  });

  test('skips braces inside strings and text that is not JSON', () => {
    expect(anything.decode('{x} {"a": "} {"} {not json')).toEqual({ a: '} {' });
  });

  test('raises ContentDecodeError with the text when no object matches', () => {
    const text = 'no objects, only [1, 2] and {broken';

    expect(() => anything.decode(text)).toThrow(ContentDecodeError);
    try {
      anything.decode(text);
    } catch (error) {
      expect((error as ContentDecodeError).output).toBe(text);
    }
  });
});
