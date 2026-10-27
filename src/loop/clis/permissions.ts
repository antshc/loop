import { ValueError } from '../value-error.ts';

export interface PermissionsInit {
  denyTools?: readonly string[];
  tools?: 'allow-all' | readonly string[];
}

const POLICY_FLAGS: ReadonlySet<string> = new Set(['--allow-all', '--allow-all-tools', '--allow-tool', '--deny-tool', '--yolo']);

/** Native tool approval patterns, with deny rules taking precedence over allows. */
export class Permissions {
  readonly denyTools: readonly string[];
  readonly tools: 'allow-all' | readonly string[];

  constructor(init: PermissionsInit = {}) {
    const tools: unknown = init.tools ?? 'allow-all';
    const denyTools: unknown = init.denyTools ?? [];
    const toolList: null | unknown[] = Array.isArray(tools) ? (tools as unknown[]) : null;
    if (toolList === null && tools !== 'allow-all') {
      throw new ValueError("tools must be 'allow-all' or an array of tool patterns");
    }
    if (!Array.isArray(denyTools)) {
      throw new ValueError('denyTools must be an array of tool patterns');
    }
    const denyList = denyTools as unknown[];
    const patterns = [...(toolList ?? []), ...denyList];
    if (patterns.some((pattern) => typeof pattern !== 'string' || pattern.trim() === '')) {
      throw new ValueError('tool permission patterns must be nonempty strings');
    }
    this.tools = toolList === null ? 'allow-all' : Object.freeze([...(toolList as string[])]);
    this.denyTools = Object.freeze([...(denyList as string[])]);
    Object.freeze(this);
  }
}

/** The selected CLI cannot enforce the requested permission policy. */
export class UnsupportedPermissions extends ValueError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'UnsupportedPermissions';
  }
}

/** Recognize native tool-policy arguments that conflict with a typed policy. */
export function hasPermissionArgs(args: readonly string[]): boolean {
  return args.some((arg) => POLICY_FLAGS.has(arg.split('=', 1)[0] ?? ''));
}
