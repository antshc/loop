# Experiment: Copilot CLI tool, path, and folder permissions in `-p` mode

Issue: [#21](https://github.com/antshc/shipyard/issues/21). Research under test: [copilot-cli-filesystem-access-and-permission-flags.md](../research/copilot-cli-filesystem-access-and-permission-flags.md). Run 2026-10-03.

## Question

With `copilot -p` and the permission flags (`--allow-all-tools`, `--yolo`, `--allow-all-paths`, `--add-dir`, `--allow-tool`, `--deny-tool`, URL flags), what can the agent read, write, and execute outside the cwd, which flags observably change that, and which claims the research left **UNVERIFIED** hold on CLI 1.0.79? Custom-agent resolution is out of scope (#5).

## Method

- **CLI under test:** native `copilot` 1.0.79 from `npm install --prefix <scratch> @github/copilot@1.0.79`, run with `--no-auto-update` and `COPILOT_AUTO_UPDATE=false`. Model `gpt-5-mini`, `--effort low`, `--no-ask-user` unless noted.
- **Fixture (scratch, deleted):** git repo `repo/` with `sub/` and `src/`, a sibling `outside/` dir, an `extra/` dir, a regular file, `~/.copilot`, `/tmp`, and an untrusted cwd under `/var/tmp`.
- **Probes:** one prompt per case that forces exactly one tool call (`view`, `create`, `bash`, `web_fetch`), no retries. The verdict is the CLI's own `tool.execution_complete` event (`success`, `error.code`) from `--output-format json`, plus the filesystem afterwards (file exists or not). The model's reply is not used as evidence.
- **Exit codes** come from invoking the native binary directly.
- **Cross-check:** all 102 cases were first run on 1.0.91 through the VS Code shim (see Environment pitfalls). Tool outcomes were identical except the cases noted in [Version differences](#version-differences) and one model variance (`X0`, the model skipped a probe step).
- **Caveats:** `/home/pet` is in `trustedFolders`, so most cases ran in a trusted cwd. `bwrap` is not installed, so sandbox containment could not be tested. Case IDs below refer to the harness cases.

## Observations (1.0.79)

### Paths

| Claim | Result | Cases |
|---|---|---|
| Default access = cwd tree + system temp | Confirmed. `view` and read-only shell (`ls`, `cat`) in cwd succeed with no flags. `/tmp` readable and (with tools allowed) writable. | A1, G6, G7, T1, T3 |
| Git root above cwd | **Not granted.** From `repo/sub`, `view`/`cat`/`create`/`echo >` on `repo/top.txt` are all denied, even with `--allow-all-tools`. Run from the repo root. | A4, A4b, A5, A6, A7 |
| Outside the tree | Needs `--add-dir <dir>` or `--allow-all-paths` for the path, and a tool grant for writes. `--allow-all-tools` alone does not open paths (view, `cat`, `create`, `echo >` all denied). `--add-dir` alone lets `view` read but `create` is denied. `--add-dir` + `--allow-all-tools` writes OK. | A8-A16 |
| `~/.copilot` | Not special-cased. Denied by default for `view` and shell (also `session-state/`), and `--allow-all-tools` alone does not help. `--allow-all-paths` opens `view` (settings.json, `session-state/<id>/events.jsonl`), and `--allow-all-paths` + tools allows writing there. | H1-H9 |
| `--disallow-temp-dir` | Confirmed. `/tmp` read and write both denied with it. | T2, T4 |
| `--add-dir` missing path | Fails fast at startup, exit 1, no model call. | V1 |
| `--add-dir` regular file | **Does not fail on 1.0.79.** Exit 0, run proceeds and answers. (1.0.91 fails fast, exit 1.) | V2 |
| `--add-dir` relative path | Without `-C`, resolves against the launch cwd and works. With `-C dir --add-dir ../x`, resolves against `-C` and works. With `--add-dir ../x -C dir` it resolves against the **launch** cwd and fails (exit 1, `Directory does not exist: /home/extra`). Option order matters on 1.0.79. | V3, V4, V6, V7 |
| `trustedFolders` location | `~/.copilot/config.json` (a "managed automatically" file). `settings.json` has no such key. | file inspection |
| `-p` in an untrusted cwd | No trust prompt. Reads work, and writes work with `--allow-all-tools`. `config.json` was not modified (md5 unchanged), so `-p` neither requires pre-trust nor records trust. `COPILOT_ALLOW_ALL=true` made no observable difference for a read. | U1-U3 |
| `--sandbox` | Accepted on 1.0.79 without `--experimental`, although absent from `--help`. Without `bwrap` it warns on stderr (`Sandboxing is enabled but is not supported on this host`), shell commands fail (`GenericFailure`), and the file `view` outside is denied (`Sandbox policy denied filesystem access.`). Exit 0. Containment itself **not testable here**. | S1-S6 |

### Permission patterns

| Claim | Result | Cases |
|---|---|---|
| `read`, `read(.env)`, `memory` accepted | Accepted for both `--allow-tool` and `--deny-tool` (exit 0). Their effect was not tested. An unknown kind (`frobnicate`) is also accepted silently. | K1-K3, K7-K9 |
| Malformed pattern | `--allow-tool='shell(('` and `'bogus('` are rejected at startup: `Invalid --allow-tool value. Error: Invalid rule format: ...`, exit 1. | K5, K6 |
| `write(path)` globs | **Supported, single segment.** `write(src/*.ts)` allows `src/w1.ts`, denies `src/w2.md` and `src/deep/w3.ts` (`*` does not cross `/`). The docs conflict is resolved in favor of globs. Exact `write(src/w4.ts)` allows only that file. `write(.env)` allows `.env` in any directory (`sub/.env`) and denies other names. | W1-W7 |
| `write` vs shell redirection | **Contradicts the docs.** With only `--allow-tool=write`, `bash` `echo hi > file` succeeded. With no flags it is denied (A2). Only `echo >` was tried. | W9, A2 |
| Granular shell allows | `shell(git:*)` runs `git status`/`git log`; `shell(touch)` runs `touch`. Read-only commands (`ls`, `cat` in cwd) need no grant. | G1-G7 |
| Deny beats allow | Confirmed for `--deny-tool` over `--allow-all-tools` and over `--yolo` (`shell(rm)`, `write`, `write(.env)`, `shell`), and over `shell(git:*)` (`shell(git push)`). Files were unchanged. A rule denial reads `Permission to run this tool was denied due to the following rules: ...`. | D1-D6, G3, G4 |
| `--deny-url` beats allows | Confirmed over `--allow-url` and over `--allow-all-urls`, for `web_fetch` (`Permission to access this URL was denied.`) and for `curl` in shell. | R3, R4, R10 |
| URL protocol | `--allow-url=example.com` allows `https://` and denies `http://`. | R2, R7 |
| `--yolo` = three axes | `--yolo` and `--allow-all` behave identically, and equal `--allow-all-tools --allow-all-paths` in the probe. The axes are **not fully independent**: `--allow-all-tools` alone also lets `web_fetch` and `curl` reach the URL (no `--allow-all-urls`). `--allow-all-urls` alone permits `web_fetch` only. `--allow-all-paths` alone permits reads only (shell write and `web_fetch` denied). | X0-X8, R5, R6, R8, R9 |

Axis matrix (bash write in cwd / view outside file / `web_fetch`):

| Flags | bash write | view outside | web_fetch |
|---|---|---|---|
| none | deny | deny | deny |
| tools | ok | deny | ok |
| paths | deny | ok | deny |
| urls | deny | deny | ok |
| tools+paths | ok | ok | ok |
| tools+urls | ok | deny | ok |
| paths+urls | deny | ok | ok |
| `--yolo` / `--allow-all` | ok | ok | ok |

### Non-interactive (`-p`) behavior

| Question | Result | Cases |
|---|---|---|
| Ungranted tool, path, or URL | The tool call returns an error to the model and the run continues. JSON: `tool.execution_complete` with `success:false`, `error.code:"denied"`, message `Permission denied and could not request permission from user`. Text mode prints `✗ <action>` then `└ Permission denied ...` on stdout. **Exit code 0.** | A2, A4, R1, N1-N3 |
| Exit code | Non-zero (1) only for startup validation: unknown flag, unavailable `--model`, missing `--add-dir`, malformed pattern, bad relative `--add-dir`. Denied tool calls do not change it. | V1, V4, K5, K6 + direct runs |
| `--no-ask-user` | No change in outcome. Without it the call is still denied immediately and no `ask_user` call occurred. | N4, N5 |
| Stderr in text mode | A successful run also writes a footer (`Changes`, `AI Credits`, `Tokens`, `Resume`) to stderr, so non-empty stderr is not an error signal. | N1-N3, K1-K10 |
| `--allow-all-tools` required? | **No.** Granular `--allow-tool` flags suffice (`write`, `write(path)`, `shell(git:*)`, `shell(touch)`). | W1-W9, G1-G5 |

## Version differences

Cross-run on 1.0.91 (same fixture, outcomes only):

- `--add-dir <regular file>`: 1.0.91 fails fast (`Not a directory`, exit 1); 1.0.79 proceeds with exit 0.
- `--add-dir ../x -C dir`: 1.0.91 resolves against `-C` and works; 1.0.79 fails (exit 1).
- Sandbox error text differs; the outcome (denied) is the same.
- All other path, pattern, deny, URL, and axis outcomes were identical.

## Conclusion

- In `-p`, access is the cwd tree plus `/tmp`. The git root, `~/.copilot`, and any other path need `--add-dir` or `--allow-all-paths`. Writes additionally need a tool grant. `--allow-all-tools` never opens paths.
- A restricted run works with granular flags and does not need `--allow-all-tools`. Deny rules win over every allow, including `--yolo`.
- **Denied actions do not fail the run.** Exit code is 0, so a caller must read the JSON events (`tool.execution_complete.success`/`error.code`) or check the filesystem to detect denials. Exit 1 only signals startup validation errors.
- The research claims resolved on 1.0.79: git root not granted; `read`/`read(.env)`/`memory` accepted; `write` globs work (single segment); `--sandbox` accepted; `-p` skips the trust prompt and does not need pre-trust; `trustedFolders` lives in `config.json`; `--yolo` = all three axes; ungranted calls are denied tool results with exit 0.
- Doc corrections for the research file: `write` does not stop `echo >` redirects when granted; `--allow-all-tools` also covers `web_fetch`/`curl` without `--allow-all-urls`; `--add-dir` file/relative-order behavior is version-dependent.

## Implication for the ralph invocation

- Run from the worktree root, not a subfolder. Example (all parts verified above): `copilot -p "$PROMPT" --output-format json --no-ask-user --allow-tool=write --allow-tool='shell(git:*)' --deny-tool='shell(git push)' --deny-tool='write(.env)' [--add-dir <existing abs dir>] [--disallow-temp-dir]`.
- Put `--add-dir` after `-C`, and use absolute paths, to behave the same on 1.0.79 and 1.0.91.
- Detect denials from the JSON events, not the exit code.
- Pin the CLI (`--no-auto-update`) when reproducibility matters.

## Not tested

- `COPILOT_HOME` as a path-gate target (an empty `COPILOT_HOME` still authenticated and ran, observed once via the shim on 1.0.91).
- Effect of `read`/`read(.env)`/`memory` rules, `permissions-config.json`, symlink escapes, linked-worktree approvals, repo hooks and workspace MCP in trusted vs untrusted folders.
- Sandbox containment (`bwrap` missing on this host).
- Whether `--allow-tool=write` covers redirects beyond `echo >`.

## Environment pitfalls found

- The `copilot` first on `PATH` in this VS Code terminal is a shim (`copilotCLIShim.js`) that ends with `process.exit(0)`, so **every** invocation exits 0 regardless of the real result. Exit codes here were measured on the native binary.
- After the first run the launcher switched from 1.0.79 to 1.0.91 (session-state `copilotVersion`). Use `--no-auto-update` or `COPILOT_AUTO_UPDATE=false` to stay on the installed version.

## Cleanup

Scratch fixture, harness, npm install of 1.0.79, `/var/tmp/exp21-untrusted`, `/tmp/exp21-*`, and `~/.copilot/exp21-probe*.txt` removed. `~/.copilot/config.json` unchanged. The CLI's own session records for the runs remain under `~/.copilot/session-state/`.
