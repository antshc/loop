// Disposable spike for #125: bun:ffi flock on an fs.openSync(path, "a+") fd.
// Usage: spike <hold|try|same|cloexec> <lockpath> [spawn]
import { dlopen, FFIType, read } from "bun:ffi";
import { openSync, readFileSync } from "node:fs";

const LIBC = process.platform === "darwin" ? "libc.dylib" : "libc.so.6";
const ERRNO_SYM = process.platform === "darwin" ? "__error" : "__errno_location";
const LOCK_EX = 2, LOCK_NB = 4;
const libc = dlopen(LIBC, {
  flock: { args: [FFIType.i32, FFIType.i32], returns: FFIType.i32 },
  [ERRNO_SYM]: { args: [], returns: FFIType.ptr },
});
const errno = () => read.i32(libc.symbols[ERRNO_SYM]()!);
const tryLock = (fd: number) => {
  const rc = libc.symbols.flock(fd, LOCK_EX | LOCK_NB);
  return { rc, errno: rc === 0 ? 0 : errno() };
};
const cloexec = (fd: number) => {
  const flags = /flags:\s*(\d+)/.exec(readFileSync(`/proc/self/fdinfo/${fd}`, "utf8"))![1];
  return (parseInt(flags, 8) & 0o2000000) !== 0;
};

const [mode, path, extra] = process.argv.slice(2);
const log = (o: object) => console.log(JSON.stringify({ mode, pid: process.pid, ...o }));

if (mode === "hold") {
  const fd = openSync(path, "a+");
  const r = tryLock(fd);
  let child: number | undefined;
  if (r.rc === 0 && extra === "spawn") child = Bun.spawn(["sleep", "300"], { stdio: ["ignore", "ignore", "ignore"] }).pid;
  log({ fd, cloexec: cloexec(fd), ...r, child });
  setInterval(() => {}, 1 << 30);
} else if (mode === "try") {
  const fd = openSync(path, "a+");
  const first = tryLock(fd);
  // errno reliability: repeat contended attempts, tally errno values
  const tally: Record<number, number> = {};
  if (first.rc !== 0) for (let i = 0; i < 10000; i++) { const e = tryLock(fd).errno; tally[e] = (tally[e] ?? 0) + 1; }
  const ebadf = libc.symbols.flock(-1, LOCK_EX | LOCK_NB) === -1 ? errno() : 0;
  log({ ...first, repeatTally: tally, ebadfProbe: ebadf });
} else if (mode === "same") {
  const fd1 = openSync(path, "a+"), fd2 = openSync(path, "a+");
  log({ first: tryLock(fd1), second: tryLock(fd2), relockSameFd: tryLock(fd1) });
}
