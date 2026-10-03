# Experiment: most restrictive Copilot CLI access for an ASP.NET restore, build, and run

Issue: [#22](https://github.com/antshc/shipyard/issues/22). Builds on [copilot-cli-p-mode-permissions.md](copilot-cli-p-mode-permissions.md) (#21), [copilot-cli-filesystem-access-and-permission-flags.md](../research/copilot-cli-filesystem-access-and-permission-flags.md) (#18) and [dotnet-cli-folder-access.md](../research/dotnet-cli-folder-access.md) (#19). Run 2026-10-03.

## Question

With `copilot -p --allow-all-tools --no-ask-user` launched from a harness folder, which flags give the agent access to only `workspace/repo1` (plus the minimum extras) while it restores, builds, and runs an ASP.NET app with several NuGet packages and a shared NuGet cache outside the repo? Which extra paths or commands had to be allowed, and what stayed denied?

## Method

- **CLI:** native `copilot` 1.0.91 (`npm install --prefix /tmp/... @github/copilot@1.0.91`), `--no-auto-update`, model `gpt-5-mini`, `--effort low`, `--no-ask-user`, `--output-format json`. .NET SDK 10.0.112, Linux.
- **Fixture:** [restrict/](restrict/) mirrors the issue layout without any `.git`: `harness/{context.md,docs/}`, `harness/workspace/repo1/{app.sln,src/Web,packages/{Greetings,Clock}}`, and a sibling `harness/workspace/repo2/` canary. "Packages" are two class libraries under `repo1/packages/`, each with an external `PackageReference` (`Humanizer.Core`, `Newtonsoft.Json`), referenced by the ASP.NET `Web` project. `Web` serves `/hello`, calls it once, prints `EXP22-RESULT: ...`, and exits.
- **Shared cache:** `NUGET_PACKAGES=$HOME/exp22-shared/nuget` (outside the harness, emptied before every case) so a successful restore is observable. The env var is inherited by the agent's shell.
- **Driver:** [restrict/run-case.sh](restrict/run-case.sh) runs one case: wipes `bin/obj` and the shared cache, runs `copilot -p`, and prints one line per tool call from the JSON `tool.execution_complete` event (`success`, `error.code`) plus the filesystem afterwards. The model's reply is not evidence.
- **Not tested:** `--sandbox` (`bwrap` is not installed), `--allow-tool=read(...)` rules beyond the case below, `-C` (cwd changed by launching from the directory instead).

## Observations

### Tool grants (cwd = `harness`)

Command under test: `cd workspace/repo1 && dotnet restore app.sln && dotnet build app.sln --no-restore && dotnet run --project src/Web --no-build`, except A5.

| Case | Flags | Result |
|---|---|---|
| A1 | `--allow-all-tools` | ok. Restore wrote the shared cache outside cwd, build and run printed the result. |
| A3 | none | denied |
| A2 | `--allow-tool='shell(dotnet)'` | denied (pattern without `:*` does not match `dotnet restore ...`) |
| A4 | `shell(cd)` + `shell(dotnet)` | denied |
| A4b | `shell(cd:*)` + `shell(dotnet:*)` | ok |
| A5 | `shell(dotnet:*)`, three separate calls (`dotnet restore/build/run <path>` from cwd) | ok |

### Paths (`--allow-all-tools` throughout)

| Case | cwd | Action | Result |
|---|---|---|---|
| B1 | `harness` | `cat` `docs/notes.md`, `workspace/repo2/secret.txt`, `context.md` | all ok: the whole harness tree is in scope |
| B2 | `harness/workspace/repo1` | `cat` the same three files via `../..` | all denied |
| B2b | `repo1` | restore, build, run with the shared cache outside cwd | ok, result printed |
| B3a / B3b | `repo1` | `cat` a file in the shared cache | denied without `--add-dir <cache>`, ok with it |
| B4a | `repo1` | `touch $HOME/exp22-outside.txt` | denied, file not created |
| B4b | `repo1` | `touch <cache>/x.txt` with `--add-dir <cache>` | ok |
| B5 | `repo1` | `dotnet build src/Web -o $HOME/exp22-outdir` | denied (path in the command text), nothing created |
| B6 | `repo1` | `OutDir=$HOME/exp22-env-out/` in the environment, command `dotnet build src/Web` | **ok, `Clock.dll` etc. written outside cwd** |

### Carve-outs inside a wide cwd (cwd = `harness`)

B7: `--deny-tool='read(workspace/repo2/secret.txt)' --deny-tool='write(workspace/repo2/*)'`.

| Call | With deny rules | Control (no rules, B7c) |
|---|---|---|
| file `view` of `repo2/secret.txt` | denied | ok |
| shell `cat repo2/secret.txt` | **ok** | ok |
| file `create` of `repo2/new.txt` | denied | ok |
| shell `echo hi > repo2/new2.txt` | **ok, file created** | ok |

### Network (cwd = `repo1`, empty shared cache)

| Case | Flags | Result |
|---|---|---|
| C1 | `--allow-all-tools --deny-url=api.nuget.org`, restore/build/run | **ok**, packages restored from nuget.org |
| C2 | same flags, `curl https://api.nuget.org/...` | denied |

## Conclusion

- **The path and URL gates check what the agent's tools and the text of a shell command touch. They do not confine child processes.** `dotnet` restores into the shared cache outside cwd (A1, B2b), downloads from nuget.org despite a URL deny (C1), and writes build output outside cwd with no grant (B6), as long as the command text names no outside path. Its writes to `~/.dotnet`, `/tmp` and `~/.local/share/NuGet` were not inspected, but they go through the same ungated process. Only the `--sandbox` flag is a candidate for OS-level confinement (not testable here).
- **Most restrictive working launch: cwd = `workspace/repo1`.** `copilot -p ... --allow-all-tools --no-ask-user` run from `repo1` completes restore, build, and run with the shared NuGet cache outside the tree and **no `--add-dir`**. The harness's `docs/`, `context.md`, and `repo2/` are denied for shell and file tools (B2). Denied: writes and reads outside cwd, and commands whose text names an outside path (B4a, B5).
- **Launching from `harness` cannot be narrowed to `repo1`.** The whole harness tree is in scope (B1). `read(...)`/`write(...)` deny rules block only the file tools; shell `cat` and `echo >` still work (B7). A harness-rooted launch therefore needs `-C`/launch from `repo1`, or an OS sandbox, to reach "only repo1".
- **Extras needed, and only on demand:** `--add-dir <shared nuget cache>` if the agent must read or write that cache itself (B3, B4b). `dotnet restore/build/run` do not need it, because the process is ungated.
- **Without `--allow-all-tools`**, the least that works is `--allow-tool='shell(dotnet:*)'` when each `dotnet` call is a separate command from cwd (A5), or `shell(cd:*)` + `shell(dotnet:*)` for `cd x && dotnet ...` (A4b). `shell(dotnet)` without `:*` does not match any `dotnet` call (A2).
- **URL denial:** `--deny-url` blocks `curl`/`web_fetch` (C2) but not NuGet's own traffic (C1). Restricting restore to a feed or offline needs `NuGet.Config`/`--source`, not a Copilot flag.
- A denied call is a tool result with exit 0 (confirmed, as in #21); every case here exited 0.

## Implication for the ralph invocation

- Start the agent in the repo/worktree directory it should be limited to. Do not rely on `--deny-tool read/write` to hide sibling folders inside the cwd tree.
- Keep the NuGet cache outside the repo via `NUGET_PACKAGES` and do not add it with `--add-dir` unless the agent must inspect it.
- To stop `dotnet` itself from writing outside the tree (`OutDir`, `Directory.Build.props`, `NUGET_PACKAGES`, `~/.dotnet`), a Copilot flag is not enough: use `--sandbox` (needs `bwrap`) or a container, and give it the allow-list from [dotnet-cli-folder-access.md](../research/dotnet-cli-folder-access.md).

## Not tested

- `--sandbox` containment and which paths it needs for `dotnet` (needs `bwrap`).
- Granular `--allow-tool` without `--allow-all-tools` from a `repo1` cwd (A-cases were run from `harness`).
- A local `.nupkg` feed inside `repo1/packages` (packages were project references).
- Behavior on the CLI versions other than 1.0.91.

## Cleanup

Removed: native CLI install, `$HOME/exp22-*` scratch dirs, `bin/obj` build outputs, and `/tmp` case logs. Kept: the fixture [restrict/](restrict/) and [restrict/run-case.sh](restrict/run-case.sh), which expects the CLI at `/tmp/exp22-cli/node_modules/.bin/copilot` (override with `COPILOT`). Copilot's own session records remain under `~/.copilot/session-state/`.
