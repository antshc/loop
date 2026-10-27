import { AgentProfile } from './base.ts';
import { CodexCli } from './codex-cli.ts';
import { CopilotCli } from './copilot-cli.ts';

export const copilot = new CopilotCli();
export const codex = new CodexCli();
export const DEFAULT = new AgentProfile(copilot);
