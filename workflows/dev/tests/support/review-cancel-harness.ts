// Child entrypoint for the review cancellation test: a real reviewer agent run in a git worktree, blocked on a fake `copilot`.
import { appendFileSync } from 'node:fs';
import { join } from 'node:path';

import { Agent, BranchStrategy, GitOptions, runEntrypoint } from '../../../../src/loop/index.ts';
import { devWorkflow } from '../../definition.ts';
import { RepositoryId, Spec } from '../../records.ts';
import { reviewDelegate } from '../../review-agent.ts';
import { reviewPullRequest } from '../../review.ts';
import { FakeReviewGit } from './review-fakes.ts';

const [workdir = '.', repository = '.'] = process.argv.slice(2);
const mark = (line: string): void => {
  appendFileSync(join(workdir, 'log'), `${line}\n`);
};

const workflow = devWorkflow({
  logger: {
    info: (message) => {
      mark(`LOG ${message}`);
    },
    warn: (message) => {
      mark(`LOG ${message}`);
    },
  },
});
const git = new FakeReviewGit();
const options = new GitOptions({
  repositoryPath: repository,
  rootPath: join(workdir, 'trees'),
  strategy: new BranchStrategy('review'),
});
const spec = new Spec({ labels: ['spec'], number: 1, title: 'CHK-1: Add basket', url: 'https://github.com/antshc/loop/issues/1' });

await runEntrypoint(async () => {
  await workflow.run(async (phases) => {
    try {
      await reviewPullRequest(
        spec,
        { delivered: true, headSha: 'c0000001', pullRequestUrl: 'https://github.com/antshc/loop/pull/9' },
        { checkout: repository, ownerName: new RepositoryId('antshc', 'loop'), worktrees: join(workdir, 'trees') },
        reviewDelegate(),
        { git, newAgent: () => Agent().withGit(options), phases },
      );
    } finally {
      mark(`STATUS ${phases.executions.filter((phase) => phase.name === 'review').map((phase) => phase.status).join(',')}`);
    }
  });
  mark('COMPLETED');
});
