// Test double for the GitHub CLI: `bun fake-gh.ts <gh arguments>`.
// FAKE_GH_STATE is `open` (default) or `closed`; FAKE_GH_HANG names an argument substring on which the process
// records its parent pid in FAKE_GH_HANG_FILE and then hangs, ignoring SIGTERM.
import { writeFileSync } from 'node:fs';

const argv = process.argv.slice(2);
const line = argv.join(' ');
const closed = process.env.FAKE_GH_STATE === 'closed';
const hang = process.env.FAKE_GH_HANG;

if (hang !== undefined && line.includes(hang)) {
  if (process.env.FAKE_GH_HANG_FILE !== undefined) {
    writeFileSync(process.env.FAKE_GH_HANG_FILE, JSON.stringify({ parent: process.ppid, self: process.pid }));
  }
  process.on('SIGTERM', () => undefined);
  setInterval(() => undefined, 1000);
} else {
  const pr = (): object => ({
    draft: false,
    head: { ref: 'feature', repo: { full_name: 'a/b' }, sha: 'sha1' },
    html_url: 'https://github.com/a/b/pull/1',
    mergeable: true,
    mergeable_state: 'clean',
    merged: false,
    number: 1,
    requested_reviewers: [],
    state: closed ? 'closed' : 'open',
    user: { login: 'me' },
  });
  const empty = { nodes: [], pageInfo: { endCursor: null, hasNextPage: false } };
  let output: unknown = {};
  if (line === 'api user') output = { login: 'me' };
  else if (line.includes('pulls?state=open')) output = [closed ? [] : [pr()]];
  else if (line.includes('repos/a/b/pulls/1') && !line.includes('reviews')) output = pr();
  else if (line.startsWith('pr view')) output = { mergeStateStatus: 'CLEAN', reviewDecision: '' };
  else if (line.includes('graphql')) output = { data: { repository: { pullRequest: { reviewThreads: empty } } } };
  else if (line.includes('check-runs')) output = [{ check_runs: [] }];
  else if (line.includes('actions/runs')) output = [{ workflow_runs: [] }];
  else if (line.includes('--slurp')) output = [[]];
  process.stdout.write(JSON.stringify(output));
}
