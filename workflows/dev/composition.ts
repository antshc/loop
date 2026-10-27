/** The dev composition root: the only place that binds implementations to the seam tokens, then rebinds caller overrides. */
import 'reflect-metadata';
import { Container, type ServiceIdentifier } from 'inversify';
import { resolve } from 'node:path';

import type { DevOptions } from './args.ts';
import type { TracingObserver } from './telemetry.ts';

import { Agent, otlpObserver, type OutputStream } from '../../src/loop/index.ts';
import { CliGitService } from './checkout.ts';
import { runCommand } from './commands.ts';
import { type DevDependencies, LOG_DIR_NAME, LOOP_HOOKS as SHIPPED_LOOP_HOOKS } from './dependencies.ts';
import { Fakes } from './dry.ts';
import { FileExecutionStore } from './journal.ts';
import { type GithubFactory, githubFor, TicketsTracker } from './platforms.ts';
import { configuredRepositories, HARNESS } from './repositories.ts';
import { DevRunner } from './runner.ts';
import {
  AGENT_FACTORY,
  CLOCK,
  COMMAND_RUNNER,
  CURRENT_DIRECTORY,
  DEV_OPTIONS,
  EXECUTION_STORE,
  GIT_SERVICE,
  GITHUB_FACTORY,
  HARNESS_GITHUB,
  HARNESS_REPOSITORY,
  LOG_DIRECTORY,
  LOOP_HOOKS,
  OBSERVER_FACTORY,
  REPOSITORIES,
  SESSION_LOG_READERS,
  STDERR,
  TICKETS_TRACKER,
} from './tokens.ts';

/**
 * A container with the shipped implementation bound to every token (shared services as singletons), then each
 * member of `dependencies` rebound over it. `--fake` seams replace the shipped implementations; an override wins over both.
 */
export function composeDev(options: DevOptions, dependencies: DevDependencies, stderr: OutputStream): Container {
  const fakes = new Fakes(options.fake, { rework: options.rework, speed: options.fakeSpeed });
  const container = new Container({ defaultScope: 'Singleton' });

  container.bind(DEV_OPTIONS).toConstantValue(options);
  container.bind(STDERR).toConstantValue(stderr);
  container.bind(HARNESS_REPOSITORY).toConstantValue(HARNESS);
  container.bind(CLOCK).toConstantValue(() => performance.now());
  container.bind(CURRENT_DIRECTORY).toConstantValue(() => process.cwd());
  container.bind(LOOP_HOOKS).toConstantValue(SHIPPED_LOOP_HOOKS);
  container.bind(COMMAND_RUNNER).toConstantValue(runCommand);
  container.bind(OBSERVER_FACTORY).toConstantValue(defaultObserver);
  container
    .bind(LOG_DIRECTORY)
    .toDynamicValue((context) =>
      resolve(context.get<() => string>(CURRENT_DIRECTORY)(), options.logDir ?? LOG_DIR_NAME),
    );

  const git = fakes.git();
  if (git === null) {
    container.bind(GIT_SERVICE).to(CliGitService);
  } else {
    container.bind(GIT_SERVICE).toConstantValue(git);
  }
  if (fakes.seams.has('store')) {
    container.bind(EXECUTION_STORE).toDynamicValue(() => fakes.store());
  } else {
    container.bind(EXECUTION_STORE).to(FileExecutionStore);
  }
  container.bind(GITHUB_FACTORY).toDynamicValue(() => fakes.githubFactory());
  container.bind(AGENT_FACTORY).toDynamicValue(() => fakes.newAgent() ?? Agent);
  container.bind(SESSION_LOG_READERS).toDynamicValue(() => fakes.readers());
  container.bind(REPOSITORIES).toDynamicValue(() => configuredRepositories(HARNESS));
  container
    .bind(HARNESS_GITHUB)
    .toDynamicValue((context) => githubFor(HARNESS, context.get<GithubFactory | null>(GITHUB_FACTORY)));
  container.bind(TICKETS_TRACKER).toDynamicValue((context) => {
    const github = context.get<ReturnType<typeof githubFor>>(HARNESS_GITHUB);
    return new TicketsTracker(HARNESS.ownerName.owner, HARNESS.ownerName.repo, (args) => github.run(args));
  });
  container.bind(DevRunner).toSelf();

  rebindOverrides(container, dependencies);
  return container;
}

function defaultObserver(endpoint: string): Promise<TracingObserver> {
  return otlpObserver({ endpoint, resourcePerAgent: true, serviceName: 'dev' });
}

/** Rebinds the token of each override that is given; `env`, `stdout` and `stderr` are read before composition. */
function rebindOverrides(container: Container, dependencies: DevDependencies): void {
  const overrides: readonly [ServiceIdentifier, unknown][] = [
    [AGENT_FACTORY, dependencies.newAgent],
    [CLOCK, dependencies.clock],
    [CURRENT_DIRECTORY, dependencies.cwd],
    [EXECUTION_STORE, dependencies.store],
    [GIT_SERVICE, dependencies.git],
    [GITHUB_FACTORY, dependencies.githubFactory],
    [LOOP_HOOKS, dependencies.loopHooks],
    [OBSERVER_FACTORY, dependencies.newObserver],
    [REPOSITORIES, dependencies.repositories],
    [SESSION_LOG_READERS, dependencies.readers],
  ];
  for (const [token, value] of overrides) {
    if (value !== undefined) {
      container.rebind(token).toConstantValue(value);
    }
  }
}
