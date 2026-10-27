import type { AgentContext } from '../run/context.ts';

/** The injectable seam that decides how the agent runs inside a container. */
export interface DockerService {
  configure: (context: AgentContext) => Promise<AgentContext>;
}

export class DockerRuntime implements DockerService {
  configure(context: AgentContext): Promise<AgentContext> {
    // Demonstration only: records intent; the runner does not launch Docker.
    return Promise.resolve(context.with({ dockerImage: 'agent:latest' }));
  }
}
