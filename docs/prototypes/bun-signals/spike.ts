// usage: spike.ts <bun-sync|node-sync|bun-async> <direct|grandchild> <token> <logfile>
import { appendFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
const [mode, kind, token, log] = process.argv.slice(2);
const mark = (m: string) => appendFileSync(log, m + "\n");
const cmd = kind === "direct" ? ["sleep", token] : ["sh", "-c", `sleep ${token} & wait`];
let child: { kill(s?: number | string): void } | undefined;
for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    mark(`HANDLER ${sig}`);
    try { child?.kill(); } catch {}
    process.exit(sig === "SIGINT" ? 130 : 143);
  });
}
mark("START");
try {
  if (mode === "bun-sync") {
    const r = Bun.spawnSync(cmd);
    mark(`CHILD_RETURNED code=${r.exitCode} sig=${r.signalCode}`);
  } else if (mode === "node-sync") {
    const r = spawnSync(cmd[0], cmd.slice(1));
    mark(`CHILD_RETURNED code=${r.status} sig=${r.signal}`);
  } else {
    const p = Bun.spawn(cmd);
    child = p;
    await p.exited;
    mark(`CHILD_RETURNED code=${p.exitCode} sig=${p.signalCode}`);
  }
} finally {
  mark("FINALLY");
}
