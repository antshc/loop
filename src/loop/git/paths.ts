import { realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';

/** True when `child` is `parent` or inside it. */
export function isInside(parent: string, child: string): boolean {
  const path = relative(parent, child);
  return path === '' || (path !== '..' && !path.startsWith(`..${sep}`) && !isAbsolute(path));
}

/** Whether something exists at `path`; a broken link counts as absent. */
export async function pathExists(path: string): Promise<boolean> {
  try {
    await realpath(path);
    return true;
  } catch (error) {
    if (isMissing(error)) {
      return false;
    }
    throw error;
  }
}

/** The path at the filesystem level: absolute, symlinks of the part that exists resolved, the rest kept. */
export async function resolvePath(path: string): Promise<string> {
  const absolute = resolve(path);
  try {
    return await realpath(absolute);
  } catch (error) {
    const parent = dirname(absolute);
    if (!isMissing(error) || parent === absolute) {
      throw error;
    }
    return join(await resolvePath(parent), basename(absolute));
  }
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && (error.code === 'ENOENT' || error.code === 'ENOTDIR');
}
