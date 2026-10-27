import { join } from 'node:path';

import { AgentProfile, copilot, Instructions, Permissions } from '../../../src/loop/index.ts';

const DEFAULT_TIMEOUT_SEC = 1800;

// Native denials reinforce prompts; agents still run with host-user permissions.
const PERMISSIONS = new Permissions({ denyTools: ['shell(git commit*)', 'shell(git push*)', 'shell(gh*)'] });

export const ADDRESS = new AgentProfile(copilot, {
  instructions: Instructions.fromFile(join(import.meta.dir, 'address.md')),
  name: 'address',
  permissions: PERMISSIONS,
  timeoutSec: DEFAULT_TIMEOUT_SEC,
});

export const MAINTAINER = new AgentProfile(copilot, {
  instructions: Instructions.fromFile(join(import.meta.dir, 'maintainer.md')),
  name: 'maintainer',
  permissions: PERMISSIONS,
  timeoutSec: DEFAULT_TIMEOUT_SEC,
});
