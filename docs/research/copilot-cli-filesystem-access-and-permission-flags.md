# Copilot CLI: filesystem access and permission flags for non-interactive `-p` runs

Sources (read 2026-10-03). Local binary: `copilot --version` → `GitHub Copilot CLI 1.0.79`. Only read-only help commands were run. Official docs describe a newer CLI (the changelog top entry is 1.0.91), so behavior added after 1.0.79 may be absent locally. Anything not confirmed in a source is marked **UNVERIFIED**.

Source keys used below:

| Key | Source |
|---|---|
| `[help]` | `copilot --help` (local 1.0.79) |
| `[help-perm]` | `copilot help permissions` (local) |
| `[help-cfg]` | `copilot help config` (local) |
| `[help-env]` | `copilot help environment` (local) |
| `[help-sbx]` | `copilot help sandbox` (local) |
| `[cmd-ref]` | https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference |
| `[prog-ref]` | https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference |
| `[config-dir]` | https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference |
| `[configure]` | https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/configure-copilot-cli |
| `[allowing]` | https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools |
| `[about]` | https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli |
| `[run-prog]` | https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically |
| `[changelog]` | https://raw.githubusercontent.com/github/copilot-cli/main/changelog.md |

## Summary

- **Default file access is the cwd tree plus the system temp dir.** Access is checked per path ("path gate"). Everything else needs `--add-dir`, `--allow-all-paths`, or an interactive grant. `[help-perm]` (Path Permissions), `[configure]` (Setting path permissions)
- **The path gate is heuristic, not a security boundary.** Shell-command paths are extracted by tokenizing the command text, and the docs list cases it misses (complex shell constructs, custom env vars, symlinks of files being created). The real boundaries are the experimental OS sandbox (`/sandbox`, `--sandbox`) or a container/VM. `[configure]`, `[about]` (Risk mitigation), `[help-sbx]`
- **Three independent permission axes:** tools (`--allow-tool` / `--deny-tool` / `--allow-all-tools`), paths (`--add-dir` / `--allow-all-paths`), URLs (`--allow-url` / `--deny-url` / `--allow-all-urls`). `--allow-all` and `--yolo` are aliases for all three allow-all flags. `[help]`, `[help-perm]`
- **Deny always beats allow**, including `--allow-all-tools` / `--allow-all` and saved approvals. `--deny-url` beats `--allow-url`. `[help-perm]`, `[allowing]`, `[help]`
- **Pattern syntax is `kind(argument)`.** Kinds are `shell`, `write`, `url`, `<mcp-server-name>`, plus `read` and `memory` in the online docs. `[help-perm]`, `[cmd-ref]` (Tool permission patterns)
- **`-p` does not prompt for permissions.** Changelog 0.0.359: "`copilot -p` will no longer interactively prompt for permission requests". The help text says `--allow-all-tools` is "required for non-interactive mode". The exact outcome of a missing permission (denied tool result vs hard failure, exit code) is not documented. See [Missing permission in `-p` mode](#missing-permission-in--p-mode).

## 1. Where the CLI can read and write by default

| Location | Default access | Notes / source |
|---|---|---|
| cwd and subdirectories | Allowed by the path gate. | `[help-perm]`: "By default, file access is restricted to paths within the current working directory and its subdirectories, plus the system temporary directory." `-C <dir>` changes the cwd before anything else (`[help]`). |
| System temp dir | Allowed automatically. | Disable with `--disallow-temp-dir` (`[help]`, `[help-perm]`). Added to defaults in changelog 0.0.349. |
| Git root above the cwd | **UNVERIFIED** as a file-access grant. | The git root is used as the *location key* for saved approvals and for loading instructions, agents and skills (`[config-dir]` permissions-config, `[cmd-ref]` instruction locations). No source says the path gate extends to it. |
| `~/.copilot` (override: `COPILOT_HOME`) | Written by the CLI itself. **UNVERIFIED** whether the agent's file tools can reach it from a normal cwd. | Layout per `[config-dir]` Directory overview: `settings.json`, `config.json`, `permissions-config.json`, `mcp-config.json`, `session-state/`, `session-store.db`, `logs/`, `agents/`, `skills/`, `hooks/`, `installed-plugins/` and others. `[help-env]` (`COPILOT_HOME`). |
| `~/.copilot/session-state/<session-id>/` | CLI-managed session data: `events.jsonl`, plans, checkpoints, tracked files. | `[config-dir]` (`session-state/`). Used by `--resume` / `--continue`. Agent access to it **UNVERIFIED**. Changelog 1.0.89 says "Agent shell commands in sandbox can access session files and logs", which applies only when sandboxing is enabled. |
| `~/.copilot/logs/` (override: `--log-dir`) | CLI-managed. | `[help]` `--log-dir`, `[config-dir]` (`logs/`). |
| Cache dir (`$XDG_CACHE_HOME/copilot` or `~/.cache/copilot` on Linux; `COPILOT_CACHE_HOME`) | CLI-managed. Not moved by `COPILOT_HOME`. | `[config-dir]` (Changing the location of the configuration directory). |
| Everything else | Needs a grant. | See [Extending access](#2-restricting-and-extending-path-access). |

Related facts:

- **Path gate scope.** It applies to shell commands, file operations (create, edit, view) and search tools (`grep`, glob). `[configure]` (Setting path permissions)
- **Read-only operations.** "Read-only operations like searching, reading files, and running read-only shell commands are allowed automatically." Tools that modify the system, edit files or access URLs need approval. `[allowing]` (Introduction)
- **Config dir contents.** Per-location approvals live in `~/.copilot/permissions-config.json`, and trusted folders / allowed URLs live in the settings files (see [section 2](#2-restricting-and-extending-path-access)). `[config-dir]`
- **Worktrees.** Saved approvals are keyed by the git root, and "linked worktrees resolve to the main repository root, so they share permissions with the main worktree". Submodules use their own working directory. `[config-dir]` (Location keys)

## 2. Restricting and extending path access

### Flags and slash commands

| Mechanism | Effect | Source |
|---|---|---|
| `--add-dir <directory>` (repeatable) | Adds a directory to the allowed list for file access. Also loads that directory's `.github/skills` and `.github/agents` as trusted configuration. | `[help]`, `[cmd-ref]` |
| `--add-dir` validation | The path must be an existing directory. A missing path or regular file fails startup immediately. A relative path resolves against the session cwd (after `-C`), regardless of option order. | `[cmd-ref]`; changelog 1.0.83 (relative-path resolution) and 1.0.85 (uniform fail-fast). Both releases are **newer than the local 1.0.79**, so local behavior is **UNVERIFIED**. |
| `--allow-all-paths` | Disables path verification; any path is accessible. | `[help]`, `[help-perm]` |
| `--disallow-temp-dir` | Removes the automatic temp-dir access. | `[help]` |
| `-C <directory>` | Sets the cwd (and therefore the base of the allowed tree). | `[help]` |
| `/add-dir PATH`, `/list-dirs` | Interactive-only equivalents. | `[cmd-ref]` slash commands |
| `permissions-config.json` → `locations.<key>.allowed_directories` | Persistent extra directories per location. Entries must be absolute, existing directories. | `[config-dir]` (permissions-config schema, Directory matching) |
| `trustedFolders` | "List of folders where permission to read or execute files has been granted." | `[help-cfg]`. Docs place it in `~/.copilot/config.json` (`[configure]` Editing trusted directories). `[help-cfg]` lists it among settings, so the exact file is **UNVERIFIED**. |

### Notes

- **`allowed_directories` only passes the path gate.** It does not approve the tool operation itself. A write inside an allowed directory can still need a `write` approval. `[config-dir]` (Directory matching)
- **Path matching rules.** Symlinks are resolved before comparison. UNC network paths are blocked. Matching is case-insensitive on Windows and case-sensitive elsewhere. `[config-dir]`
- **Saved approvals are not written by CLI flags.** `--allow-tool` / `--deny-tool` are session-only and are not written to `permissions-config.json`. `[allowing]` (Persisted permissions)
- **`permissions-config.json` cannot restrict.** It supports no deny, ask or URL rules. `[config-dir]`
- **Trust prompt.** An interactive session asks whether to trust the launch directory ("this session" or "this and future sessions"). `[configure]` (Setting trusted directories)
- **Trust in `-p` mode.** Whether `-p` skips this prompt or requires the folder to be already trusted is **UNVERIFIED**. What is documented:
  - Repo hooks, workspace MCP servers (`.mcp.json`, `.github/mcp.json`) and project extensions are **not loaded** in `-p` unless opted in. The opt-ins are `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true`, `GITHUB_COPILOT_PROMPT_MODE_WORKSPACE_MCP=true` and `GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS=true`. Repo hooks and workspace MCP also load when the folder is already trusted. `[cmd-ref]` (Environment variables); changelog 1.0.40, 1.0.49
  - `COPILOT_ALLOW_ALL` set to exactly `true` also trusts the working directory. Other truthy spellings (`1`, `yes`, `on`, `y`) only auto-approve tools. `[cmd-ref]` (Environment variables)
- **Managed-policy block.** A policy `permissions.disableBypassPermissionsMode: "disable"` (user settings, managed settings or MDM) suppresses `--allow-all-tools`, `--allow-all-paths`, `--allow-all-urls`, `--allow-all` and `--yolo` at startup. `[cmd-ref]` (Restricting the --allow-all options); `[config-dir]`
- **Real isolation.** The experimental OS sandbox (`/sandbox`, `sandbox.*` in `settings.json`, bubblewrap on Linux) restricts shell commands to cwd, PATH dirs, temp dir and the user profile by default. `userPolicy.filesystem.readwritePaths` / `readonlyPaths` / `deniedPaths` extend or restrict this. Built-in file edits "still follow the same policy on a best-effort basis". `[help-sbx]`
- **`--sandbox` flag.** It is documented as "Useful with `-p`" and "Only available in experimental mode" in `[cmd-ref]`, but it is **not listed in local `copilot --help`**. Treat it as **UNVERIFIED** on 1.0.79.

## 3. Permission patterns and flag syntax

### Value syntax

- Values are repeatable flags or a quoted comma-separated list: `--allow-tool='write, shell(npm:*)'`. `[run-prog]`, `[cmd-ref]` ("For multiple tools, use a quoted, comma-separated list")
- Flags are written `--flag=value` or `--flag value`. `[help]` shows `--allow-tool[=tools...]`.
- Malformed `--allow-tool` / `--deny-tool` patterns are rejected with an error (changelog 1.0.71).
- Always single-quote patterns that contain `*`, `(` or `)` in the shell.

### `--allow-tool` / `--deny-tool` patterns (`kind(argument)`; argument optional)

Sources: `[help-perm]` (Tool Permissions), `[cmd-ref]` (Tool permission patterns), `[prog-ref]` (Tool filters).

| Kind | Pattern | Meaning |
|---|---|---|
| `shell` | `shell` | All shell commands. |
| `shell` | `shell(git)` / `shell(npm test)` | Exact command match. For `git` and `gh`, approval is by first-level subcommand (`shell(git push)`, `shell(gh pr create)`). |
| `shell` | `shell(git:*)` | Prefix match on the command stem. Matches `git push`, `git pull`, not `gitea`. Wildcard matching is on the command stem only. |
| `write` | `write` | All non-shell file creation and modification. |
| `write` | `write(.env)` | Relative path matches by trailing path components, so any `.env` in any directory. |
| `write` | `write(/abs/path/.env)` | Absolute path scopes the rule to one location. |
| `write` | `write(src/*.ts)` | Appears as an example in `[cmd-ref]` / `[prog-ref]`. The same page says "no glob support yet" for `write` paths. **Conflicting docs, so UNVERIFIED.** |
| `read` | `read`, `read(.env)` | Listed in online docs only. **Not listed in local `copilot help permissions` (1.0.79)**, so its presence in 1.0.79 is **UNVERIFIED**. |
| `memory` | `memory` | Storing facts to agent memory. Online docs only. **UNVERIFIED** locally. |
| `url` | `url(github.com)` | HTTPS only; a domain with no protocol defaults to `https://`. |
| `url` | `url(http://localhost:3000)` | Explicit protocol and port. |
| `url` | `url(https://*.github.com)` | Wildcard subdomain, only at the start of the host. |
| `url` | `url(https://docs.github.com/copilot/*)` | Path wildcard, only at the end of the path. |
| MCP server | `MyMCP` | All tools of the MCP server named `MyMCP` (the configured server name, not a sanitized tool-name prefix). |
| MCP server | `MyMCP(create_issue)` | One tool of that server. |

Additional rules:

- `write(...)` rules resolve symlinks and `.`/`..` segments, and are case-insensitive on macOS and Windows. `[cmd-ref]`
- `write` does **not** cover shell redirections. "To allow all shell redirections, set `--allow-all-tools`." `[help-perm]`
- Wildcards are only supported for `shell` (`:*`) and for `url` (leading host label, trailing path). `[prog-ref]` note
- Examples from the help: `--allow-tool='shell(git:*)' --deny-tool='shell(git push)'`, `--allow-tool='write'`, `--deny-tool='MyMCP(denied_tool)' --allow-tool='MyMCP'`. `[help]`

### Tool visibility (separate from permission)

Source: `[help-perm]`, `[allowing]` (Layers of tool controls), `[cmd-ref]` (Tool availability values).

- `--available-tools=...` leaves only the listed tools visible to the model. `--excluded-tools=...` hides only the listed tools.
- If both are given, `--available-tools` is applied and `--excluded-tools` is ignored.
- Allow and deny flags "do not expose tools that were filtered out" by these options.
- Tool names include `bash`, `view`, `create`, `edit`, `apply_patch`, `glob`, `grep`, `web_fetch`, `task`, `ask_user`, `skill`.

### URL permission flags and settings

Source: `[help-perm]` (URL Permissions), `[help]`, `[help-cfg]`, `[configure]`.

- **Defaults.** All URLs need approval by default.
- **Protocol-aware.** Approving `https://x` does **not** allow `http://x`. A pattern with no protocol is `https://`.
- **Flags.** `--allow-url=<url-or-domain>`, `--deny-url=<...>` (deny wins), `--allow-all-urls`.
- **Persistent settings.** `allowedUrls` and `deniedUrls` in `settings.json`: exact URLs, domains, or `*.github.com`. `deniedUrls` wins over allow.
- **Enforcement scope.** The URL gate covers the `web_fetch` tool and "a curated list of shell commands that access the network (such as `curl`, `wget`, and `fetch`)". URLs in file contents, env vars or obfuscated strings are **not detected**. `[configure]`
- **`web_fetch` SSRF protection.** Loopback, RFC-1918 and metadata IPs are blocked independently of URL permissions. `COPILOT_WEB_FETCH_ALLOW_LOCALHOST=1` re-enables localhost. `[cmd-ref]` (Security)

### Allow-all flags

| Flag | Equivalent | Source |
|---|---|---|
| `--allow-all-tools` | All tool approvals. Env `COPILOT_ALLOW_ALL`. Help text: "required for non-interactive mode". | `[help]` |
| `--allow-all-paths` | Disable path verification. | `[help]` |
| `--allow-all-urls` | Disable URL verification. | `[help]` |
| `--allow-all` | `--allow-all-tools --allow-all-paths --allow-all-urls` | `[help]`, `[help-perm]` |
| `--yolo` | Same as `--allow-all`. | `[help]` |

### Precedence

1. **Tool visibility filters first.** `--available-tools` / `--excluded-tools` decide what the model can see at all. `[help-perm]`
2. **Deny beats allow.** `--deny-tool` wins over `--allow-tool`, `--allow-all-tools`, `--allow-all`, and saved approvals in `permissions-config.json`. `[help-perm]`, `[allowing]`, `[about]`
3. **URL deny beats URL allow.** `--deny-url` > `--allow-url` > `--allow-all-urls`, and `deniedUrls` > `allowedUrls`. `[help]`, `[help-cfg]`
4. **Settings cascade.** The documented order (later overrides earlier) is: built-in defaults → MDM managed → user `settings.json` → repo `.github/copilot/settings.json` → local `.github/copilot/settings.local.json` → environment variables → CLI flags. A repo's `deniedUrls` is union-only (repos can add, never remove). `[config-dir]` (Configuration file settings)
5. **Managed rules.** Managed `permissions.deny/ask/allow` use deny > ask > allow, and `allow` is an intersection across sources. `[config-dir]` (Managed permission rules)

## Missing permission in `-p` mode

| Question | What the sources say |
|---|---|
| Does `-p` prompt interactively? | **No.** Changelog 0.0.359: "`copilot -p` will no longer interactively prompt for permission requests". |
| Does `-p` need `--allow-all-tools`? | `[help]` says `--allow-all-tools` is "required for non-interactive mode" and `[cmd-ref]` says "Required when using the CLI programmatically". But `[run-prog]` and `[prog-ref]` show `-p` runs with only granular flags (for example `--allow-tool='shell(git:*)'`, `--allow-tool='write, shell(npm:*)'`) and tell users to "give minimal permissions". `[about]`: "To allow Copilot to modify and execute files you should also use one of the approval options". Read as: ungranted tools are not usable, and granular allows are supported. |
| What happens to an ungranted tool call (denied result fed back to the model, hard error, or run aborts)? | **UNVERIFIED.** No source describes it. The earlier changelog 0.0.340 added "a prompt to approve new paths in `-p` mode", which predates and appears to be superseded by 0.0.359. |
| Exit code on a missing permission? | **UNVERIFIED.** Documented non-zero causes: LLM backend failure in `-p` (0.0.354), prompt "blocked before responding" (1.0.71), failed `--share` / `--share-gist` export (1.0.71), startup failure on a bad `--add-dir` path (`[cmd-ref]`), malformed `--allow-tool` / `--deny-tool` pattern (1.0.71). A prompt-mode run with a child task that fails but whose parent recovers exits `0` (1.0.87, newer than local). A denied tool call is not listed as a failure cause. |
| `ask_user` in `-p` | `--no-ask-user` disables the tool so the agent cannot pause for input. `[help]`, `[prog-ref]`, `[run-prog]` |
| Waiting on background tasks | `-p` waits for background agents/shells up to `COPILOT_TASK_WAIT_TIMEOUT_SECONDS` (default 600; `0` = don't wait). `[cmd-ref]` (Environment variables) |
| `copilot workflow run` (not `-p`) | "The command does not display permission approval prompts"; permissions must be granted up front. `[prog-ref]` |

To pin the real failure mode and exit code, run a throwaway `copilot -p` with a deliberately ungranted write and observe stdout, stderr and `$?`. That was not done here because only read-only help commands were allowed.

## Cross-reference

[sandcastle-agent-invocation-and-extension-points.md](sandcastle-agent-invocation-and-extension-points.md) records that sandcastle's `copilot` provider runs `copilot -p '<prompt>' --output-format json --model M [--allow-all-tools]` (no path/URL flags), so it relies on the default cwd tree plus temp dir.

## Answer

- Default file access is the cwd tree plus the system temp dir; `~/.copilot` is CLI-owned, and session state (`~/.copilot/session-state/`) is written by the CLI, not granted to the agent. Agent access to those is **UNVERIFIED**.
- Extend paths with repeatable `--add-dir <existing dir>`, or `--allow-all-paths` to drop the check. Persistent equivalents: `permissions-config.json` → `allowed_directories`, `trustedFolders`. `--disallow-temp-dir` removes temp access.
- Permissions are three independent axes (tools, paths, URLs). `--allow-all` / `--yolo` = all three allow-all flags. Deny beats allow everywhere, including `--allow-all-tools`; `--deny-url` beats `--allow-url`.
- Patterns are `kind(arg)`: `shell(git:*)`, `shell(git push)`, `write`, `write(path)`, `url(https://*.host)`, `MyMCP`, `MyMCP(tool)`. Quote them; use a repeated flag or a comma list.
- `-p` never prompts. The help says `--allow-all-tools` is required, while the docs' own `-p` examples use only granular `--allow-tool`. What happens for an ungranted tool call (denied tool result vs abort) and its exit code are **UNVERIFIED**. Use `--no-ask-user`.
- The path gate is heuristic. For real containment use the experimental `/sandbox` or a container/VM.

## Minimal restricted `-p` invocation

Run from the worktree root (the cwd tree is the only writable area; no `--allow-all-*` flags). Docs-derived. The ungranted-tool behavior is **UNVERIFIED**.

```bash
cd /path/to/worktree && copilot -p "$PROMPT" -s --no-ask-user --output-format json \
  --allow-tool='write' \
  --allow-tool='shell(git:*)' --allow-tool='shell(pytest:*)' \
  --deny-tool='shell(git push)' --deny-tool='shell(rm)' --deny-tool='write(.env)' \
  --disallow-temp-dir
# Extra writable dir:   --add-dir /abs/existing/dir
# Specific URL access:  --allow-url=github.com   (default is deny/approval-required)
# Fallback if tools are refused in -p (per --help): --allow-all-tools plus the --deny-tool lines
```

For a read-only run, no allow flags are needed (reads and read-only shell commands are auto-allowed): `copilot -p "$PROMPT" -s --no-ask-user`. `[allowing]` (Introduction)
