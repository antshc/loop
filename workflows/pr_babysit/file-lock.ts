import { dlopen, FFIType, read } from 'bun:ffi';
import { closeSync, mkdirSync, openSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

export interface FileLock extends Disposable {
  readonly path: string;
  /** Idempotent. The lock file is kept and never contains a process id. */
  release: () => void;
}

/** Platform seam. `tryAcquire` returns `undefined` on contention and throws {@link FileLockError} on other failures. */
export interface FileLockBackend {
  tryAcquire: (path: string) => (() => void) | undefined;
}

/** Another holder (this process or another) owns the lock; the caller reports Blocked "Another process owns <path>". */
export class FileLockContendedError extends Error {
  readonly path: string;

  constructor(path: string) {
    super(`Another process owns ${path}`);
    this.name = 'FileLockContendedError';
    this.path = path;
  }
}

/** The OS refused the lock for a reason other than contention. */
export class FileLockError extends Error {
  readonly code: number;
  readonly path: string;

  constructor(path: string, code: number) {
    super(`Cannot lock ${path}: OS error ${String(code)}`);
    this.name = 'FileLockError';
    this.code = code;
    this.path = path;
  }
}

/** The platform cannot provide an OS lock; callers must stop instead of running unlocked. */
export class FileLockUnavailableError extends Error {
  constructor(reason: string) {
    super(`File locking is unavailable: ${reason}`);
    this.name = 'FileLockUnavailableError';
  }
}

const EWOULDBLOCK = { darwin: 35, linux: 11 } as const;
const LOCK_EX = 2;
const LOCK_NB = 4;

function posixBackend(platform: 'darwin' | 'linux'): FileLockBackend {
  const flock = { args: [FFIType.i32, FFIType.i32], returns: FFIType.i32 } as const;
  const errnoLocation = { args: [], returns: FFIType.ptr } as const;
  let lockDescriptor: (descriptor: number) => number;
  let errno: () => number;
  if (platform === 'darwin') {
    const libc = dlopen('libc.dylib', { __error: errnoLocation, flock });
    lockDescriptor = (descriptor) => libc.symbols.flock(descriptor, LOCK_EX | LOCK_NB);
    errno = () => {
      const location = libc.symbols.__error();
      return location === null ? -1 : read.i32(location);
    };
  } else {
    const libc = dlopen('libc.so.6', { __errno_location: errnoLocation, flock });
    lockDescriptor = (descriptor) => libc.symbols.flock(descriptor, LOCK_EX | LOCK_NB);
    errno = () => {
      const location = libc.symbols.__errno_location();
      return location === null ? -1 : read.i32(location);
    };
  }

  return {
    tryAcquire(path) {
      // Bun opens with O_CLOEXEC, so spawned children never inherit the descriptor.
      const descriptor = openSync(path, 'a+');
      if (lockDescriptor(descriptor) !== 0) {
        const code = errno();
        closeSync(descriptor);
        if (code === EWOULDBLOCK[platform]) return undefined;
        throw new FileLockError(path, code);
      }
      return () => {
        closeSync(descriptor);
      };
    },
  };
}

const GENERIC_READ_WRITE = 0xc0000000;
const SHARE_ALL = 0x7;
const OPEN_ALWAYS = 4;
const FILE_ATTRIBUTE_NORMAL = 0x80;
const LOCKFILE_FAIL_IMMEDIATELY = 1;
const LOCKFILE_EXCLUSIVE_LOCK = 2;
const ERROR_LOCK_VIOLATION = 33;
const INVALID_HANDLE_VALUE = 0xffffffffffffffffn;
const OVERLAPPED_SIZE = 32;

/** Load the platform backend, or throw {@link FileLockUnavailableError} naming the limitation. */
export function loadFileLockBackend(platform: NodeJS.Platform = process.platform): FileLockBackend {
  try {
    if (platform === 'linux' || platform === 'darwin') return posixBackend(platform);
    if (platform === 'win32') return windowsBackend();
  } catch (error) {
    throw new FileLockUnavailableError(error instanceof Error ? error.message : String(error));
  }
  throw new FileLockUnavailableError(`no lock implementation for platform ${platform}`);
}

function windowsBackend(): FileLockBackend {
  const kernel32 = dlopen('kernel32.dll', {
    CloseHandle: { args: [FFIType.u64], returns: FFIType.i32 },
    CreateFileW: {
      args: [FFIType.ptr, FFIType.u32, FFIType.u32, FFIType.ptr, FFIType.u32, FFIType.u32, FFIType.ptr],
      returns: FFIType.u64,
    },
    GetLastError: { args: [], returns: FFIType.u32 },
    LockFileEx: {
      args: [FFIType.u64, FFIType.u32, FFIType.u32, FFIType.u32, FFIType.u32, FFIType.ptr],
      returns: FFIType.i32,
    },
  });

  return {
    tryAcquire(path) {
      const wide = Buffer.from(`${path}\0`, 'utf16le');
      // A null security descriptor makes the handle non-inheritable.
      const handle = kernel32.symbols.CreateFileW(
        wide,
        GENERIC_READ_WRITE,
        SHARE_ALL,
        null,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        null,
      );
      if (handle === INVALID_HANDLE_VALUE) throw new FileLockError(path, kernel32.symbols.GetLastError());
      const overlapped = Buffer.alloc(OVERLAPPED_SIZE);
      const locked = kernel32.symbols.LockFileEx(
        handle,
        LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
        0,
        1,
        0,
        overlapped,
      );
      if (locked === 0) {
        const code = kernel32.symbols.GetLastError();
        kernel32.symbols.CloseHandle(handle);
        if (code === ERROR_LOCK_VIOLATION) return undefined;
        throw new FileLockError(path, code);
      }
      return () => {
        kernel32.symbols.CloseHandle(handle);
      };
    },
  };
}

let defaultBackend: FileLockBackend | undefined;

/**
 * Take a non-blocking exclusive OS lock on `path`, creating the file and its parents if needed.
 * One descriptor per lock; a second acquisition (same or another process) throws {@link FileLockContendedError}.
 * The OS releases the lock when the holder dies, including by SIGKILL.
 */
export function acquireFileLock(path: string, backend?: FileLockBackend): FileLock {
  const resolved = resolve(path);
  mkdirSync(dirname(resolved), { recursive: true });
  let selected = backend;
  if (selected === undefined) {
    assertFileLockAvailable();
    selected = defaultBackend;
  }
  const close = selected?.tryAcquire(resolved);
  if (close === undefined) throw new FileLockContendedError(path);
  let released = false;
  const release = (): void => {
    if (released) return;
    released = true;
    close();
  };
  return { path, release, [Symbol.dispose]: release };
}

/** Fail fast at startup when the platform cannot lock. */
export function assertFileLockAvailable(): void {
  defaultBackend ??= loadFileLockBackend();
}

/** Run `action` while holding the lock; the lock is released when it settles. */
export async function withFileLock<T>(path: string, action: () => Promise<T> | T, backend?: FileLockBackend): Promise<T> {
  const lock = acquireFileLock(path, backend);
  try {
    return await action();
  } finally {
    lock.release();
  }
}
