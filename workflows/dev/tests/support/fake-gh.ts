import type { GhRunner } from '../../platforms.ts';

type Node = Record<string, unknown>;

const PR_DIFF = 'diff --git a/a b/a\n';

/** Stands in for the `gh` binary: returns canned JSON for reads and records every call. */
export class FakeGh {
  readonly calls: (readonly string[])[] = [];
  readonly #created = new Map<number, Node>();
  readonly #prs: Node[];
  readonly #specs: Node[];
  readonly #tickets: Map<number, Node[]>;

  constructor(
    options: { prs?: Node[]; specs?: Node[]; tickets?: Map<number, Node[]> } = {},
  ) {
    this.#tickets =
      options.tickets ??
      new Map([
        [
          1,
          [
            issueNode(10, 'Add login form'),
            issueNode(11, 'Add login tests', ['hitl']),
            issueNode(12, 'Split login spec', ['spec']),
            issueNode(13, 'Old login task', [], 'CLOSED'),
          ],
        ],
      ]);
    this.#specs = options.specs ?? [
      issueNode(1, 'Add login page', ['spec']),
      issueNode(2, 'Add logout button', ['spec']),
    ];
    this.#prs = options.prs ?? [pullRequestNode(10, 'feature/login')];
  }

  readonly run: GhRunner = (args) => {
    this.calls.push(args);
    const value = (flag: string): string => args[args.indexOf(flag) + 1] ?? '';
    const [first, second] = args;
    if (first === 'pr' && second === 'list') {
      return Promise.resolve(JSON.stringify(this.#prs.filter((pr) => pr.headRefName === value('--head'))));
    }
    if (first === 'pr' && second === 'create') {
      this.#prs.push({ ...pullRequestNode(99, value('--head')), body: value('--body') });
      return Promise.resolve('https://github.com/owner/repo/pull/99\n');
    }
    if (first === 'pr' && second === 'edit') {
      for (const pr of this.#prs) {
        if (String(pr.number) === args[2]) {
          pr.body = value('--body');
        }
      }
      return Promise.resolve('');
    }
    if (first === 'pr' && second === 'diff') {
      return Promise.resolve(PR_DIFF);
    }
    if (first === 'issue' && second === 'create') {
      const number = 101 + this.#created.size;
      const node: Node = { ...issueNode(number, value('--title')), body: value('--body') };
      this.#created.set(number, node);
      return Promise.resolve(String(node.url));
    }
    if (first === 'api' && second === '--method') {
      const spec = Number(args[3]?.split('/').at(-2));
      const child = Number(args.at(-1)?.split('=').at(-1)) - 1000;
      const created = this.#created.get(child);
      if (created !== undefined) {
        this.#tickets.set(spec, [...(this.#tickets.get(spec) ?? []), created]);
      }
      return Promise.resolve('{}');
    }
    if (first === 'api' && args.includes('--jq')) {
      return Promise.resolve(String(1000 + Number(args[1]?.split('/').at(-1))));
    }
    if (first === 'issue') {
      return Promise.resolve('');
    }
    const query = args.find((arg) => arg.startsWith('query=')) ?? '';
    if (query.includes('subIssues')) {
      const number = Number(args.find((arg) => arg.startsWith('number='))?.slice('number='.length));
      const nodes = this.#tickets.get(number) ?? [];
      return Promise.resolve(JSON.stringify([{ data: { repository: { issue: { subIssues: { nodes } } } } }]));
    }
    if (query.includes('labels: [')) {
      return Promise.resolve(JSON.stringify([{ data: { repository: { issues: { nodes: this.#specs } } } }]));
    }
    return Promise.resolve('{}');
  };
}

export function issueNode(number: number, title: string, labels: readonly string[] = [], state = 'OPEN'): Node {
  return {
    body: `Body of #${String(number)}`,
    comments: { nodes: [{ author: { login: 'ann' }, body: 'hello', createdAt: '2026-01-01T00:00:00Z' }, { author: null, body: 'bye', createdAt: '2026-01-02T00:00:00Z' }] },
    labels: { nodes: labels.map((name) => ({ name })) },
    number,
    state,
    title,
    url: `https://github.com/owner/repo/issues/${String(number)}`,
  };
}

function pullRequestNode(number: number, head: string): Node {
  return {
    body: '',
    headRefName: head,
    number,
    title: 'My PR',
    url: `https://github.com/owner/repo/pull/${String(number)}`,
  };
}
