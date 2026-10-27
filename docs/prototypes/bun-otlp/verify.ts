// Reads out/received.jsonl and asserts what the receiver saw. Usage: bun verify.ts <label>
import { readFileSync } from "node:fs";

const label = process.argv[2] ?? "run";
const rows = readFileSync("out/received.jsonl", "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
const traces = rows.filter((r) => r.path === "/v1/traces");
const metrics = rows.filter((r) => r.path === "/v1/metrics");
const spans = traces.flatMap((r) => r.body.resourceSpans.flatMap((rs: any) => rs.scopeSpans.flatMap((ss: any) => ss.spans)));
const byName = Object.fromEntries(spans.map((s: any) => [s.name, s]));
const agent = byName["invoke_agent dev"], tool = byName["execute_tool bash"], inner = byName["execute_tool nested"];
const attr = (s: any, k: string) => s?.attributes?.find((a: any) => a.key === k)?.value;
const checks: Record<string, boolean> = {
  "trace request received": traces.length > 0,
  "metrics request received": metrics.length > 0,
  "3 spans": spans.length === 3,
  "agent is root": !!agent && !agent.parentSpanId,
  "tool parent = agent": !!tool && tool.parentSpanId === agent?.spanId,
  "nested parent = tool": !!inner && inner.parentSpanId === tool?.spanId,
  "same traceId": new Set(spans.map((s: any) => s.traceId)).size === 1,
  "loop attrs present": attr(agent, "gen_ai.operation.name")?.stringValue === "invoke_agent" && attr(agent, "loop.agent.exit_code")?.intValue != null,
};
let ok = true;
for (const [k, v] of Object.entries(checks)) { console.log(`${v ? "PASS" : "FAIL"} [${label}] ${k}`); ok &&= v; }
console.log(`[${label}] requests: ${traces.length} trace, ${metrics.length} metric; encoding=${traces[0]?.contentType}`);
process.exit(ok ? 0 : 1);
