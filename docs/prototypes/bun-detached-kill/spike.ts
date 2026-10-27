// usage: spike.ts <direct|grandchild|stubborn> <token> <logfile> <workdir> <cleanupMs>
// Disposable spike: detached spawn + group kill + Cancelled -> import.meta.main exit code.
import { appendFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
const [kind, token, log, workdir, cleanupMs] = process.argv.slice(2);
const mark = (m: string) => appendFileSync(log, m + "\n");
const GRACE_MS = 1000;
const CODES = { SIGINT: 130, SIGTERM: 143 } as const;
type Sig = keyof typeof CODES;

class Cancelled extends Error {
  constructor(readonly signal: Sig) { super(`cancelled by ${signal}`); }
  get exitCode() { return CODES[this.signal]; }
}

// The "process helper": spawn detached, kill its group on signal, reject Cancelled.
async function runAgent(cmd: string[]): Promise<number> {
  const child = Bun.spawn(cmd, { detached: true, stdout: "ignore", stderr: "ignore" });
  mark(`SPAWNED pid=${child.pid}`);
  const killGroup = (s: NodeJS.Signals) => { try { process.kill(-child.pid, s); } catch {} };
  let cancelledBy: Sig | undefined;
  let escalate: Timer | undefined;
  const onSignal = (sig: Sig) => {
    if (cancelledBy) {
      mark(`FORCE ${sig}`);
      killGroup("SIGKILL");
      process.exit(CODES[sig]);
    }
    cancelledBy = sig;
    mark(`HANDLER ${sig}`);
    killGroup("SIGTERM");
    escalate = setTimeout(() => { mark("ESCALATE SIGKILL"); killGroup("SIGKILL"); }, GRACE_MS);
  };
  const onInt = () => onSignal("SIGINT");
  const onTerm = () => onSignal("SIGTERM");
  process.on("SIGINT", onInt);
  process.on("SIGTERM", onTerm);
  try {
    const code = await child.exited;
    clearTimeout(escalate);
    mark(`CHILD_EXITED code=${child.exitCode} sig=${child.signalCode}`);
    if (cancelledBy) throw new Cancelled(cancelledBy);
    return code;
  } finally {
    // Keep a force-exit listener while the caller's cleanup runs after cancellation.
    if (!cancelledBy) {
      process.off("SIGINT", onInt);
      process.off("SIGTERM", onTerm);
    }
  }
}

async function main() {
  const worktree = `${workdir}/worktree`;
  const hook = `${workdir}/hook.json`;
  mkdirSync(worktree, { recursive: true });
  writeFileSync(`${worktree}/file`, "x");
  writeFileSync(hook, "{}");
  const inner = `sleep ${token} & wait`;
  const cmd =
    kind === "direct" ? ["sleep", token]
    : kind === "grandchild" ? ["sh", "-c", inner]
    : ["sh", "-c", `trap '' TERM; ${inner}`]; // stubborn: sh and sleep ignore SIGTERM
  mark("START");
  try {
    await runAgent(cmd);
  } finally {
    mark("FINALLY_START");
    await Bun.sleep(Number(cleanupMs));
    rmSync(worktree, { recursive: true, force: true });
    rmSync(hook, { force: true });
    mark("FINALLY_DONE");
  }
}

if (import.meta.main) {
  main().catch((e) => {
    if (e instanceof Cancelled) {
      mark(`GUARD exit=${e.exitCode}`);
      process.exit(e.exitCode);
    }
    throw e;
  });
}
