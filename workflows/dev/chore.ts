/** The chore step: capture the published pull request's diff on the worktree, run a cleanup agent, publish its commit. */
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { AgentWorkspace, WorkflowRun } from '../../src/loop/index.ts';
import type { GitService } from './checkout.ts';
import type { GitHubRepo } from './platforms.ts';
import type { ChoreResult, ChoreWorkflow, Commit, Spec } from './records.ts';

import { getLogger, ProcessFailedError } from '../../src/loop/index.ts';
import { CommandError } from './commands.ts';
import { commitSubject, madeNoCommit } from './gates.ts';
import { loopSessionName, TicketWorkflowError } from './records.ts';

const logger = getLogger('workflow.chore');

export interface ChoreOptions {
  readonly git: GitService;
  readonly github: GitHubRepo;
  readonly phases: WorkflowRun;
}

/** How the chore step ended: `committed` (with `commit`), `nothing` to change, or `discarded` (with the violation as `note`). */
export interface ChoreReport {
  readonly commit: string;
  readonly note: string;
  readonly outOfScope: readonly string[];
  readonly status: string;
}

export const DIFF_DIR = join('bin', 'review_diff');
export const MANIFEST_NAME = '_manifest.tsv';

const DIFF_HEADER = 'diff --git ';
const SAME_PATH_HEADER = /^diff --git a\/(?<path>.+) b\/\k<path>$/;
const RENAMED_HEADER = /^diff --git a\/.+ b\/(?<path>.+)$/;
const LINES_WITH_ENDINGS = /[^\n\r]*(?:\r\n|\n|\r)|[^\n\r]+$/g;

/**
 * Writes one diff file per changed path, plus `_manifest.tsv` (`REAL_PATH<TAB>SANITIZED_FILENAME`), under `bin/review_diff` of `worktree`.
 *
 * The worktree already holds the PR branch, so no checkout is needed; the manifest is the only source of real paths.
 */
export async function capturePullRequestDiff(github: GitHubRepo, pullRequestUrl: string, worktree: string): Promise<string> {
  const diff = await github.run(['pr', 'diff', pullRequestUrl, '--repo', github.slug]);
  const directory = join(worktree, DIFF_DIR);
  rmSync(directory, { force: true, recursive: true });
  mkdirSync(directory, { recursive: true });
  const manifest: string[] = [];
  for (const [path, lines] of splitByFile(diff)) {
    const sanitized = uniqueName(path.replaceAll('/', '_'), directory);
    writeFileSync(join(directory, sanitized), lines.join(''));
    manifest.push(`${path}\t${sanitized}\n`);
  }
  writeFileSync(join(directory, MANIFEST_NAME), manifest.join(''));
  return directory;
}

/** Runs one cleanup agent after the PR exists; the harness commits its edits as one chore commit and pushes it. Best effort: any failure restores the worktree to the delivered commit and leaves the PR as it was. */
export async function runChore(
  spec: Spec,
  pullRequestUrl: string,
  worktree: AgentWorkspace,
  runChoreAgent: ChoreWorkflow,
  options: ChoreOptions,
): Promise<ChoreReport> {
  const { git, github, phases } = options;
  const { baseBranch, featureBranch } = spec;
  if (baseBranch === null || featureBranch === null) {
    throw new TicketWorkflowError(`cannot resolve branches for Spec ${spec.url}`);
  }
  const headBefore = await git.head(worktree.path);
  let result: ChoreResult = {};
  const violation = await phases.phase('chore', { attributes: { spec: spec.number } }, async (phase) => {
    let found: null | string;
    try {
      const diffDir = await capturePullRequestDiff(github, pullRequestUrl, worktree.path);
      try {
        const commits = (await git.branchCommits(worktree.path, baseBranch)).map(describe);
        result = await runChoreAgent(
          {
            baseBranch,
            branchCommits: commits,
            diffDir,
            featureBranch,
            pullRequestUrl,
            sessionName: loopSessionName(spec.number, spec.title),
          },
          worktree,
        );
      } finally {
        rmSync(diffDir, { force: true, recursive: true });
      }
      found = await madeNoCommit(git, worktree.path, headBefore.sha);
    } catch (error) {
      if (!isExpected(error)) {
        throw error;
      }
      found = `chore agent failed: ${error.message}`;
    }
    phase.gate('the agent did not commit', found === null, found ?? '');
    return found;
  });
  const outOfScope = result.outOfScope ?? [];
  if (violation !== null) {
    logger.warning(`chore for ${pullRequestUrl} discarded: ${violation}`);
    await phases.phase('restore', { attributes: { to: headBefore.sha } }, () => git.restore(worktree.path, headBefore.sha));
    return { commit: '', note: violation, outOfScope, status: 'discarded' };
  }
  if ((await git.changedFiles(worktree.path)).length === 0) {
    return { commit: '', note: result.summary ?? '', outOfScope, status: 'nothing' };
  }
  const subject = commitSubject(`chore(${spec.initiative}): `, result.commitMessage ?? '', 'refine the change set');
  const commit = await phases.phase('commit-chore', { attributes: { spec: spec.number } }, async (phase) => {
    const created = await git.commit(worktree.path, subject, result.summary ?? '');
    phase.set({ commit: created.sha });
    await git.pushIfAhead(worktree.path, featureBranch, baseBranch);
    return created;
  });
  return { commit: commit.sha, note: result.summary ?? '', outOfScope, status: 'committed' };
}

function describe(commit: Commit): string {
  const entry = `${commit.sha.slice(0, 7)} ${commit.subject}`;
  return commit.body === ''
    ? entry
    : `${entry}\n${commit.body.replaceAll(/^(?=.*\S)/gm, '    ')}`;
}

function headerPath(header: string): string {
  const match = SAME_PATH_HEADER.exec(header) ?? RENAMED_HEADER.exec(header);
  const path = match?.groups?.path;
  if (path === undefined) {
    throw new TicketWorkflowError(`unparseable diff header: ${JSON.stringify(header)}`);
  }
  return path;
}

/** Failures of the cleanup step that leave the delivered commit intact; anything else is a defect. */
function isExpected(error: unknown): error is Error {
  return (
    error instanceof CommandError ||
    error instanceof ProcessFailedError ||
    error instanceof TicketWorkflowError ||
    (error instanceof Error && 'syscall' in error)
  );
}

function splitByFile(diff: string): [string, string[]][] {
  const files: [string, string[]][] = [];
  for (const line of diff.match(LINES_WITH_ENDINGS) ?? []) {
    if (line.startsWith(DIFF_HEADER)) {
      files.push([headerPath(line.replace(/[\r\n]+$/, '')), []]);
    }
    files.at(-1)?.[1].push(line);
  }
  return files;
}

/** Flattening `/` to `_` can map two paths to one name; the manifest still tells them apart. */
function uniqueName(name: string, directory: string): string {
  let candidate = name;
  let counter = 1;
  while (existsSync(join(directory, candidate)) || candidate === MANIFEST_NAME) {
    counter += 1;
    candidate = `${String(counter)}_${name}`;
  }
  return candidate;
}
