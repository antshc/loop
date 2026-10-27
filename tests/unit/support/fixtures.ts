import { join } from 'node:path';

export const FIXTURES = join(import.meta.dir, '..', '..', 'fixtures');

/** A golden fixture captured from the Python implementation. */
export async function golden<T>(...path: string[]): Promise<T> {
  return (await Bun.file(join(FIXTURES, ...path)).json()) as T;
}
