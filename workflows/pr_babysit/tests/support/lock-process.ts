// Test helper process: `lock-process.ts <hold|try> <lockPath> [spawn-child]`.
// Prints one JSON line. `hold` then stays alive until killed.
import { acquireFileLock, FileLockContendedError } from '../../file-lock.ts';

const [mode, path, extra] = process.argv.slice(2);
if (path === undefined) throw new Error('lock path required');

try {
  const lock = acquireFileLock(path);
  let child: number | undefined;
  if (mode === 'hold') {
    if (extra === 'spawn-child') {
      child = Bun.spawn([process.execPath, '-e', 'setInterval(() => undefined, 1000)'], {
        stdio: ['ignore', 'ignore', 'ignore'],
      }).pid;
    }
    console.log(JSON.stringify({ child, pid: process.pid, status: 'acquired' }));
    setInterval(() => undefined, 1000);
  } else {
    lock.release();
    console.log(JSON.stringify({ pid: process.pid, status: 'acquired' }));
  }
} catch (error) {
  if (!(error instanceof FileLockContendedError)) throw error;
  console.log(JSON.stringify({ message: error.message, pid: process.pid, status: 'contended' }));
}
