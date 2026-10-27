import { afterAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import { ALL_RULES, CONTRACTS, describeViolations, findViolations, LOOP_LAYERS } from './contracts.ts';

const FIXTURES = join(import.meta.dir, '..', '..', 'node_modules', '.cache', 'architecture-fixtures');
mkdirSync(FIXTURES, { recursive: true });
const fixtureRoot = mkdtempSync(join(FIXTURES, 'case-'));
afterAll(() => {
  rmSync(fixtureRoot, { force: true, recursive: true });
});

type Files = Readonly<Record<string, string>>;

const imports = (...specifiers: string[]): string => `${specifiers.map((s) => `import '${s}';`).join('\n')}\nexport {};\n`;
const leaf = 'export {};\n';

let counter = 0;
interface Seed {
  readonly contract: string;
  readonly files: Files;
  readonly from: string;
  readonly name: string;
  readonly rule: string;
}

function fixture(files: Files): string {
  const root = join(fixtureRoot, String(++counter));
  for (const [file, content] of Object.entries(files)) {
    const path = join(root, file);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
  }
  return root;
}

async function violationsOf(files: Files, contractId?: string): Promise<string[]> {
  const contract = CONTRACTS.find((candidate) => candidate.id === contractId);
  const rules = ALL_RULES.filter((rule) => !contract?.rules.includes(rule));
  return describeViolations(await findViolations(fixture(files), rules));
}

const layerSeeds: Seed[] = LOOP_LAYERS.flatMap((layer, index): Seed[] => {
  const name = layer[0] ?? '';
  const own = `src/loop/${name}/seed.ts`;
  const above = LOOP_LAYERS[index - 1]?.[0];
  const notAbove: Seed[] =
    above === undefined
      ? []
      : [
          {
            contract: 'layers',
            files: { [`src/loop/${above}/index.ts`]: leaf, [own]: imports(`../${above}/index.ts`) },
            from: own,
            name: `${name} imports ${above} directly`,
            rule: `layers/${layer.join('+')}-not-above`,
          },
          {
            contract: 'layers',
            files: {
              [`src/loop/${above}/index.ts`]: leaf,
              [own]: imports('../log.ts'),
              'src/loop/log.ts': imports(`./${above}/index.ts`),
            },
            from: own,
            name: `${name} reaches ${above} through an unlayered module`,
            rule: `layers/${layer.join('+')}-not-above`,
          },
        ];
  const siblings: Seed[] = layer.flatMap((sibling): Seed[] =>
    layer
      .filter((other) => other !== sibling)
      .map((other) => ({
        contract: 'layers',
        files: {
          [`src/loop/${other}/index.ts`]: leaf,
          [`src/loop/${sibling}/seed.ts`]: imports(`../${other}/index.ts`),
        },
        from: `src/loop/${sibling}/seed.ts`,
        name: `${sibling} imports sibling ${other}`,
        rule: `layers/${sibling}-independent-of-siblings`,
      })),
  );
  return [...notAbove, ...siblings];
});

const seeds: Seed[] = [
  ...layerSeeds,
  {
    contract: 'workflows-public-loop-only',
    files: { 'src/loop/run/index.ts': leaf, 'workflows/plan_implement.ts': imports('../src/loop/run/index.ts') },
    from: 'workflows/plan_implement.ts',
    name: 'workflow imports a private layer module',
    rule: 'workflows-public-loop-only',
  },
  {
    contract: 'workflows-public-loop-only',
    files: { 'src/loop/log.ts': leaf, 'workflows/dev/seed.ts': imports('../../src/loop/log.ts') },
    from: 'workflows/dev/seed.ts',
    name: 'workflow imports an unlayered Loop module',
    rule: 'workflows-public-loop-only',
  },
  {
    contract: 'ticket-orchestration-no-agent-workflow',
    files: { 'workflows/dev/agent.ts': leaf, 'workflows/dev/attempt.ts': imports('./agent.ts') },
    from: 'workflows/dev/attempt.ts',
    name: 'attempt imports agent directly',
    rule: 'ticket-orchestration-no-agent-workflow',
  },
  {
    contract: 'ticket-orchestration-no-agent-workflow',
    files: {
      'workflows/dev/attempt.ts': imports('./checkout.ts'),
      'workflows/dev/checkout.ts': imports('./repositories.ts'),
      'workflows/dev/repositories.ts': leaf,
    },
    from: 'workflows/dev/attempt.ts',
    name: 'attempt reaches repositories through checkout',
    rule: 'ticket-orchestration-no-agent-workflow',
  },
  {
    contract: 'workflows-independent',
    files: { 'workflows/dev/index.ts': leaf, 'workflows/workflow_reporting.ts': imports('./dev/index.ts') },
    from: 'workflows/workflow_reporting.ts',
    name: 'workflow imports another workflow directly',
    rule: 'workflows-independent',
  },
  {
    contract: 'workflows-independent',
    files: {
      'workflows/index.ts': imports('./pr_babysit/settings.ts'),
      'workflows/plan_implement.ts': imports('./index.ts'),
      'workflows/pr_babysit/settings.ts': leaf,
    },
    from: 'workflows/plan_implement.ts',
    name: 'workflow reaches another workflow through a shared module',
    rule: 'workflows-independent',
  },
  {
    contract: 'pr-maintenance-no-scheduler',
    files: {
      'workflows/pr_babysit/infrastructure/scheduler.ts': leaf,
      'workflows/pr_babysit/workflow/maintainer.ts': imports('../infrastructure/scheduler.ts'),
    },
    from: 'workflows/pr_babysit/workflow/maintainer.ts',
    name: 'maintenance imports the scheduler directly',
    rule: 'pr-maintenance-no-scheduler',
  },
  {
    contract: 'pr-maintenance-no-scheduler',
    files: {
      'workflows/pr_babysit/infrastructure/scheduler.ts': leaf,
      'workflows/pr_babysit/settings.ts': imports('./infrastructure/scheduler.ts'),
      'workflows/pr_babysit/workflow/watcher.ts': imports('../settings.ts'),
    },
    from: 'workflows/pr_babysit/workflow/watcher.ts',
    name: 'maintenance reaches the scheduler through settings',
    rule: 'pr-maintenance-no-scheduler',
  },
  {
    contract: 'telemetry-only-in-adapter',
    files: { 'src/loop/clis/runner.ts': imports('@opentelemetry/api') },
    from: 'src/loop/clis/runner.ts',
    name: 'Loop module imports a telemetry package',
    rule: 'telemetry-only-in-adapter',
  },
  {
    contract: 'telemetry-only-in-adapter',
    files: { 'workflows/dev/seed.ts': "export const load = () => import('@opentelemetry/sdk-trace-base');\n" },
    from: 'workflows/dev/seed.ts',
    name: 'workflow dynamically imports a telemetry package',
    rule: 'telemetry-only-in-adapter',
  },
  {
    contract: 'telemetry-only-in-adapter',
    files: {
      'node_modules/@opentelemetry/api/index.js': 'module.exports = {};\n',
      'node_modules/@opentelemetry/api/package.json': '{"name":"@opentelemetry/api","version":"1.0.0","main":"index.js"}',
      'src/loop/hooks/seed.ts': imports('@opentelemetry/api'),
    },
    from: 'src/loop/hooks/seed.ts',
    name: 'Loop module imports an installed telemetry package',
    rule: 'telemetry-only-in-adapter',
  },
  {
    contract: 'telemetry-only-in-adapter',
    files: { 'tests/unit/seed.test.ts': imports('@opentelemetry/api') },
    from: 'tests/unit/seed.test.ts',
    name: 'unrelated test imports a telemetry package',
    rule: 'telemetry-only-in-adapter',
  },
  {
    contract: 'telemetry-only-in-adapter',
    files: { 'src/loop/observe/seed.ts': imports('@opentelemetry/exporter-trace-otlp-http') },
    from: 'src/loop/observe/seed.ts',
    name: 'non-adapter module beside the adapter imports a telemetry package',
    rule: 'telemetry-only-in-adapter',
  },
];

describe('seeded violation fails the contract', () => {
  for (const seed of seeds) {
    test(`${seed.contract}: ${seed.name}`, async () => {
      const violations = await violationsOf(seed.files);
      expect(violations.some((line) => line.startsWith(`${seed.rule}: ${seed.from} -> `))).toBe(true);
    });

    test(`${seed.contract}: ${seed.name} passes once the contract is removed`, async () => {
      expect(await violationsOf(seed.files, seed.contract)).toEqual([]);
    });
  }

  test('every rule of every contract is proven by a seeded violation', () => {
    const proven = new Set(seeds.map((seed) => seed.rule));
    const rules = ALL_RULES.map((rule) => rule.name ?? '');
    expect(rules.filter((name) => !proven.has(name))).toEqual([]);
  });
});

describe('legal imports pass', () => {
  const clean: Record<string, Files> = {
    'a lower layer importing a lower layer': {
      'src/loop/observe/index.ts': leaf,
      'src/loop/run/seed.ts': imports('../observe/index.ts'),
    },
    'a Workflow importing the public Loop entry point': {
      'src/loop/index.ts': leaf,
      'workflows/dev/seed.ts': imports('../../src/loop/index.ts'),
    },
    'a Workflow reaching a private Loop module only through the public entry point': {
      'src/loop/index.ts': imports('./run/index.ts'),
      'src/loop/run/index.ts': leaf,
      'workflows/plan_implement.ts': imports('../src/loop/index.ts'),
    },
    'the telemetry adapter importing the telemetry packages': {
      'src/loop/observe/otel.ts': "import '@opentelemetry/api';\nexport const load = () => import('@opentelemetry/sdk-trace-base');\n",
    },
    'the telemetry adapter test importing the telemetry SDK': {
      'tests/unit/otel.test.ts': imports('@opentelemetry/sdk-trace-base'),
    },
    'ticket orchestration importing other dev modules': {
      'workflows/dev/attempt.ts': imports('./checkout.ts'),
      'workflows/dev/checkout.ts': leaf,
    },
    'unlayered Loop modules importing any layer': {
      'src/loop/builder.ts': leaf,
      'src/loop/log.ts': imports('./builder.ts'),
    },
    'workflows importing their own modules and a Loop entry point through the package name': {
      'src/loop/index.ts': leaf,
      'workflows/dev/a.ts': imports('./b.ts'),
      'workflows/dev/b.ts': imports('loop'),
    },
  };
  for (const [name, files] of Object.entries(clean)) {
    test(name, async () => {
      expect(await violationsOf(files)).toEqual([]);
    });
  }
});

describe('contract set', () => {
  test('the layers contract is generated as 14 rules from one layer list', () => {
    const layers = CONTRACTS.find((contract) => contract.id === 'layers');
    expect(layers?.rules).toHaveLength(14);
  });

  test('every contract except the public-entry and telemetry ones checks indirect imports', () => {
    const direct = new Set(['telemetry-only-in-adapter', 'workflows-public-loop-only']);
    const withoutReachable = CONTRACTS.filter((contract) => !direct.has(contract.id)).flatMap((contract) =>
      contract.rules
        .filter((rule) => !('to' in rule && 'reachable' in rule.to && rule.to.reachable))
        .map((rule) => rule.name ?? ''),
    );
    expect(withoutReachable).toEqual([]);
  });
});
