// Disposable experiment test (#124). Run: ./run.sh (or `bun test`).
import { cpSync, appendFileSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "bun:test";
import { cruise } from "dependency-cruiser";
import config from "./.dependency-cruiser.cjs";

type Violation = { from: string; to: string; rule: { name: string }; via?: unknown };
type Rule = { name: string; to: Record<string, unknown> };

const CASES = join(import.meta.dir, "out", "cases");

async function violations(root: string, forbidden: Rule[] = config.forbidden): Promise<Violation[]> {
  const result = await cruise(["src", "workflows"], {
    ...config.options,
    baseDir: root,
    validate: true,
    ruleSet: { forbidden },
  });
  return (result.output as { summary: { violations: Violation[] } }).summary.violations;
}

// Copy the clean skeleton and append seeded `import` lines: [file, specifier][].
function seed(name: string, edits: [string, string][]): string {
  const root = join(CASES, name);
  rmSync(root, { recursive: true, force: true });
  mkdirSync(CASES, { recursive: true });
  cpSync(join(import.meta.dir, "skeleton"), root, { recursive: true });
  for (const [file, spec] of edits) appendFileSync(join(root, file), `import "${spec}";\n`);
  return root;
}

const show = (vs: Violation[]) => vs.map((v) => `${v.rule.name}: ${v.from} -> ${v.to}`);

test("clean skeleton: every import-linter contract rule passes", async () => {
  const vs = await violations(join(import.meta.dir, "skeleton"));
  console.log("clean:", show(vs));
  // Only finding: real loop.workflow.adapters.otel lazily imports opentelemetry.trace.
  expect(show(vs)).toEqual([
    "otel-only-in-observe-otel: src/loop/workflow/adapters/otel.ts -> @opentelemetry/api",
  ]);
});

const cases: { name: string; rule: string; from: string; edits: [string, string][] }[] = [
  { name: "layers-direct", rule: "layers/observe-not-above", from: "src/loop/observe/values.ts",
    edits: [["src/loop/observe/values.ts", "../builder.ts"]] },
  // observe -> log (no layer; legal) -> builder (legal): illegal only as an indirect chain.
  { name: "layers-indirect", rule: "layers/observe-not-above", from: "src/loop/observe/roles.ts",
    edits: [["src/loop/observe/roles.ts", "../log.ts"], ["src/loop/log.ts", "./builder.ts"]] },
  { name: "layers-siblings", rule: "layers/clis-independent-of-siblings", from: "src/loop/clis/base.ts",
    edits: [["src/loop/clis/base.ts", "../git/index.ts"]] },
  { name: "workflows-private-loop", rule: "workflows-public-loop-only", from: "workflows/plan_implement.ts",
    edits: [["workflows/plan_implement.ts", "../src/loop/run/index.ts"]] },
  { name: "dev-attempt-direct", rule: "dev-attempt-no-agent-workflow", from: "workflows/dev/attempt.ts",
    edits: [["workflows/dev/attempt.ts", "./agent.ts"]] },
  { name: "dev-attempt-indirect", rule: "dev-attempt-no-agent-workflow", from: "workflows/dev/attempt.ts",
    edits: [["workflows/dev/checkout.ts", "./repositories.ts"]] },
  { name: "independence-direct", rule: "workflows-independent", from: "workflows/workflow_reporting.ts",
    edits: [["workflows/workflow_reporting.ts", "./dev/index.ts"]] },
  // plan_implement -> workflows/index (outside the four) -> pr_babysit.
  { name: "independence-indirect", rule: "workflows-independent", from: "workflows/plan_implement.ts",
    edits: [["workflows/plan_implement.ts", "./index.ts"], ["workflows/index.ts", "./pr_babysit/settings.ts"]] },
  { name: "pr-babysit-direct", rule: "pr-babysit-workflow-no-scheduler", from: "workflows/pr_babysit/workflow/maintainer.ts",
    edits: [["workflows/pr_babysit/workflow/maintainer.ts", "../infrastructure/scheduler.ts"]] },
  { name: "pr-babysit-indirect", rule: "pr-babysit-workflow-no-scheduler", from: "workflows/pr_babysit/workflow/watcher.ts",
    edits: [["workflows/pr_babysit/workflow/watcher.ts", "../settings.ts"], ["workflows/pr_babysit/settings.ts", "./infrastructure/scheduler.ts"]] },
  { name: "otel-outside-observe", rule: "otel-only-in-observe-otel", from: "src/loop/clis/runner.ts",
    edits: [["src/loop/clis/runner.ts", "@opentelemetry/api"]] },
];

describe("seeded violation is caught", () => {
  for (const c of cases) {
    test(c.name, async () => {
      const vs = await violations(seed(c.name, c.edits));
      console.log(`${c.name}:`, show(vs).filter((v) => !v.includes("adapters/otel.ts")));
      expect(vs.some((v) => v.rule.name === c.rule && v.from === c.from)).toBe(true);
    });
  }
});

test("contrast: without reachable, the indirect layers chain is missed", async () => {
  const direct = config.forbidden.map((r: Rule) => {
    const { reachable: _, ...to } = r.to;
    return { ...r, to };
  });
  const vs = await violations(join(CASES, "layers-indirect"), direct);
  console.log("layers-indirect, direct-only rules:", show(vs).filter((v) => !v.includes("adapters/otel.ts")));
  expect(vs.some((v) => v.rule.name === "layers/observe-not-above")).toBe(false);
});
