// Disposable experiment config (#124): pyproject.toml [tool.importlinter] contracts as dependency-cruiser rules.
// Paths are relative to the cruised root, which holds src/loop/ and workflows/.

const mod = (names) => `^src/loop/(${names.join("|")})(/|\\.ts$)`;

// Contract 1 — layers, containers = ["loop"]; top first, "a | b" = independent siblings.
const LAYERS = [
  ["factory", "workflow"],
  ["builder", "dryrun"],
  ["agents"],
  ["clis", "docker", "git"],
  ["run"],
  ["hooks"],
  ["sessions"],
  ["observe"],
];

const layerRules = LAYERS.flatMap((layer, i) => [
  // import-linter layers forbid indirect upward chains by default -> reachable: true.
  ...(i === 0
    ? []
    : [
        {
          name: `layers/${layer.join("|")}-not-above`,
          comment: "Contract 1: a layer must not import (even indirectly) any higher layer",
          severity: "error",
          from: { path: mod(layer) },
          to: { path: mod(LAYERS.slice(0, i).flat()), reachable: true },
        },
      ]),
  // "|" siblings are mutually independent, also indirectly.
  ...layer.map((name) => ({
    name: `layers/${name}-independent-of-siblings`,
    comment: "Contract 1: '|' siblings must not import each other",
    severity: "error",
    from: { path: mod([name]) },
    to: { path: mod(layer.filter((other) => other !== name)), reachable: true },
  })).filter(() => layer.length > 1),
]);

const PRIVATE_LOOP = [
  "agents", "builder", "clis", "docker", "dryrun", "factory",
  "git", "hooks", "observe", "run", "sessions", "workflow",
];

const WORKFLOWS = "(dev|plan_implement|workflow_reporting|pr_babysit)";

module.exports = {
  forbidden: [
    ...layerRules,
    {
      name: "workflows-public-loop-only",
      comment: "Contract 2: workflows import only the public loop package (allow_indirect_imports = true -> direct only)",
      severity: "error",
      from: { path: "^workflows/" },
      to: { path: mod(PRIVATE_LOOP) },
    },
    {
      name: "dev-attempt-no-agent-workflow",
      comment: "Contract 3: workflows.dev.attempt must not import dev.agent / dev.repositories / dev.run (indirect too)",
      severity: "error",
      from: { path: "^workflows/dev/attempt(/|\\.ts$)" },
      to: { path: "^workflows/dev/(agent|repositories|run)(/|\\.ts$)", reachable: true },
    },
    {
      name: "workflows-independent",
      comment: "Contract 4: no workflow imports another workflow (indirect too)",
      severity: "error",
      from: { path: `^workflows/${WORKFLOWS}(/|\\.ts$)` },
      to: {
        path: `^workflows/${WORKFLOWS}(/|\\.ts$)`,
        pathNot: "^workflows/$1(/|\\.ts$)",
        reachable: true,
      },
    },
    {
      name: "pr-babysit-workflow-no-scheduler",
      comment: "Contract 5: pr_babysit.workflow must not import infrastructure.scheduler (indirect too)",
      severity: "error",
      from: { path: "^workflows/pr_babysit/workflow(/|\\.ts$)" },
      to: { path: "^workflows/pr_babysit/infrastructure/scheduler(/|\\.ts$)", reachable: true },
    },
    {
      name: "otel-only-in-observe-otel",
      comment: "Extra: only observe/otel imports @opentelemetry/*",
      severity: "error",
      from: { pathNot: "^src/loop/observe/otel\\.ts$" },
      to: { path: "^(node_modules/)?@opentelemetry/" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
  },
};
