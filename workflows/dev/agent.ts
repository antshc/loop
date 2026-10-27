/** Named prompt templates and the default agent delegates of the dev Workflow. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { AgentWorkspace } from '../../src/loop/index.ts';
import type {
  AgentCommitSubject,
  ChoreContext,
  ChoreResult,
  PlanContext,
  PlanResult,
  Ticket,
  TicketContext,
  TicketResult,
  VerifyContext,
  VerifyResult,
} from './records.ts';

import { AgentRequest, DEFAULT, getLogger } from '../../src/loop/index.ts';
import { Owner } from './definition.ts';
import { commentJson, indentedJson, ordered, ticketJson } from './format.ts';
import { TicketWorkflowError } from './records.ts';
import { ChoreResponse, DevResponse, PlanResponse, VerifyResponse } from './responses.ts';

const logger = getLogger('workflow.dev');

export const PROMPTS = join(import.meta.dir, 'prompts');
export const PROMPT = join(PROMPTS, 'implement.md');

export const DEVELOPER = DEFAULT.with({ name: Owner.DEVELOPER });
export const PLANNER = DEFAULT.with({ name: Owner.PLANNER });
export const VERIFIER = DEFAULT.with({ name: Owner.VERIFIER });
export const CHORE = DEFAULT.with({ name: Owner.CHORE });

const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

/** A prompt template placeholder has no matching argument. */
export class PromptError extends TicketWorkflowError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'PromptError';
  }
}

/** Values for every placeholder in the chore prompt template. */
export function choreArgs(context: ChoreContext, worktree: string): Record<string, string> {
  return {
    BRANCH_COMMITS: (context.branchCommits ?? []).join('\n\n') || 'No commits exist on the feature branch yet.',
    DIFF_DIR: context.diffDir,
    FEATURE_BRANCH: context.featureBranch,
    PULL_REQUEST_URL: context.pullRequestUrl,
    TARGET_BRANCH: context.baseBranch,
    WORKTREE_PATH: worktree,
  };
}

/** Runs one fresh chore agent over the pull request's captured diff; its edits stay uncommitted in the worktree. */
export async function choreChangeSet(context: ChoreContext, worktree: AgentWorkspace, template: string): Promise<ChoreResult> {
  const prompt = renderPrompt(template, choreArgs(context, worktree.path));
  const { output } = await worktree.agent(CHORE).run(new AgentRequest(prompt, context.sessionName ?? null));
  return new ChoreResponse().decode(output);
}

/** Runs one fresh implementation agent and decodes its response for the selected Ticket. */
export async function implementTicket(context: TicketContext, worktree: AgentWorkspace, template: string): Promise<TicketResult> {
  const args = promptArgs(
    context.ticket,
    context.identifier,
    context.initiativeCommits,
    worktree.path,
    context.baseBranch,
    context.featureBranch,
  );
  const { output } = await worktree.agent(DEVELOPER).run(new AgentRequest(renderPrompt(template, args)));
  return new DevResponse(context.identifier).decode(output);
}

/** Reads a named Markdown template from the dev prompt folder. */
export function loadPrompt(name: string, directory: string = PROMPTS): string {
  return readFileSync(join(directory, `${name}.md`), 'utf8');
}

/** Values for every placeholder in the planner prompt template. */
export function planArgs(context: PlanContext, checkout: string): Record<string, string> {
  const { spec } = context;
  const featureBranch = spec.featureBranch;
  if (featureBranch === null) {
    throw new TicketWorkflowError(`cannot resolve feature branch for Spec ${spec.url}`);
  }
  const specJson = ordered([
    ['number', spec.number],
    ['title', spec.title],
    ['url', spec.url],
    ['body', spec.body],
    ['comments', spec.comments.map(commentJson)],
  ]);
  return {
    CHECKOUT_PATH: checkout,
    FEATURE_BRANCH: featureBranch,
    SPEC_JSON: indentedJson(specJson),
    TARGET_BRANCH: context.baseBranch,
  };
}

/** Runs one read-only planner agent on the checkout and decodes the Tickets it proposes. */
export async function planTickets(context: PlanContext, workspace: AgentWorkspace, template: string): Promise<PlanResult> {
  const prompt = renderPrompt(template, planArgs(context, context.sourceRoot ?? workspace.path));
  const { output } = await workspace.agent(PLANNER).run(new AgentRequest(prompt, context.sessionName ?? null));
  return new PlanResponse().decode(output);
}

/** Values for every placeholder in the dev prompt template. */
export function promptArgs(
  ticket: Ticket,
  identifier: AgentCommitSubject,
  initiativeCommits: readonly string[],
  worktree: string,
  baseBranch: string,
  featureBranch: string,
): Record<string, string> {
  return {
    COMMIT_SUBJECT_PREFIX: identifier.toSubject(),
    FEATURE_BRANCH: featureBranch,
    INITIATIVE_COMMITS:
      initiativeCommits.join('\n') || 'No task commits for this Initiative exist on the feature branch yet.',
    TARGET_BRANCH: baseBranch,
    TICKET_JSON: indentedJson(ticketJson(ticket)),
    WORKTREE_PATH: worktree,
  };
}

/** Replaces each `{{KEY}}` placeholder; a missing argument raises, an unused one is logged. */
export function renderPrompt(template: string, args: Readonly<Record<string, string>>): string {
  const used = new Set<string>();
  const rendered = template.replaceAll(PLACEHOLDER, (_match, key: string) => {
    const value = Object.hasOwn(args, key) ? args[key] : undefined;
    if (value === undefined) {
      throw new PromptError(`missing prompt argument: ${key}`);
    }
    used.add(key);
    return value;
  });
  for (const key of Object.keys(args).filter((name) => !used.has(name)).sort()) {
    logger.warning(`unused prompt argument: ${key}`);
  }
  return rendered;
}

/** Values for every placeholder in the verifier prompt template. */
export function verifyArgs(context: VerifyContext, worktree: string): Record<string, string> {
  return {
    BRANCH_COMMITS: (context.branchCommits ?? []).join('\n\n') || 'No commits exist on the feature branch yet.',
    EVIDENCE_DIR: context.evidenceDir,
    FEATURE_BRANCH: context.featureBranch,
    PULL_REQUEST_URL: context.pullRequestUrl,
    SPEC_JSON: context.specJson,
    TARGET_BRANCH: context.baseBranch,
    TICKETS_JSON: context.ticketsJson,
    VERIFICATION_SKILLS: context.skills.map((skill) => `- \`${skill}\``).join('\n   '),
    WORKTREE_PATH: worktree,
  };
}

/** Runs one fresh verifier agent that drives the app and fixes code-caused failures, leaving the fixes uncommitted. */
export async function verifyIntegratedApp(context: VerifyContext, worktree: AgentWorkspace, template: string): Promise<VerifyResult> {
  const prompt = renderPrompt(template, verifyArgs(context, worktree.path));
  const { output } = await worktree.agent(VERIFIER).run(new AgentRequest(prompt, context.sessionName ?? null));
  return new VerifyResponse().decode(output);
}
