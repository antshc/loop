/** The verify stage: a verifier agent drives the integrated app and fixes what the change got wrong; the harness commits the fixes. */
import { mkdirSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import type { AgentWorkspace, Phase, WorkflowRun } from '../../src/loop/index.ts';
import type { GitService } from './checkout.ts';
import type { TicketsTracker } from './platforms.ts';
import type { Spec, VerifyResult, VerifyWorkflow } from './records.ts';

import { ProcessFailedError } from '../../src/loop/index.ts';
import { CommandError } from './commands.ts';
import { indentedJson, ordered, ticketJson } from './format.ts';
import { commitSubject, findVerificationSkills, madeNoCommit, protectedChanges } from './gates.ts';
import { loopSessionName, slugify, TicketWorkflowError } from './records.ts';

/** The settled outcome of app verification; `verdict` is a verdict or `skipped` when the repository has no verification skill. */
export class VerifyReport {
  readonly evidenceDir: null | string;
  readonly fixCommit: string;
  readonly summary: string;
  readonly unfixed: readonly string[];
  readonly verdict: string;
  /** Any verdict but `pass` or `skipped` keeps the PR a draft. */
  get blocksReady(): boolean {
    return this.verdict !== 'pass' && this.verdict !== 'skipped';
  }

  constructor(
    verdict: string,
    summary = '',
    evidenceDir: null | string = null,
    unfixed: readonly string[] = [],
    fixCommit = '',
  ) {
    this.verdict = verdict;
    this.summary = summary;
    this.evidenceDir = evidenceDir;
    this.unfixed = unfixed;
    this.fixCommit = fixCommit;
    Object.freeze(this);
  }
}

export const SKIPPED = new VerifyReport('skipped', 'no app-verification skill found');

export interface VerifyOptions {
  /** Where evidence is kept, outside every worktree. */
  readonly evidenceRoot: string;
  readonly git: GitService;
  readonly phases: WorkflowRun;
  readonly tracker: TicketsTracker;
}

/** Records the settled verification verdict after evidence checks and any fixes. */
export function verifyApp(
  spec: Spec,
  pullRequestUrl: string,
  worktree: AgentWorkspace,
  runVerify: VerifyWorkflow,
  options: VerifyOptions,
): Promise<VerifyReport> {
  return options.phases.phase('verify', { attributes: { spec: spec.number } }, async (phase) => {
    const report = await verifyIntegratedBranch(spec, pullRequestUrl, worktree, runVerify, options, phase);
    phase.set({ verdict: report.verdict });
    if (report.verdict === 'skipped') {
      phase.skip(report.summary);
    } else if (report.blocksReady) {
      phase.fail('Application verification did not pass');
    }
    return report;
  });
}

function emptyResult(verdict: string): Required<VerifyResult> {
  return { commitMessage: '', findings: [], summary: '', verdict, verification: '' };
}

/** `%Y%m%dT%H%M%S%f` of the local time. */
function evidenceStamp(now: Date): string {
  const pad = (value: number, width = 2): string => String(value).padStart(width, '0');
  const date = `${String(now.getFullYear())}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  return `${date}T${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}${pad(now.getMilliseconds() * 1000, 6)}`;
}

async function postVerdict(spec: Spec, tracker: TicketsTracker, report: VerifyReport): Promise<void> {
  const lines = [
    `dev: app verification **${report.verdict}**`,
    '',
    report.summary,
    '',
    `Evidence: \`${report.evidenceDir ?? 'None'}\``,
  ];
  if (report.fixCommit !== '') {
    lines.push(`Fix commit: ${report.fixCommit}`);
  }
  if (report.unfixed.length > 0) {
    lines.push('', 'Unfixed findings:', ...report.unfixed.map((item) => `- ${item}`));
  }
  spec.comment(lines.join('\n'));
  await tracker.updateSpec(spec);
}

