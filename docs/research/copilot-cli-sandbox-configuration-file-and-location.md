# Copilot CLI sandbox: configuration file, options, and location

Sources read 2026-10-03. Local binary at read time: `copilot --version` → `GitHub Copilot CLI 1.0.91` (the sibling research note was written against 1.0.79). Only read-only help commands and a read of the local `settings.json` `sandbox` key were run. Anything not confirmed in a source is marked **UNVERIFIED**. The sandbox is **experimental** in the CLI.

| Key | Source |
|---|---|
| `[help-sbx]` | `copilot help sandbox` (local 1.0.91) |
| `[help]` | `copilot --help` (local 1.0.91) |
| `[help-env]` | `copilot help environment` (local) |
| `[cfg-dir]` | https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference |
| `[cmd-ref]` | https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference |
| `[cfg-sbx]` | https://docs.github.com/en/copilot/how-tos/cloud-and-local-sandboxes/configuring-local-sandbox-settings |
| `[fs-policy]` | https://docs.github.com/en/copilot/concepts/agents/copilot-cli/understanding-local-sandboxing |
| `[about-sbx]` | https://docs.github.com/en/copilot/concepts/about-cloud-and-local-sandboxes |

## Summary

- **There is no dedicated sandbox config file.** Sandbox settings are the `sandbox` key inside the user `settings.json` (JSONC allowed). `[help-sbx]` (Configuration), `[cfg-sbx]` (Viewing your current sandbox settings)
- **Default location:** `~/.copilot/settings.json`. **Relocate it** by setting `COPILOT_HOME` to the directory that should replace `~/.copilot`. `[cfg-dir]` (settings.json, Changing the location of the configuration directory), `[help-env]`
- **`--config-dir=DIRECTORY` is deprecated** in favor of `COPILOT_HOME`, and is not listed in local `copilot --help`. `[cmd-ref]` (Command-line options), `[help]`. `copilot sandbox ca` explicitly rejects `--config-dir`; use `COPILOT_HOME`. `[cmd-ref]` (Using `copilot sandbox ca`)
- **Repo settings cannot carry `sandbox`.** Repo `.github/copilot/settings.json` (and `settings.local.json`) honor only a fixed key list that does not include `sandbox`; other keys are silently ignored. `[cfg-dir]` (Repository settings). So per-worktree sandbox policy via a committed file is not possible.
- **Only other sources are managed settings** (MDM / server / file `/etc/github-copilot/managed-settings.json` on Linux). They set a floor users cannot relax. `[cfg-dir]` (MDM managed settings)
- **Per-run switch:** `--sandbox` / `--no-sandbox` enable/disable for one session without saving; documented as "Useful with `-p`", experimental only. Not shown in local `copilot --help` (1.0.91). `[cmd-ref]`, `[help-sbx]` (mentions `--sandbox`)
- **No documented flag or env var points at an alternate sandbox policy file**, and none sets individual `sandbox.*` values per run. Per-run policy therefore needs a dedicated `COPILOT_HOME` per run or per policy. **UNVERIFIED** by execution; see [Open questions](#open-questions).

## 1. Options in the `sandbox` key

Merged from `[help-sbx]` (Configuration), `[cfg-dir]` (user settings table), `[cmd-ref]` (Security).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `sandbox.enabled` | boolean | `false` | Turn command sandboxing on. Also `/sandbox enable`/`disable`. |
| `sandbox.addCurrentWorkingDirectory` | boolean | on when enabling | Grant read/write to the cwd (and, in a Git repo, the repo's `.git` read/write plus read of the rest of the repo). UI label "Include working directory". `[cfg-sbx]` |
| `sandbox.allowDevToolAccess` | boolean | `true` | Auto-grant dev-tool locations: read of caches, toolchains, registry config (`~/.npmrc`, `~/.m2/settings.xml`); read/write of shared build caches (go-build, ccache, sccache, Gradle, Cargo registry, `gh` cache). Per-command and per-manifest scoped. `~/.ssh`, `credentials.toml` stay excluded. Also grants read-only on `PATH` dirs and toolchain env vars (`PYTHONPATH`, `VIRTUAL_ENV`, `DOTNET_ROOT`, `JAVA_HOME`, `GOPATH`, `CARGO_HOME`, `NODE_PATH`, ...). `[help-sbx]`, `[cmd-ref]` (Sandbox tool directory grants) |
| `sandbox.allowBypass` | boolean | `true` | Per-command escape hatch (prompt to re-run outside the sandbox). With `false`, a blocked command stops the task. `[cfg-sbx]` (Allowing sandbox bypass) |
| `sandbox.auth.git` / `sandbox.auth.gh` | boolean | `true` | Authenticate sandboxed `git` / `gh` through the local masking proxy. Renamed from `sandbox.gitAuth` / `sandbox.ghAuth` (old keys ignored). |
| `sandbox.credentials.envVars` | map | — | `{ "NAME": { "injectHosts": [...] } }` — mask env credential values, inject only into HTTPS headers for the listed hosts (exact or `*.example.net`). |
| `sandbox.sandboxMcpServers` / `sandbox.sandboxLspServers` | boolean | `true` | Spawn local (stdio) MCP/LSP servers inside the sandbox. Remote MCP servers are never sandboxed. |
| `sandbox.userPolicy.filesystem.readwritePaths` | string[] | `[]` | Extra read/write paths. `[help-sbx]` |
| `sandbox.userPolicy.filesystem.readonlyPaths` | string[] | `[]` | Extra read-only paths. |
| `sandbox.userPolicy.deniedPaths` | string[] | `[]` | Denied paths. `[cfg-dir]` lists it as `sandbox.userPolicy.deniedPaths`, while `[help-sbx]` writes `userPolicy.filesystem.deniedPaths`. **Conflicting docs: exact key UNVERIFIED.** Windows BaseContainer cannot enforce deny rules (policy rejected). |
| `sandbox.userPolicy.network.allowOutbound` | boolean | `true` | Allow outbound network. `[help-sbx]`, `[cfg-sbx]` |
| `sandbox.userPolicy.network.allowLocalNetwork` | boolean | `true` | Allow local network / loopback. |
| `sandbox.userPolicy.network.allowedHosts` / `blockedHosts` | string[] | `[]` | Host allow/deny via strict local proxy on every OS. Exact host, IP, or `*.example.com`; `blockedHosts` wins; non-empty `allowedHosts` blocks everything else. `[cfg-dir]` |
| `sandbox.userPolicy.network.proxy` | object | unset | Upstream HTTP proxy `{ url, username?, password? }`. Password goes to the OS keychain. Cooperative on macOS, enforced on Linux (IPv4 proxy, no embedded creds), unsupported on Windows. |
| `sandbox.userPolicy.seatbelt.keychainAccess` | boolean | `false` | macOS only; config-file-only. |
| `sandbox.failIfUnavailable` | boolean | — | **Managed-only.** Block the session if the sandbox cannot be established. |
| `sandbox.learningMode` | `"allow"`/`"deny"` | — | **Managed, Windows MDM-only.** |

Behavior facts:

- **Path format.** Use absolute paths; adding a directory includes its subtree; **wildcards are not supported**; `~/path` expands when typed in the dialog. A configured path that does not exist is dropped and noted in `/sandbox policy`. `[cfg-sbx]`, `[fs-policy]`
- **Overlap.** The more specific path wins. User-configured read-only/denied rules always stand over automatic grants. `[fs-policy]`
- **Seed policy** on `/sandbox enable`: cwd, `PATH` dirs, temp dir and user profile; outbound allowed; git/`gh` auth via proxy; bypass allowed. `[help-sbx]`
- **Linux backend:** bubblewrap ≥ 0.5.0 on `PATH`; with outbound allowed also `slirp4netns`, util-linux ≥ 2.35 (`unshare`, `nsenter`), `iptables`/`ip6tables` (+ restore), `/dev/net/tun`. Unsupported host: sandbox is turned off with a notice (fails closed under managed enforcement). `[help-sbx]`, `[about-sbx]`
- **Built-in file tools are not OS-sandboxed**; they check the policy in software (best effort). `[fs-policy]`
- **Inspect:** `/sandbox policy` (effective policy per cwd, read-only), `/sandbox status`, `/settings` then search `sandbox`. `Ctrl+E` in the `/sandbox` dialog saves and opens `settings.json`. `[cmd-ref]`, `[cfg-dir]`

## 2. Specifying the config location

| Mechanism | Effect on sandbox config | Source |
|---|---|---|
| `COPILOT_HOME=/dir` | Replaces `~/.copilot` entirely → `settings.json` read from `/dir/settings.json`. Existing config, sessions, plugins and saved permissions are **not** found in the new dir; copy what is needed. Cache dir is unaffected (`COPILOT_CACHE_HOME`). | `[cfg-dir]` (Changing the location), `[help-env]` |
| `--config-dir=DIR` | Deprecated alias; accepted by some subcommands (`copilot instruction`, `copilot lsp`, `permissions-config.json` resolution: `--config-dir` > `COPILOT_HOME` > default). Not in local `--help`. Whether a plain `copilot -p` honors it for `settings.json` is **UNVERIFIED**. | `[cmd-ref]`, `[cfg-dir]` |
| `~/.copilot/settings.json` symlink | Writes from `/settings` follow the symlink to its target. | `[cfg-dir]` |
| Repo `.github/copilot/settings.json`, `settings.local.json` | **Cannot** set `sandbox` (not a supported repo key). | `[cfg-dir]` |
| Managed file `/etc/github-copilot/managed-settings.json` (Linux; must be root-owned, not world-writable, not a symlink) | `sandbox` floor; most-restrictive merge with user settings. | `[cfg-dir]` (MDM managed settings) |
| `--sandbox` / `--no-sandbox` | Per-session on/off only; policy still comes from settings. `--no-sandbox` is ignored under a managed floor. | `[cmd-ref]` |
| `--experimental` | Required for `/sandbox` and `--sandbox`; also settable as `experimental: true` in `settings.json`. | `[cmd-ref]`, `[help-sbx]` |

Settings precedence (later wins): defaults → MDM → user `settings.json` → repo → local → env vars → CLI flags. For `sandbox`, the policy is composed from every source at once in the most restrictive direction, not "last wins". `[cfg-dir]`, `[fs-policy]` (Enterprise-managed policies)

Local observation: `~/.copilot/settings.json` exists here with `experimental: true` and **no** `sandbox` key (sandbox not configured).

## 3. Implication for a Python orchestrator

- Per-worktree policy cannot come from a repo file. Options seen in the sources: (a) write a `settings.json` containing `sandbox` into a dedicated directory per run/policy and launch `copilot` with `COPILOT_HOME` pointing at it, (b) edit the user `settings.json`, or (c) use a managed file. (a) needs auth state (`config.json`/keychain token, plugins, `permissions-config.json`) to be available in that directory, or auth via `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN`. `[cmd-ref]` (`copilot login`). Not verified.
- Paths are absolute and non-wildcard, so the orchestrator must render the file per worktree path.
- `-p` + sandbox: `--sandbox` is documented for `-p`. Whether blocked access under `-p` surfaces as a denied tool result, a bypass prompt (none in `-p`), or an abort is **UNVERIFIED**; see also the open `-p` permission question in [copilot-cli-filesystem-access-and-permission-flags.md](copilot-cli-filesystem-access-and-permission-flags.md).

## Open questions

All need an experiment (candidates for the existing Copilot CLI experiment tickets):

1. Does `copilot -p` honor `COPILOT_HOME` for `settings.json` and the `sandbox` key, and what breaks (auth, plugins) in an empty home?
2. Does `--config-dir` work for `-p` on 1.0.91, or is it ignored?
3. Exact key for denied paths: `sandbox.userPolicy.deniedPaths` vs `sandbox.userPolicy.filesystem.deniedPaths`.
4. Behavior of a sandbox-blocked command and of `sandbox.allowBypass` under `-p` (outcome, exit code).
5. Is `--sandbox` accepted without `--experimental` / with `experimental: true` in settings.
6. Does a Linux host here meet the sandbox prerequisites (`bwrap`, `slirp4netns`, `unshare`, `nsenter`, `iptables`, `/dev/net/tun`)?

## Answer

- **File:** `settings.json`, `sandbox` key; default `~/.copilot/settings.json`; JSONC; no separate sandbox file.
- **Options:** `enabled`, `addCurrentWorkingDirectory`, `allowDevToolAccess`, `allowBypass`, `auth.git/gh`, `credentials.envVars`, `sandboxMcpServers`, `sandboxLspServers`, `userPolicy.filesystem.{readwritePaths,readonlyPaths}`, denied paths, `userPolicy.network.{allowOutbound,allowLocalNetwork,allowedHosts,blockedHosts,proxy}`, `userPolicy.seatbelt.keychainAccess` (macOS); managed-only `failIfUnavailable`, `learningMode`.
- **Location:** only via `COPILOT_HOME` (whole config dir; `--config-dir` is deprecated). Repo-level files cannot hold `sandbox`. Managed file `/etc/github-copilot/managed-settings.json` sets a non-relaxable floor.
- **Per run:** `--sandbox` / `--no-sandbox` toggle only; there is no documented per-run policy file flag.
