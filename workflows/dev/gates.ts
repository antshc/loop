/** Gates for planner, verifier, cleanup, and reviewer work. */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import type { GitService } from './checkout.ts';

/** The machinery that proves the app: a verification fix must never change what grades it. */
export const PROTECTED_PATHS: readonly string[] = [
  'proof/',
  'e2e/',
  'playwright.config.ts',
  '.github/skills/',
  '.claude/skills/',
  '.agents/skills/',
];

const SKILL_DIRECTORIES: readonly string[] = ['.github/skills', '.claude/skills', '.agents/skills'];
const DRIVES = /\bdriv\w+/i;
const PROVES = /\b(?:prove|proof)\b/i;
const SEPARATORS = String.fromCharCode(0x1c, 0x1d, 0x1e);
/** The line boundaries Python's `splitlines` recognizes. */
const LINE_BREAKS = new RegExp(`\\r\\n|[\\n\\r\\v\\f${SEPARATORS}\\u0085\\u2028\\u2029]`);

/** `prefix` + the first line of `message` (or `fallback`), without a repeated prefix, capped at 100 characters. */
export function commitSubject(prefix: string, message: string, fallback: string): string {
  const line = (message.trim().split(LINE_BREAKS)[0] ?? '').trim();
  const body = (line.startsWith(prefix) ? line.slice(prefix.length) : line).trim() || fallback.trim();
  return Array.from(`${prefix}${body}`).slice(0, 100).join('').trimEnd();
}

/** Project skills whose description says they drive the app to prove behavior; skills that disable model invocation are ignored. */
export function findVerificationSkills(root: string): string[] {
  const found: string[] = [];
  for (const directory of SKILL_DIRECTORIES) {
    for (const name of skillFolders(join(root, directory))) {
      const skill = join(root, directory, name, 'SKILL.md');
      if (!isFile(skill)) {
        continue;
      }
      const values = frontmatter(skill);
      if ((values.get('disable-model-invocation') ?? '').toLowerCase() === 'true') {
        continue;
      }
      const description = values.get('description') ?? '';
      if (DRIVES.test(description) && PROVES.test(description)) {
        found.push(skill);
      }
    }
  }
  return found;
}

/** The violation when the agent moved HEAD: agents edit, the harness commits. */
export async function madeNoCommit(git: GitService, path: string, headSha: string): Promise<null | string> {
  const head = await git.head(path);
  return head.sha === headSha ? null : `the agent committed (${head.sha}); only the harness commits`;
}

/** The changed files that sit inside the protected paths. */
export function protectedChanges(files: Iterable<string>, protectedPaths: Iterable<string> = PROTECTED_PATHS): string[] {
  const prefixes = [...protectedPaths];
  return [...files].filter((file) =>
    prefixes.some((prefix) => file === prefix.replace(/\/+$/, '') || file.startsWith(prefix)),
  );
}

/** The violation when a read-only agent moved HEAD or changed the working tree compared with `statusBefore`. */
export async function readonlyViolation(
  git: GitService,
  path: string,
  headSha: string,
  statusBefore: string,
): Promise<null | string> {
  const violation = await madeNoCommit(git, path, headSha);
  if (violation !== null) {
    return violation;
  }
  return (await git.status(path)) === statusBefore ? null : 'the read-only agent changed the working tree';
}

function frontmatter(skill: string): Map<string, string> {
  const lines = readFileSync(skill, 'utf8').split(LINE_BREAKS);
  const values = new Map<string, string>();
  if (lines[0]?.trim() !== '---') {
    return values;
  }
  for (const line of lines.slice(1)) {
    if (line.trim() === '---') {
      break;
    }
    const separator = line.indexOf(':');
    if (separator >= 0) {
      values.set(line.slice(0, separator).trim(), line.slice(separator + 1).trim().replaceAll(/^["']+|["']+$/g, ''));
    }
  }
  return values;
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function skillFolders(directory: string): string[] {
  try {
    return readdirSync(directory).sort();
  } catch {
    return [];
  }
}