/** Verifies the integrated branch when the repository has a verification skill; fix commits are pushed, the verdict is posted on the Spec. */
async function verifyIntegratedBranch(
  spec: Spec,
  pullRequestUrl: string,
  worktree: AgentWorkspace,
  runVerify: VerifyWorkflow,
  options: VerifyOptions,
  phase: Phase,
): Promise<VerifyReport> {
  const { git, phases, tracker } = options;
  const { baseBranch, featureBranch } = spec;
  if (baseBranch === null || featureBranch === null) {
    throw new TicketWorkflowError(`cannot resolve branches for Spec ${spec.url}`);
  }
  const skills = findVerificationSkills(worktree.path);
  if (skills.length === 0) {
    return SKIPPED;
  }
  phase.set({ skills: skills.length });
  const evidenceDir = join(options.evidenceRoot, slugify(spec.initiative), evidenceStamp(new Date()));
  mkdirSync(evidenceDir, { recursive: true });
  const headBefore = await git.head(worktree.path);
  let result: Required<VerifyResult> = emptyResult('inconclusive');
  let note = '';
  try {
    const context = {
      baseBranch,
      branchCommits: (await git.branchCommits(worktree.path, baseBranch)).map(
        (commit) => `${commit.sha.slice(0, 7)} ${commit.subject}`,
      ),
      evidenceDir,
      featureBranch,
      pullRequestUrl,
      sessionName: loopSessionName(spec.number, spec.title),
      skills,
      specJson: indentedJson(ordered([
        ['number', spec.number],
        ['title', spec.title],
        ['url', spec.url],
        ['body', spec.body],
      ])),
      ticketsJson: indentedJson(spec.tickets.map(ticketJson)),
    };
    result = { ...result, ...(await runVerify(context, worktree)) };
  } catch (error) {
    if (!(error instanceof CommandError || error instanceof ProcessFailedError || error instanceof TicketWorkflowError)) {
      throw error;
    }
    note = `verifier failed: ${error.message}`;
    phase.fail('Verifier could not complete');
  }
  phase.set({ verdict: result.verdict });
  const moved = await madeNoCommit(git, worktree.path, headBefore.sha);
  if (!phase.gate('the agent did not commit', moved === null, moved ?? '')) {
    note = moved ?? '';
  }
  if (note === '' && result.verdict !== 'inconclusive') {
    const evidence = readdirSync(evidenceDir).length > 0;
    if (!phase.gate('evidence was saved', evidence, `no evidence in ${evidenceDir}`)) {
      note = `no evidence in ${evidenceDir}`;
    }
  }
  let verdict = note === '' ? result.verdict : 'inconclusive';
  if (note !== '') {
    await phases.phase('restore', { attributes: { to: headBefore.sha } }, () => git.restore(worktree.path, headBefore.sha));
  }
  let fixCommit = '';
  if (note === '') {
    const files = await git.changedFiles(worktree.path);
    const forbidden = protectedChanges(files);
    if (forbidden.length > 0) {
      note = `fixes touched the proof framework and were discarded: ${forbidden.join(', ')}`;
      await phases.phase('restore', { attributes: { to: headBefore.sha } }, () =>
        git.restore(worktree.path, headBefore.sha),
      );
      verdict = verdict === 'pass' ? 'partial' : verdict;
    } else if (files.length > 0) {
      const subject = commitSubject(`fix(${spec.initiative}): `, result.commitMessage, 'fix verification findings');
      fixCommit = await phases.phase('commit-fixes', { attributes: { spec: spec.number } }, async (committing) => {
        const commit = await git.commit(worktree.path, subject, result.summary);
        committing.set({ commit: commit.sha });
        await git.pushIfAhead(worktree.path, featureBranch, baseBranch);
        return commit.sha;
      });
    }
  }
  const unfixed = result.findings
    .filter((finding) => finding.status !== 'fixed')
    .map((finding) => finding.summary + ((finding.reason ?? '') === '' ? '' : ` (${finding.reason ?? ''})`));
  if (verdict === 'pass' && unfixed.length > 0) {
    verdict = 'partial';
  }
  const report = new VerifyReport(verdict, note === '' ? result.summary : note, evidenceDir, unfixed, fixCommit);
  await postVerdict(spec, tracker, report);
  return report;
}
