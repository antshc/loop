/** Load reusable instruction text without coupling it to an agent CLI. */

import { isAbsolute } from 'node:path';

import { ValueError } from '../value-error.ts';

export interface InstructionsInit {
  path?: null | string;
  url?: null | string;
}

const MAX_BYTES = 1024 * 1024;
const FETCH_TIMEOUT_MS = 10_000;

/** An inert file or public HTTP source resolved when an agent client first runs. */
export class Instructions {
  readonly path: null | string;
  readonly url: null | string;

  constructor(init: InstructionsInit = {}) {
    const path = init.path ?? null;
    const url = init.url ?? null;
    if ((path === null) === (url === null)) {
      throw new ValueError('instructions require exactly one file or URL');
    }
    if (path !== null && !isAbsolute(path)) {
      throw new ValueError('instruction file path must be absolute');
    }
    if (url !== null && !isPublicHttpUrl(url)) {
      throw new ValueError('instructions require a public HTTP(S) URL without credentials');
    }
    this.path = path;
    this.url = url;
    Object.freeze(this);
  }

  /** Declare an absolute UTF-8 instruction file without reading it. */
  static fromFile(path: string): Instructions {
    return new Instructions({ path });
  }

  /** Declare a public raw-text URL without fetching it. */
  static fromUrl(url: string): Instructions {
    return new Instructions({ url });
  }
}

/** An instruction source could not provide usable UTF-8 text. */
export class InstructionsError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'InstructionsError';
  }
}

/** Snapshot instructions once, with no source I/O in dry-run mode. */
export async function resolveInstructions(
  source: Instructions | null | string,
  options: { dryRun?: boolean } = {},
): Promise<null | string> {
  if (source === null) {
    return null;
  }
  if (typeof source === 'string') {
    return text(source);
  }
  if (options.dryRun === true) {
    return `[instructions from ${source.path ?? source.url ?? ''}; omitted in dry-run]`;
  }
  let content: Uint8Array;
  try {
    content = source.path === null ? await fetchBytes(source.url ?? '') : await readFileBytes(source.path);
  } catch (error) {
    if (error instanceof InstructionsError) {
      throw error;
    }
    throw new InstructionsError('could not load UTF-8 instructions', { cause: error });
  }
  if (content.length > MAX_BYTES) {
    throw new InstructionsError('instructions exceed the 1 MiB limit');
  }
  try {
    return text(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(content));
  } catch (error) {
    if (error instanceof InstructionsError) {
      throw error;
    }
    throw new InstructionsError('could not load UTF-8 instructions', { cause: error });
  }
}

async function fetchBytes(url: string): Promise<Uint8Array> {
  const response = await fetch(url, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  if (!response.ok) {
    throw new Error(`HTTP Error ${String(response.status)}: ${response.statusText}`);
  }
  if ((response.headers.get('Content-Type') ?? '').toLowerCase().includes('text/html')) {
    await response.body?.cancel();
    throw new InstructionsError('instruction URL must return raw text, not HTML');
  }
  return readUpTo(response.body, MAX_BYTES + 1);
}

function isPublicHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname !== '' && url.username === '' && url.password === '';
  } catch {
    return false;
  }
}

async function readFileBytes(path: string): Promise<Uint8Array> {
  return await Bun.file(path).slice(0, MAX_BYTES + 1).bytes();
}

/** Reads at most `limit` bytes, so an oversized source is detected without loading all of it. */
async function readUpTo(body: null | ReadableStream<Uint8Array>, limit: number): Promise<Uint8Array> {
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (body !== null) {
    const reader = body.getReader();
    while (size < limit) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      chunks.push(value);
      size += value.length;
    }
    await reader.cancel();
  }
  return Buffer.concat(chunks).subarray(0, limit);
}

function text(value: string): string {
  if (value.trim() === '') {
    throw new InstructionsError('instructions must not be empty');
  }
  return value;
}
