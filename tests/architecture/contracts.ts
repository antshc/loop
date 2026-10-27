import {
  cruise,
  type IForbiddenRuleType,
  type IViolation,
} from 'dependency-cruiser';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

export interface Contract {
  readonly description: string;
  readonly id: string;
  readonly rules: readonly IForbiddenRuleType[];
}

/** Loop layers, highest first; names within one layer are independent siblings. */
export const LOOP_LAYERS: readonly (readonly string[])[] = [
  ['factory', 'workflow'],
  ['builder', 'dryrun'],
  ['agents'],
  ['clis', 'docker', 'git'],
  ['run'],
  ['hooks'],
  ['sessions'],
  ['observe'],
];

export const WORKFLOWS: readonly string[] = ['dev', 'plan_implement', 'workflow_reporting', 'pr_babysit'];

/** The only module allowed to import the telemetry packages. */
export const TELEMETRY_ADAPTER = '^src/loop/observe/otel(/|\\.ts$)';

/** Tests of the adapter may drive the telemetry SDK directly. */
const TELEMETRY_TESTS = '^(tests|workflows/[^/]+/tests)/.*(otel|telemetry)[^/]*\\.test\\.ts$';

const TELEMETRY_PACKAGES = '(^|/)@opentelemetry/';
const PUBLIC_LOOP_ENTRY = '^src/loop/index\\.ts$';

/** Directories cruised, relative to the root; absent ones are skipped. */
const CRUISED_ROOTS: readonly string[] = ['src', 'workflows', 'tests'];

const modulesIn = (root: string, names: readonly string[]): string => `^${root}/(${names.join('|')})(/|\\.ts$)`;
const loopModules = (names: readonly string[]): string => modulesIn('src/loop', names);

function layerRules(layers: readonly (readonly string[])[]): IForbiddenRuleType[] {
  return layers.flatMap((layer, index): IForbiddenRuleType[] => {
    const higher = layers.slice(0, index).flat();
    const notAbove: IForbiddenRuleType[] =
      higher.length === 0
        ? []
        : [
            {
              comment: 'A layer must not import, even indirectly, any higher layer.',
              from: { path: loopModules(layer) },
              name: `layers/${layer.join('+')}-not-above`,
              severity: 'error',
              to: { path: loopModules(higher), reachable: true },
            },
          ];
    const siblings: IForbiddenRuleType[] =
      layer.length < 2
        ? []
        : layer.map((name) => ({
            comment: 'Layers listed side by side must not import each other, even indirectly.',
            from: { path: loopModules([name]) },
            name: `layers/${name}-independent-of-siblings`,
            severity: 'error',
            to: { path: loopModules(layer.filter((other) => other !== name)), reachable: true },
          }));
    return [...notAbove, ...siblings];
  });
}

export const CONTRACTS: readonly Contract[] = [
  {
    description:
      'The loop library is layered: factory/workflow over builder/dryrun over agents over clis/docker/git over run over hooks over sessions over observe',
    id: 'layers',
    rules: layerRules(LOOP_LAYERS),
  },
  {
    description: 'The workflows import only the public loop entry point (direct imports only)',
    id: 'workflows-public-loop-only',
    rules: [
      {
        comment: 'Direct-only: reaching a private Loop module through the public entry point is allowed.',
        from: { path: '^workflows/' },
        name: 'workflows-public-loop-only',
        severity: 'error',
        to: { path: '^src/loop/', pathNot: PUBLIC_LOOP_ENTRY },
      },
    ],
  },
  {
    description: 'Ticket orchestration imports no agent workflow',
    id: 'ticket-orchestration-no-agent-workflow',
    rules: [
      {
        from: { path: modulesIn('workflows/dev', ['attempt']) },
        name: 'ticket-orchestration-no-agent-workflow',
        severity: 'error',
        to: { path: modulesIn('workflows/dev', ['agent', 'repositories', 'run']), reachable: true },
      },
    ],
  },
  {
    description: 'No workflow imports another workflow',
    id: 'workflows-independent',
    rules: [
      {
        from: { path: modulesIn('workflows', WORKFLOWS) },
        name: 'workflows-independent',
        severity: 'error',
        to: {
          path: modulesIn('workflows', WORKFLOWS),
          pathNot: '^workflows/$1(/|\\.ts$)',
          reachable: true,
        },
      },
    ],
  },
  {
    description: 'PR maintenance decisions do not depend on the discovery scheduler',
    id: 'pr-maintenance-no-scheduler',
    rules: [
      {
        from: { path: modulesIn('workflows/pr_babysit', ['workflow']) },
        name: 'pr-maintenance-no-scheduler',
        severity: 'error',
        to: { path: modulesIn('workflows/pr_babysit/infrastructure', ['scheduler']), reachable: true },
      },
    ],
  },
  {
    description: 'Only the telemetry adapter imports the telemetry packages',
    id: 'telemetry-only-in-adapter',
    rules: [
      {
        comment:
          'Direct only: any chain to the packages ends in a direct import, and importing the adapter itself is legitimate.',
        from: { pathNot: [TELEMETRY_ADAPTER, TELEMETRY_TESTS] },
        name: 'telemetry-only-in-adapter',
        severity: 'error',
        to: { path: TELEMETRY_PACKAGES },
      },
    ],
  },
];

export const ALL_RULES: readonly IForbiddenRuleType[] = CONTRACTS.flatMap((contract) => contract.rules);

/** One line per violated rule and importing module, naming the contract. */
export function describeViolations(violations: readonly IViolation[]): string[] {
  const targets = new Map<string, Set<string>>();
  for (const { from, rule, to } of violations) {
    const key = `${rule.name}: ${from}`;
    targets.set(key, (targets.get(key) ?? new Set<string>()).add(to));
  }
  return [...targets].map(([key, to]) => `${key} -> ${[...to].join(', ')}`);
}

export async function findViolations(
  root: string,
  rules: readonly IForbiddenRuleType[] = ALL_RULES,
): Promise<IViolation[]> {
  const result = await cruise(
    CRUISED_ROOTS.filter((entry) => existsSync(join(root, entry))),
    {
      baseDir: root,
      doNotFollow: { path: 'node_modules' },
      ruleSet: { forbidden: [...rules] },
      tsPreCompilationDeps: true,
      validate: true,
    },
  );
  if (typeof result.output === 'string') throw new TypeError('dependency-cruiser returned text instead of a result');
  return result.output.summary.violations;
}
