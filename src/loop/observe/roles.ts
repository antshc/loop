/** Shared resource names for observation: a backend such as Aspire colors each role apart, so Workflows reuse them. */
export const Role = {
  ALL: ['engineer', 'code', 'planner', 'developer', 'reviewer'],
  CODE: 'code',
  DEVELOPER: 'developer',
  // The person whose request starts a run.
  ENGINEER: 'engineer',
  PLANNER: 'planner',
  REVIEWER: 'reviewer',
} as const;
