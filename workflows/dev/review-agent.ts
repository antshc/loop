/** The reviewer's named prompt and the agent delegate bound to it. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { AgentRequest, type AgentWorkspace, DEFAULT, getLogger } from '../../src/loop/index.ts';
import { Owner } from './definition.ts';
import { type ReviewContext, type ReviewResult, type ReviewWorkflow, TicketWorkflowError } from './records.ts';
import { ReviewResponse } from './review-reply.ts';

const logger = getLogger('workflow.dev');

const PROMPTS = join(import.meta.dir, 'prompts');
const PLACEHOLDER = /\{\{\s*([A-Za-z_]\w*)\s*\}\}/g;

export const REVIEWER = DEFAULT.with({ name: Owner.REVIEWER });

/** A prompt template placeholder has no matching argument. */
export class ReviewPromptError extends TicketWorkflowError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'PromptError';
  }
}

/** Reads the `review` Markdown template from the dev prompt folder. */
export function loadReviewTemplate(): string {
  return readFileSync(join(PROMPTS, 'review.md'), 'utf8');
}

/** Replaces each `{{KEY}}` placeholder; a missing argument raises, an unused one is logged. */
export function renderReviewPrompt(template: string, args: Readonly<Record<string, string>>): string {
  const used = new Set<string>();
  const rendered = template.replaceAll(PLACEHOLDER, (_match, key: string) => {
    const value = Object.hasOwn(args, key) ? args[key] : undefined;
    if (value === undefined) {
      throw new ReviewPromptError(`missing prompt argument: ${key}`);
    }
    used.add(key);
    return value;
  });
  for (const key of Object.keys(args).filter((name) => !used.has(name)).sort()) {
    logger.warning(`unused prompt argument: ${key}`);
  }
  return rendered;
}

/** Values for every placeholder in the reviewer prompt template. */
export function reviewArgs(context: ReviewContext): Record<string, string> {
  return {
    HEAD_SHA: context.headSha,
    INITIATIVE: context.initiative,
    PULL_REQUEST_URL: context.pullRequestUrl,
    SOURCE_ROOT: context.sourceRoot,
  };
}

/** The delegate for `RepositoryWorkflow.runReview`: one read-only reviewer agent bound to the `review` template. */
export function reviewDelegate(template: string = loadReviewTemplate()): ReviewWorkflow {
  return (context, workspace) => reviewWithAgent(context, workspace, { template });
}

/** Runs one read-only reviewer agent on the checkout; it posts its own findings through the `code-review` skill. */
export async function reviewWithAgent(
  context: ReviewContext,
  workspace: AgentWorkspace,
  options: { readonly template: string },
): Promise<ReviewResult> {
  const prompt = renderReviewPrompt(options.template, reviewArgs(context));
  const { output } = await workspace.agent(REVIEWER).run(new AgentRequest(prompt, context.sessionName));
  return new ReviewResponse().decode(output);
}
