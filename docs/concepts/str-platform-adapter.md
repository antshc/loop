# Platform Adapter
**Type:** Architecture Pattern

## Purpose

Keep application and orchestration code independent of a specific repository/work-tracking platform such as GitHub or Azure DevOps.

Platform-specific CLIs, field names, states, and response shapes are translated at the infrastructure boundary into one normalized contract.

## Concept

A **Platform Adapter** exposes repository and work-management capabilities through a platform-neutral contract.

Application code depends only on the contract and normalized models such as `Ticket` and `PullRequest`. Each concrete adapter translates those operations to the native platform interface—for example, `gh` for GitHub or `az` for Azure DevOps—and maps native responses back to the normalized model.

Scope: this applies where Loop's own code calls a platform. Clients only a user's workflow calls, such as the `TicketsTracker` in `workflows/dev/platforms.ts`, are concrete helpers with no contract ([Ship Loop as a TypeScript workflow library](../adr/ship-loop-as-a-typescript-workflow-library-with-no-built-in-workflows.md)).

The concrete adapter is selected in the composition root, normally from explicit configuration or the repository remote. Platform detection and construction are outside application behavior.

```text
Application / Workflows
        |
        v
  PlatformAdapter
        ^
      /   \
     /     \
GitHub     Azure DevOps
Adapter      Adapter
   |           |
   v           v
 gh CLI       az CLI
```

The contract represents capabilities the application needs, not the command structure of any one provider.

## Rules

- MUST make application and workflow code depend on the platform-neutral adapter contract rather than a concrete platform implementation.
- MUST define the contract as a TypeScript `interface` whose members are the operations, and MUST make each concrete adapter `implements` it, so an adapter missing an operation fails type-checking.
- MUST keep the contract in the application/runtime layer; adapters import it, never the reverse.
- MUST normalize platform-specific work-item and pull-request data before returning it through the contract.
- MUST translate platform-specific states, identifiers, labels or tags, assignees, and response shapes inside the concrete adapter.
- MUST keep native CLI invocation and parsing inside the concrete adapter.
- MUST provide one concrete adapter per supported platform.
- MUST select and construct the concrete adapter in the composition root or an equivalent infrastructure factory.
- MUST NOT expose `gh`, `az`, or provider-specific response models through the application-facing contract.
- MUST NOT branch on GitHub-versus-Azure-DevOps behavior inside application or workflow code when the difference can be handled by the adapter.
- SHOULD keep the contract limited to capabilities actually required by the application.
- SHOULD validate provider-native JSON at the adapter boundary (a type guard or parser), because the compiler cannot check what a CLI prints.
- SHOULD run one shared contract test suite against every adapter.
- SHOULD detect the platform from durable repository configuration such as the Git remote when no explicit platform configuration is supplied.

## Example

A normalized TypeScript contract:

```ts
interface Ticket {
  readonly id: number;
  readonly title: string;
  readonly state: string;
  readonly tags: readonly string[];
  readonly assignedTo: string | null;
  readonly url: string;
}

interface ListTicketsOptions {
  tags?: readonly string[];
  state?: string;
  limit?: number;
}

interface PlatformAdapter {
  listTickets(options?: ListTicketsOptions): Promise<readonly Ticket[]>;
  addComment(ticketId: number, comment: string): Promise<void>;
  createBranch(name: string, fromBranch?: string): Promise<void>;
}
```

GitHub translates the normalized operation to `gh`:

```ts
interface GhIssue {
  number: number;
  title: string;
  state: string;
  labels: { name: string }[];
  assignees: { login: string }[];
  url: string;
}

class GitHubAdapter implements PlatformAdapter {
  readonly #repo: string;

  constructor(owner: string, repo: string, private readonly gh: (args: readonly string[]) => Promise<string>) {
    this.#repo = `${owner}/${repo}`;
  }

  async listTickets({ tags = [], state, limit }: ListTicketsOptions = {}): Promise<readonly Ticket[]> {
    const args = ['issue', 'list', '--repo', this.#repo, '--json', 'number,title,state,labels,assignees,url'];
    if (state !== undefined) args.push('--state', state);
    if (limit !== undefined) args.push('--limit', String(limit));
    for (const tag of tags) args.push('--label', tag);

    const issues = JSON.parse(await this.gh(args)) as GhIssue[];
    return issues.map((issue) => ({
      id: issue.number,
      title: issue.title,
      state: issue.state.toLowerCase(),
      tags: issue.labels.map((label) => label.name),
      assignedTo: issue.assignees[0]?.login ?? null,
      url: issue.url,
    }));
  }

  // addComment and createBranch call `gh` the same way.
}
```

Azure DevOps implements the same contract but translates it to `az boards` and maps fields such as `System.Id`, `System.Title`, `System.State`, and `System.Tags` into the same `Ticket` model.

Selection stays outside the application:

```ts
function createPlatformAdapter(remoteUrl: string): PlatformAdapter {
  if (remoteUrl.includes('dev.azure.com')) {
    const { org, project, repo } = parseAzureDevOpsRemote(remoteUrl);
    return new AzureDevOpsAdapter(org, project, repo);
  }
  const { owner, repo } = parseGithubRemote(remoteUrl);
  return new GitHubAdapter(owner, repo, runGh);
}
```

The caller remains platform-agnostic:

```ts
const items = await platform.listTickets({ tags: ['ready'], state: 'open' });
```

## Benefits and Trade-offs

**Benefits**

- Keeps orchestration and use-case code independent of GitHub or Azure DevOps.
- Centralizes native CLI commands, parsing, and provider-specific mappings.
- Makes platform implementations replaceable without changing application behavior.
- Gives tests a small contract that can be replaced with a fake adapter.

**Trade-offs**

- The normalized contract can expose only the common or intentionally supported capability set.
- Adapters are coupled to the contract by `implements`; the compiler checks member names and signatures, but not what a provider CLI actually prints.
- Provider-specific features may require an explicit contract extension instead of leaking through the abstraction.
- State and field normalization adds mapping code that must be kept correct as provider behavior changes.

## Validation

A conforming implementation demonstrates that the same application code can operate through at least two concrete platform adapters without provider-specific branching, and that provider-native output is converted to normalized models before crossing the adapter boundary.

## References

- [Squad PlatformAdapter contract](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/types.ts)
- [Squad GitHubAdapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/github.ts)
- [Squad AzureDevOpsAdapter](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/azure-devops.ts)
- [Squad platform adapter factory](https://github.com/bradygaster/squad/blob/dev/packages/squad-sdk/src/platform/index.ts)
